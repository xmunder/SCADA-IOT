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
- (−) La detección de un gateway caído tarda unos 3 minutos (3 minutos sin latido). Con los datos faltantes tratados solo
  como incumplimiento tardó unos 8.5 minutos en una prueba real, porque CloudWatch amplía la ventana de evaluación en las
  métricas esporádicas; `FILL(m1, 0)` la reduce a ~3 minutos (183 s medidos).
- (−) Verificado en AWS (una sola corrida de cada prueba): el latido llega a CloudWatch cada 15 s, las métricas de estado,
  la alarma de ausencia de latido (pasa a OK con el gateway y a ALARM sin él) y `check_policy` con el tema de estado. En un
  corte real de 5 minutos (`corte-observabilidad`, 501 lotes en cola) la alarma de ausencia de latido saltó a los ~3.5 min
  del corte y volvió a OK 2 min después de reconectar; la alarma de cola grande saltó **después** de reconectar y duró un
  minuto. **Sin verificar**: el aviso por correo.
- (−) **El latido viaja por el mismo enlace que se corta**, así que durante el corte no llega: el panel muestra un hueco, no
  una cola que crece. El tamaño de la cola (469 lotes en el primer latido tras reconectar) solo se ve al volver el enlace.
  Durante el corte la señal útil es la ausencia de latido. Ver la cola crecer en vivo exigiría un canal independiente
  (un registro local con su propia alarma o un segundo enlace); no se hizo.
- (−) La prueba real mostró que `check_policy` publicaba un mensaje inválido que hacía fallar la Lambda de ingesta y disparaba
  la alarma de errores; la Lambda ahora lo cuenta como rechazado.
