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

## Evidencia adicional (segunda ronda)

Cinco corridas más de 5 minutos a 5,000 tags (`cola-directo-r2/`, `cola-sqs-c20-r2/`, `cola-sqs-c10/`, `cola-sqs-c40/`,
`estable-cola-c20/`, `estable-directo/`). Las cuatro primeras, cada una en una tabla nueva.

| Tabla nueva, 5,000 tags | p95 | p99 | Espera de ingesta | Errores Lambda | Lecturas perdidas |
|---|---|---|---|---|---|
| Directo, corrida 1 y 2 | 181 s · 200 s | 402 s · 459 s | 445 s · 904 s | 713 · 853 | 0 · **5** |
| Cola tope 20, corrida 1 y 2 | 104 s · 70 s | 113 s · 88 s | 172 s · 32 s | 0 · 0 | 0 · 0 |
| Cola tope 10 | 69 s | 76 s | 98 s | 0 | 0 |
| Cola tope 40 | 65 s | 83 s | 113 s | 4 | 0 |

- **El modo directo perdió 5 lecturas en la segunda corrida** (cinco tags, una lectura cada uno): IoT Core agotó sus reintentos
  contra una Lambda saturada. Con la cola no se perdió ninguna lectura en ninguna corrida. Es la mejor evidencia a favor de la cola.
- **La variación entre tablas nuevas es grande.** Con el mismo tope 20, la p95 fue de 104 s y de 70 s, y la espera de ingesta, de
  172 s y de 32 s. En modo directo la espera fue de 445 s y de 904 s. Con dos corridas por configuración, la ventaja de la cola en
  p95 (70-104 s frente a 181-200 s) es consistente, pero los números exactos no son reproducibles.
- **Con tope 10, 20 y 40 los resultados son parecidos** (p95 entre 65 y 104 s). El tope 5 era el único claramente insuficiente.
  Subir a 40 no mejoró y añadió errores de la Lambda (4) y una cola más vieja (215 s): el límite lo pone la tabla, no el tope.
- **En estado estable (tabla ya escalada) la cola cuesta latencia:** p50 de 0.6 s y p95 de 4.9 s, frente a 0.11 s y 0.20 s en modo
  directo. Es la ventana de espera del lote (`IngestBatchWindowSeconds`) más la lectura de la cola. La cola compra robustez ante
  picos y fallos, no velocidad.

## Fallos probados en AWS (`loadtest/results/fallos-cola/`)

- **Fallo transitorio de la tabla (tabla inexistente durante ~45 s, 5,900 lecturas):** los mensajes volvieron a la cola, se
  procesaron al restaurar la tabla y no se perdió ninguna lectura.
- **Mensajes a la cola de fallidos:** con la tabla rota, los 4 mensajes llegaron a la cola de fallidos entre 5 y 8 minutos
  (visibilidad de 30 s; con los 180 s de producción tardaría varias veces más). Al restaurar la tabla y reprocesar desde la cola de
  fallidos (`start-message-move-task`), se movieron los 4 mensajes y las 40 lecturas quedaron guardadas.
- El correo de las alarmas (SNS) se verificó con un disparo manual y luego con un corte real del gateway: llegaron el aviso de latido ausente (3 min 50 s después del corte) y el de cola grande tras reconectar.

## Consecuencias

- (+) Sin errores ni limitaciones de la Lambda ante una tabla que aún no escala, y menos latencia de cola y tiempo de drenaje.
- (+) Un mensaje que falla no se pierde: vuelve a la cola, y tras cinco intentos pasa a la cola de fallidos, con alarma.
- (+) El tope de concurrencia protege a DynamoDB, y la cola da dos señales nuevas (edad y profundidad) en el panel.
- (−) Con el tope demasiado bajo, la latencia **empeora**. Hay que dimensionarlo con la tasa de llegada (5 resultó insuficiente a
  5,000 lecturas/s) y vigilar la edad de la cola. No se probaron topes intermedios ni mayores.
- (−) La mediana sube respecto al modo directo y, en estado estable con la tabla ya ajustada, la cola añade la ventana de espera del
  lote (`IngestBatchWindowSeconds`). En estado estable se midió: p95 de 4.9 s frente a 0.2 s en modo directo.
- (−) Una pieza más (cola, cola de fallidos, un rol de IoT para escribir en la cola, un mapeo de la Lambda) y sus costos. Una cuenta que
  no permita crear roles no puede usar el modo `queue` sin adaptar la plantilla.
- (−) **Evidencia limitada:** dos corridas por configuración como máximo (una sola con topes 10 y 40), y el arranque en frío varía
  mucho entre tablas (ver la segunda ronda). Las cifras exactas no son reproducibles; la dirección (la cola reduce la p95 y evita
  la pérdida) se repitió.
- (−) La tabla nueva sigue limitando las escrituras (1.74 millones de eventos limitados con tope 20): la cola amortigua, pero no escala
  la tabla. Calentarla o aprovisionar capacidad no se evaluó.
