# ADR 0001: Almacenamiento y reenvío en el borde

**Estado:** aceptada

## Contexto

El enlace entre la planta y la nube puede caer. Si el gateway solo publicara en
memoria, cada corte causaría pérdida de telemetría, lo que afecta a la fiabilidad
exigida para el análisis histórico.

## Decisión

Cada lote se escribe en un buffer SQLite local (modo WAL, `synchronous=FULL`) antes de
enviarse. Un registro se elimina solo después del PUBACK de AWS IoT Core. Al
reconectar, el reenvío continúa desde el más antiguo, en ventanas de mensajes en
vuelo (por defecto 16): el orden de confirmación no está garantizado.

## Consecuencias

- (+) Un corte de red o un reinicio no pierde datos ya confirmados por el buffer.
- (+) El buffer es el punto de medición de fiabilidad en la prueba de estrés.
- (−) Entrega al menos una vez: pueden aparecer duplicados tras un reinicio entre
  el PUBACK y el borrado. Se mitiga con clave (serie, tiempo) en DynamoDB y
  se mide con `verify_counts`.
- (−) Durante un corte prolongado, el disco crece; conviene vigilar `pending()`.
