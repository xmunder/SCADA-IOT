# ADR 0006: Observabilidad con latido del gateway y alarmas

**Estado:** aceptada

## Contexto

Hasta ahora el prototipo solo emitía métricas desde la Lambda de ingesta. Nada avisaba de un gateway caído, de una cola
local que crecía ni de errores de la Lambda, y la saturación de una tabla nueva (p95 de 144 s) solo se vio al revisar los
resultados a mano. El gateway tampoco puede escribir en CloudWatch: solo tiene credenciales de IoT Core, y darle más
ampliaría su superficie de seguridad.

## Decisión

- El gateway publica un **latido** cada `GATEWAY_STATUS_INTERVAL_S` (15 s) en `plant/<sitio>/status`, por el mismo canal
  MQTT: cola pendiente, contadores y estado de las conexiones OPC UA y MQTT. Es de mejor esfuerzo y no pasa por el buffer,
  porque un estado atrasado no sirve.
- Una **Lambda de estado**, separada de la de ingesta y sin permisos sobre DynamoDB, lo convierte en métricas EMF con la
  hora de la Lambda.
- Siete **alarmas** de CloudWatch y un **panel**. La alarma de ausencia de latido trata los datos faltantes como
  incumplimiento. El aviso por correo (SNS) solo se crea si se da `AlarmEmail` al desplegar, para no versionar el correo.
- La política de IoT permite publicar en el tema de estado y se **versiona** (`ensure_policy` crea una nueva versión
  predeterminada), de modo que un gateway ya aprovisionado la recibe sin cambiar de certificado.

## Consecuencias

- (+) Un gateway caído, una sesión OPC UA perdida o una cola creciente se detectan en minutos.
- (+) El gateway no necesita credenciales de CloudWatch.
- (+) Es la base para medir el amortiguador SQS de la fase siguiente (edad y profundidad de la cola).
- (−) Detecta la caída del gateway, no la remedia: sigue sin haber redundancia (R1).
- (−) Una Lambda y una regla más, y el costo de las alarmas y del panel. El latido añade unos 5,760 mensajes al día.
- (−) Las alarmas, el panel y el latido no se han probado desplegados en AWS; faltan esas comprobaciones.
- (−) El latido a 15 s y las alarmas de tres minutos fijan una detección de unos tres a cuatro minutos.
