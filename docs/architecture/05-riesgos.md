# Registro de riesgos

Probabilidad e impacto son **estimaciones cualitativas del autor**, no cálculos. Estado: **Abierto**, **Mitigado**,
**Parcial**, **Materializado** (ya ocurrió) o **Aceptado**.

| ID | Riesgo | Prob. | Impacto | Mitigación y estado |
|---|---|---|---|---|
| R1 | El gateway es un punto único de falla: si el proceso cae no se recoge ninguna lectura | Media | Alto | El buffer en disco protege los datos ya recogidos, no la recolección. Sin redundancia. La ausencia de latido dispara una alarma: detecta la caída, no la remedia. **Abierto** |
| R2 | Una tabla DynamoDB nueva sostiene ~4,000 escrituras/s al principio: una carga mayor se retrasa (p95 de 144 s observado) hasta que la capacidad se ajusta (~7 min de presión sostenida) | Alta | Medio | La clave idempotente evita pérdida. La cola SQS (`IngestMode=queue`, tope 20) bajó la p95 de 181 a 104 s y eliminó los errores y las limitaciones de la Lambda; el retraso sigue, porque la tabla tarda en escalar. Calentar la tabla o aprovisionar capacidad no se evaluó. **Parcial** |
| R3 | Un desfase de reloj falsea las latencias (con el reloj 180 ms adelantado, p95 de 0 a 8 ms) | Alta | Medio | Medición SNTP por corrida, compensación y aborto si no se puede medir ([ADR 0004](../adr/0004-metodo-de-medicion.md)). **Mitigado** |
| R4 | El entorno de ejecución cambia o desaparece: la cuenta solo permite una región y su plan gratuito y sus créditos caducan el 16 de mayo de 2027 | Media | Alto | Infraestructura como código portable y resultados versionados ([ADR 0005](../adr/0005-region-de-despliegue.md)). **Mitigado** |
| R5 | Costos no controlados en una cuenta con créditos | Media | Medio | Escrituras de la matriz estimadas en ~3.5 USD. Se recomendó una alerta de presupuesto; no se verificó que exista. **Parcial** |
| R6 | Exposición de la clave privada del dispositivo | Baja | Alto | `certs/` y `.env` están fuera de Git y se escaneó el repositorio. No hay rotación de certificados. **Parcial** |
| R7 | El transporte OPC UA no está cifrado (simulador) | Alta | Alto | Aceptable solo en laboratorio. Una planta real exige OPC UA seguro. **Abierto** |
| R8 | Dependencia de AWS (IoT Core, Lambda, DynamoDB) | Media | Medio | La ingesta está aislada en una Lambda y el gateway no conoce DynamoDB ([ADR 0003](../adr/0003-lambda-como-puente-a-dynamodb.md)). **Aceptado** |
| R9 | DynamoDB no es una base de series de tiempo: sin consultas por ventana ni retención por capas | Alta | Medio | Documentado; alternativas en trabajo futuro (TimescaleDB, Timestream for InfluxDB). **Aceptado** |
| R10 | En un corte prolongado el disco del gateway crece sin límite | Baja | Medio | La cola se publica en el latido y hay una alarma por lotes pendientes, que salta al reconectar (durante un corte el latido no sale); sigue sin haber un tope de disco. **Parcial** |
| R11 | Evidencia con pocas repeticiones (3 corridas de 5 min, 3 cortes, una corrida por intervalo de vaciado) y un solo sitio | Alta | Medio | Declarado en `loadtest/results/README.md`. **Abierto** |
| R12 | El servidor OPC UA deja de estar disponible mientras el gateway corre | Media | Medio | El gateway reintenta con retroceso exponencial y retoma la suscripción; se detecta la caída leyendo el estado del servidor cada 2 s. Probado con el simulador (278 lecturas en 20 s tras reiniciarlo). **Mitigado** |
| R13 | Las cuotas de AWS cambian (p. ej. 100 publicaciones/s por conexión en IoT Core) | Baja | Medio | El lote de 100 lecturas mantiene 50 mensajes/s a 5,000 tags. **Aceptado** |
