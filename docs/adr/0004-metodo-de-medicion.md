# ADR 0004: Método de medición de las pruebas de estrés

**Estado:** aceptada

## Contexto

La evaluación del prototipo mide rendimiento, escalabilidad y fiabilidad con latencia p95, pérdida de
lecturas y tiempo de recuperación. Las mediciones deben ser reproducibles y de bajo costo.

## Decisión

- **Latencia de ingesta:** la Lambda guarda `ingest_latency_ms` (hora de escritura menos
  `ts_ms` de origen) en cada item. `loadtest.latency_report` la lee con un Query por tag y
  calcula percentiles por rango más cercano. Como depende de dos relojes,
  `loadtest.clock_check` estima el desfase con SNTP y `run_matrix` se detiene si supera 50 ms.
- **Pérdida:** lecturas enviadas por tag (guardadas en `summary.json`) menos lecturas
  almacenadas, con un Query por tag. La clave (serie, tiempo) elimina duplicados.
- **Escalabilidad:** `run_matrix` ejecuta niveles de tags por repeticiones y agrega media y
  desviación estándar muestral por nivel.
- **Corte de enlace:** `outage_test` bloquea el puerto de salida 8883 con el firewall de Windows,
  muestrea el buffer SQLite y elimina la regla al terminar (incluso ante errores). Se probaron y
  descartaron dos cortes desde la nube: retirar la política y desactivar el certificado en IoT
  Core no cierran una conexión MQTT ya establecida (el gateway siguió publicando).
- **Robustez frente a relojes desfasados:** (1) la Lambda guarda la latencia **cruda**, sin recortar a 0
  (la métrica EMF de CloudWatch sí se recorta); (2) los scripts miden el desfase por SNTP antes y después de cada
  corrida y lo restan (`latencia real = medida - (referencia - equipo)`), y registran desfase, deriva e
  incertidumbre; (3) se aborta si el desfase supera 250 ms o no se puede medir; (4) `loadtest/sync_clock.ps1`
  reduce la deriva del equipo.
- **Observabilidad en la nube:** métricas EMF desde la Lambda, sin permisos adicionales.

## Consecuencias

- (+) Las mediciones se repiten con un comando y dejan CSV y JSON versionables.
- (+) El corte es reversible: el script elimina la regla de firewall incluso ante errores.
- (−) Requiere una consola de administrador y afecta a todo el tráfico saliente por el puerto
  8883 del equipo durante la prueba.
- (−) La corrección tiene la incertidumbre del SNTP (decenas de ms): las latencias de ese orden o menores
  no deben interpretarse en valor absoluto.
- (−) El desfase se estima con SNTP (incertidumbre de decenas de ms). Un primer intento con el
  encabezado HTTP `Date` (resolución de 1 s) dio +0,18 s cuando el desfase real era −0,18 s, y la
  Lambda recortaba a 0 las latencias negativas, con lo que las latencias bajas se perdían.
- (−) El corte es total; no reproduce una pérdida parcial de paquetes.
- (−) Con carga alta la ingesta se retrasa (límites de escritura de DynamoDB y concurrencia de la
  Lambda). `run_matrix` espera a que lleguen los items antes de contar la pérdida, y registra
  esa espera en `ingest_wait_s`.
- (−) La latencia medida con `mqtt_load` excluye la espera del lote en el borde, que sí aparece
  con el gateway real; ambas mediciones deben reportarse por separado.
- (−) Una corrida de 5,000 tags durante 30 minutos escribe unos 9 millones de items; la
  duración de la matriz se ajusta al presupuesto disponible.
