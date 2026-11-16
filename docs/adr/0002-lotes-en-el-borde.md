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

Con 200 tags, el gateway real y los valores por defecto (lotes de hasta 100 lecturas o 1 s), la
latencia de ingesta fue de 0,60 s (p50) y 1,67 s (p95), corregida por el desfase de reloj, frente a
0,11 s (p50) y 0,17 s (p95) medidas con el generador de carga, que no agrupa en el borde. La
diferencia corresponde a la espera del lote y al intervalo de publicación de OPC UA (500 ms).
Reducir `GATEWAY_FLUSH_INTERVAL_S` baja la latencia a cambio de más mensajes e invocaciones; ese
compromiso queda por cuantificar.
