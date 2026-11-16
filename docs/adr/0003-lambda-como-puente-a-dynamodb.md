# ADR 0003: Lambda como puente entre IoT Core y DynamoDB

**Estado:** aceptada (reemplaza la decisión inicial de usar Timestream)

## Contexto

Una regla de IoT Core puede escribir directamente en DynamoDB, pero queremos validar
cada lectura, medir la latencia de ingesta y registrar las lecturas rechazadas, lo que
una regla SQL no permite.

La versión inicial usaba Amazon Timestream (LiveAnalytics). Se descartó por dos motivos:

- AWS cerró el servicio a clientes nuevos el 20 de junio de 2025 (las cargas existentes
  siguen funcionando) y recomienda Timestream for InfluxDB como sustituto.
- En la cuenta usada para las pruebas, Timestream for LiveAnalytics está bloqueado por una política
  de organización y Timestream for InfluxDB exige, según la consola, un plan de pago.

Las alternativas eran TimescaleDB autogestionado en EC2, PostgreSQL en RDS y S3 con Athena
(análisis, no consulta casi en tiempo real); no se evaluaron.

## Decisión

La regla invoca una función Lambda (Python 3.12) que valida cada lectura, descarta las
que no son `GOOD` y escribe un item por lectura en DynamoDB con clave de partición
`series` (`site#tag`) y clave de orden `ts_ms`. El item incluye `value` e
`ingest_latency_ms`. La infraestructura se despliega con AWS SAM (`infra/template.yaml`).

Por defecto SAM crea para la Lambda un rol de mínimo privilegio, con escritura solo en la tabla.
El parámetro `ExecutionRoleName` permite reutilizar un rol existente en cuentas que no permiten
crear roles.

## Consecuencias

- (+) La lógica de validación y medición está en código y tiene pruebas unitarias.
- (+) La clave (serie, tiempo) hace idempotentes los reenvíos: no hay duplicados.
- (+) Facturación por solicitud, sin servicio en mantenimiento.
- (−) Cada invocación añade latencia y costo. Esto se mide en la prueba de carga y
  se reporta como límite de escalabilidad.
- (−) DynamoDB no ofrece consultas de series de tiempo con SQL ni retención automática
  por almacenamiento en capas; la retención requeriría configurar TTL.
- (−) Un rol existente indicado con `ExecutionRoleName` puede ser más permisivo que el mínimo necesario.
- (−) Una tabla nueva solo sostiene ~4,000 escrituras por segundo: la primera corrida a 5,000 tags tuvo una
  latencia de ingesta p95 de 144 s.
- (+) Cambiar de almacenamiento afecta a la Lambda, a la plantilla y a los scripts de
  verificación, no al gateway.

Fuente: [aviso de disponibilidad de Timestream for LiveAnalytics](https://docs.aws.amazon.com/timestream/latest/developerguide/AmazonTimestreamForLiveAnalytics-availability-change.html).
