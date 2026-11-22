# ADR 0007: Cola SQS entre IoT Core y la Lambda de ingesta

**Estado:** aceptada, como modo opcional (`IngestMode=queue`); el modo `direct` sigue siendo el predeterminado

## Contexto

La regla de IoT invocaba la Lambda directamente. Con una tabla DynamoDB recién creada (~4,000 escrituras/s al principio), una
carga de 5,000 lecturas/s saturaba la tabla: la primera corrida tuvo p95 de 144 s, y la Lambda acumuló limitaciones por concurrencia y
errores mientras IoT Core reintentaba. Nada amortiguaba el pico, y un fallo de la Lambda dependía de los reintentos de IoT Core.

## Decisión

Un modo opcional en el que la regla escribe cada mensaje en una cola SQS y la Lambda la lee en lotes (`IngestMode=queue`):

- Cola estándar (no FIFO: la clave idempotente hace innecesario el orden) con una cola de mensajes fallidos a los cinco intentos,
  tiempo de visibilidad de 180 s (seis veces el de la Lambda) y alarmas por la edad del mensaje más antiguo y por mensajes fallidos.
- La Lambda lee hasta `IngestBatchSize` mensajes (10, hasta 1,000 lecturas) y los escribe en una sola operación. Responde con
  fallos parciales: solo vuelven a la cola los mensajes con lecturas válidas cuya escritura falló. Un cuerpo inválido no se reintenta.
- `IngestMaxConcurrency` limita cuántas copias de la Lambda leen a la vez, y con ello la velocidad de escritura a DynamoDB.
- SQS en lugar de Kinesis: no se necesita orden por dispositivo, relectura ni varios consumidores, y SQS no exige dimensionar fragmentos.
- El modo `direct` se conserva para poder comparar y volver atrás sin perder datos.

## Evidencia

Una corrida de 5 minutos con 5,000 tags contra una tabla DynamoDB nueva, para cada configuración (`loadtest/results/cola-directo/`,
`cola-sqs/`, `cola-sqs-c20/`):

| | Directo | Cola, tope 5 | Cola, tope 20 |
|---|---|---|---|
| Latencia p50 | 22 s | 255 s | 41 s |
| Latencia p95 | 181 s | 482 s | **104 s** |
| Latencia p99 | 402 s | 521 s | **113 s** |
| Espera hasta que termina la ingesta | 445 s | 573 s | **172 s** |
| Errores de la Lambda | 713 | **0** | **0** |
| Limitaciones de la Lambda | 6,778 | **0** | **0** |
| Escrituras limitadas de DynamoDB | 5.58 millones | 0 | 1.74 millones |
| Mensajes en cola (máximo) y edad | no aplica | 9,028 · 515 s | 3,407 · 104 s |
| Cola de fallidos | no aplica | 0 | 0 |
| Lecturas perdidas | 0 | 0 | 0 |

- La cola elimina los errores y las limitaciones de la Lambda: el pico se convierte en espera ordenada, no en reintentos.
- **El tope de concurrencia decide el resultado.** Con 5, el consumo (estimado en unas 1,700 escrituras/s: 1.5 millones de lecturas en unos 870 s) quedó por debajo de la
  llegada (5,000/s): la cola creció durante toda la carga y la latencia fue peor que sin cola. Con 20 se acercó al límite de la tabla
  y la cola se vació antes: la latencia de los mensajes más lentos (p95, p99) y el tiempo de drenaje mejoraron mucho.
- La mediana con tope 20 (41 s) es peor que la del modo directo (22 s): la cola ordena la espera y el reparto es más parejo, pero
  los mensajes que en el modo directo pasaban primero ahora esperan su turno.

## Consecuencias

- (+) Sin errores ni limitaciones de la Lambda ante una tabla que aún no escala, y menos latencia de cola y tiempo de drenaje.
- (+) Un mensaje que falla no se pierde: vuelve a la cola, y tras cinco intentos pasa a la cola de fallidos, con alarma.
- (+) El tope de concurrencia protege a DynamoDB, y la cola da dos señales nuevas (edad y profundidad) en el panel.
- (−) Con el tope demasiado bajo, la latencia **empeora**. Hay que dimensionarlo con la tasa de llegada (5 resultó insuficiente a
  5,000 lecturas/s) y vigilar la edad de la cola. No se probaron topes intermedios ni mayores.
- (−) La mediana sube respecto al modo directo y, en estado estable con la tabla ya ajustada, la cola añade la ventana de espera del
  lote (`IngestBatchWindowSeconds`). Esa latencia en estado estable no se midió con la cola.
- (−) Una pieza más (cola, cola de fallidos, un rol de IoT para escribir en la cola, un mapeo de la Lambda) y sus costos. Una cuenta que
  no permita crear roles no puede usar el modo `queue` sin adaptar la plantilla.
- (−) **Evidencia limitada:** una corrida de cada configuración, cada una en una tabla nueva. No se midió cuánto varía el
  arranque en frío entre tablas, así que las diferencias entre modos podrían deberse en parte a esa variación. La ruta de la cola de
  fallidos y de los fallos parciales solo se probó con pruebas unitarias, no en AWS.
- (−) La tabla nueva sigue limitando las escrituras (1.74 millones de eventos limitados con tope 20): la cola amortigua, pero no escala
  la tabla. Calentarla o aprovisionar capacidad no se evaluó.
