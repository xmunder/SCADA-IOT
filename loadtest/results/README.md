# Resultados de las pruebas

Evidencia de las pruebas, obtenida en una única cuenta de AWS (us-east-2). Cada carpeta lleva su `summary.json`,
`runs.csv`, `outage.json` o `report.json` según la prueba.

| Carpeta | Qué es | Notas |
|---|---|---|
| `matriz-us-east-2-v2/` | Matriz de carga: 200, 1,000 y 5,000 tags × 3 repeticiones de 5 min | El desfase de reloj se midió por SNTP antes y después de cada corrida (`clock_offset_ms`, `clock_drift_ms`, `clock_uncertainty_ms` en `runs.csv`) y se restó de las latencias. La primera corrida de 5,000 tags es la de tabla fría. |
| `gateway-200-us-east-2/` | Cadena completa con el gateway real, 200 tags, 4 min | Incluye la espera del lote en el borde. Pérdida estimada por huecos de continuidad en los tags analógicos. |
| `corte-5min-us-east-2/`, `-r2/`, `-r3/` | Tres cortes de enlace de 5 min (firewall, puerto 8883) | Continuidad verificada en 134 tags analógicos durante la ventana completa de cada corte (`continuity.json`): 0 lecturas faltantes. Recuperación de 7.1, 16.2 y 17.3 s: depende de cuándo cae el siguiente reintento del cliente (retroceso de hasta 30 s); el vaciado del buffer tardó 5 a 6 s en los tres. |
| `cold-table-test/` | Escrituras directas a una tabla DynamoDB nueva, 5,000 items/s, 2 fases de 150 s | Prueba previa y breve: aísla el límite de una tabla fría sin IoT Core ni Lambda. Queda superada por `cold-table-long/`. |
| `cold-table-long/` | Lo mismo con 8 fases de 150 s (20 min) | La capacidad pasa de ~4,000 a ~4,500 y a 5,000 escrituras/s en unos 7 minutos de presión sostenida. Rechazo por fase: 17.9, 9.5, 4.9 y 0 % desde la fase 4. |
| `flush-sweep/` | Barrido del intervalo de vaciado del gateway real (0.1, 0.25, 0.5, 1 y 2 s), 200 tags, 3 min por valor | Una sola corrida por valor: las diferencias de mediana menores a ~100 ms no son significativas. Ver el ADR 0002. |
| `gateway-1000/` | Gateway real con 1,000 tags, 4 min | Latencia p95 menor que con 200 tags porque los lotes se llenan por tamaño. |
| `observabilidad-fase1/` | Gateway real y simulador contra AWS, latido, métricas y alarmas | `latido-y-alarmas.txt`: primera prueba (la alarma de errores salta por un mensaje inválido de `check_policy`, ya corregido; la de ausencia de latido tardó ~8.5 min con datos faltantes). `alarma-de-latido.txt`: tras evaluar con `FILL(m1, 0)`, salta 183 s después de detener el gateway. `corte-real-panel.txt`: historial de alarmas y métricas por minuto durante un corte real (ver `corte-observabilidad/`). Una sola corrida de cada una. |
| `corte-observabilidad/` | Corte de enlace de 5 min con el gateway publicando latidos (firewall, puerto 8883) | 501 lotes en cola al máximo y recuperación de 18.2 s, la más larga de las cuatro (7.1, 16.2, 17.3 y 18.2 s): depende del reintento del cliente MQTT, no de vaciar el disco. Durante el corte no llegan latidos; el primer latido tras reconectar informó 469 lotes. |
| `cola-directo/` | 5,000 tags contra una tabla DynamoDB nueva, `IngestMode=direct`, 1 corrida de 5 min | p95 de 181 s, p99 de 402 s; 713 errores y 6,778 limitaciones de la Lambda; 5.58 millones de escrituras limitadas de DynamoDB; 0 pérdida. |
| `cola-sqs/` | Lo mismo con `IngestMode=queue` y `IngestMaxConcurrency=5` | **Peor**: p95 de 482 s. 0 errores y 0 limitaciones, pero el tope era menor que la tasa de llegada y la cola llegó a 9,028 mensajes (edad máxima 515 s). 0 pérdida. |
| `cola-sqs-c20/` | Lo mismo con `IngestMaxConcurrency=20` | p95 de 104 s, p99 de 113 s, espera de ingesta de 172 s (frente a 445 s); 0 errores y 0 limitaciones de la Lambda; 1.7 millones de escrituras limitadas de DynamoDB; cola máxima de 3,407 mensajes (edad 104 s); cola de fallidos vacía; 0 pérdida. La mediana (41 s) es peor que la del modo directo (22 s). Una sola corrida de cada una: la variación entre tablas nuevas no se midió. |

## Corridas excluidas del repositorio (en `.gitignore`)

- `corte-40s/`, `corte-60s/`: pruebas de un método de corte que no cortaba (quitar la política y desactivar el
  certificado en IoT Core no cierran una conexión MQTT ya abierta).
- `matriz-us-east-2/`: detenida; las latencias bajas quedaron recortadas a 0 por un reloj adelantado 180 ms.
- `validacion-reloj/`: validación breve de la corrección de reloj.

## Cómo leer una latencia

La latencia de ingesta es `hora de la Lambda - ts_ms de origen`, corregida con `latencia real = medida - (referencia - equipo)`.
La corrección tiene una incertidumbre de unos 40 ms: las latencias de ese orden no deben leerse en valor absoluto.
Ver `docs/adr/0004-metodo-de-medicion.md`.
