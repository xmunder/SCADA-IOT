# ADR 0002: Agrupar lecturas en lotes en el borde

**Estado:** aceptada

## Contexto

Publicar un mensaje por cada cambio de tag produciría una cantidad de mensajes MQTT
y de invocaciones de Lambda proporcional al número de tags. En la prueba de carga
con miles de tags, ese costo y la latencia de invocación se vuelven el cuello de botella.

## Decisión

Agrupar hasta `GATEWAY_BATCH_SIZE` lecturas (100 por defecto) o las lecturas de
`GATEWAY_FLUSH_INTERVAL_S` (1 s). Cada lote es un mensaje JSON con el sitio y una lista de lecturas.

## Consecuencias

- (+) Reduce el número de mensajes y de invocaciones en un factor cercano al tamaño del lote.
- (+) La prueba de carga permite comparar tamaños de lote con datos reales.
- (−) Una lectura puede esperar hasta el intervalo de vaciado antes de llegar a la nube.
  El intervalo es parte de la latencia que se reporta.

## Evidencia

Con el gateway real, 200 tags y los valores por defecto (lotes de hasta 100 lecturas o 1 s), la latencia de
ingesta fue de 0,60 a 0,70 s (p50) y de 1,67 a 1,73 s (p95), corregida por el desfase de reloj, frente a 0,11 s (p50) y
0,17 s (p95) medidas con el generador de carga, que no agrupa en el borde. La diferencia corresponde a la espera del
lote y al intervalo de publicación de OPC UA (500 ms).

Un barrido de `GATEWAY_FLUSH_INTERVAL_S` con 200 tags (una corrida de 3 min por valor; ver `loadtest/flush_sweep.py` y
`loadtest/results/flush-sweep/`) dio:

| Vaciado | Mensajes/s | Latencia p50 | Latencia p95 |
|---|---|---|---|
| 0,1 s | 1,95 | 502 ms | 808 ms |
| 0,25 s | 1,96 | 608 ms | 896 ms |
| 0,5 s | 1,96 | 594 ms | 1.146 ms |
| 1,0 s | 1,64 | 702 ms | 1.732 ms |
| 2,0 s | 1,35 | 652 ms | 1.753 ms |

- Por debajo de 0,5 s el número de mensajes no cambia: OPC UA entrega las lecturas en ráfagas cada 500 ms y el
  gateway emite un lote por ráfaga.
- La latencia p95 crece con el intervalo hasta ~1 s y después se estabiliza: a 2 s el lote de 100 lecturas se llena
  antes de que venza el tiempo (136 lecturas/s entre 100 son 1,35 mensajes/s) y el tamaño pasa a ser el límite.
- La mediana no es monótona: con una sola corrida por valor y ±40 ms de incertidumbre de reloj, las diferencias de
  mediana menores a ~100 ms no son significativas.
- Con 1,000 tags (620 lecturas/s) los lotes se llenan por tamaño: 6,45 mensajes/s, p50 de 0,49 s y p95 de 1,29 s,
  menos latencia que con 200 tags. Sin agrupar serían ~620 mensajes/s.

Casi todo el ahorro de mensajes proviene de agrupar (de ~136 a menos de 2 mensajes/s a 200 tags), no de afinar el
intervalo entre 0,1 y 2 s.
