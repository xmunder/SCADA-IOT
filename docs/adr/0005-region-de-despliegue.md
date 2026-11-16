# ADR 0005: Región de despliegue y portabilidad de la plantilla

**Estado:** aceptada

## Contexto

La cuenta de AWS usada para las pruebas (plan gratuito con créditos) tiene una política de organización que bloquea
todas las regiones salvo us-east-2: CloudFormation, IoT Core, Lambda y DynamoDB respondían solo allí. Timestream
LiveAnalytics también está bloqueado, y Timestream for InfluxDB exige, según la consola, un plan de pago.

## Decisión

- La plantilla SAM es portable entre cuentas: el rol de la Lambda es opcional (`ExecutionRoleName` vacío crea uno de
  mínimo privilegio; con un valor se reutiliza un rol existente) y la región se indica al desplegar
  (`infra/deploy.ps1` usa us-east-2 por defecto).
- Todos los resultados se obtienen en us-east-2.

## Consecuencias

- (+) El pipeline se reproduce en cualquier cuenta que tenga IoT Core, Lambda y DynamoDB con un único despliegue.
- (−) Solo se evaluó una región; la disponibilidad entre regiones queda sin medir.
- (−) La opción de reutilizar un rol existente (`ExecutionRoleName`) no se ha probado desde que el rol es condicional.
- (−) El plan gratuito de la cuenta y sus créditos caducan el 16 de mayo de 2027.
