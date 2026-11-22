# Escenarios de calidad

Un escenario por cada uno de los once atributos de calidad considerados, con el formato de Bass, Clements y
Kazman (estímulo, respuesta y medida). **Los objetivos se fijaron al redactar este documento; no eran requisitos previos de un cliente.**

Estado de la evidencia: **Medido** (hay resultados experimentales), **Observado** (hecho verificable en el desarrollo,
sin experimento dedicado), **Parcial** y **Argumentado** (se apoya en el diseño, no en una medición).

| Atributo | Escenario (estímulo, respuesta, medida) | Resultado y evidencia | Estado |
|---|---|---|---|
| Rendimiento | 1,000 lecturas/s sostenidas: la lectura llega a la base con latencia de ingesta p95 ≤ 0.25 s (sin lote en el borde) | p95 de 0.17 s (3 corridas de 5 min). Con el gateway real, p95 de 1.73 s a 200 tags y de 1.29 s a 1,000 tags por la espera del lote. `loadtest/results/matriz-us-east-2-v2/`, `gateway-200-us-east-2/`, `gateway-1000/`, `flush-sweep/` | Medido |
| Escalabilidad | Subir de 200 a 5,000 tags: sin pérdida y con p95 ≤ 0.25 s | Sin pérdida en 9 corridas; p95 normal con la tabla ya ajustada. Una tabla nueva sostiene ~4,000 escrituras/s al principio y tarda ~7 min de presión sostenida en ajustarse a 5,000: la primera corrida a 5,000 tags tuvo p95 de 144 s. Con una tabla nueva y la cola SQS (tope 20), p95 de 104 s frente a 181 s en modo directo, sin errores de la Lambda (una corrida de cada una). `cold-table-long/`, `cold-table-test/`, `cola-directo/`, `cola-sqs-c20/` | Medido |
| Fiabilidad | Corte de enlace de 5 min: ninguna lectura perdida y recuperación en menos de 30 s | Tres cortes: ~490 lotes acumulados; recuperación de 7.1 a 17.3 s (media 13.5 s); 0 faltantes en 134 tags en cada uno. `corte-5min-us-east-2/`, `-r2/`, `-r3/` | Medido |
| Seguridad | Un dispositivo intenta publicar en el tema de otro sitio: la nube lo rechaza | Rechazado, igual que conectar con un cliente ajeno. `infra/check_policy.py` | Medido |
| Disponibilidad | Cae el proceso del gateway: el servicio se restablece automáticamente | **Parcial**: si cae el servidor OPC UA, el gateway reconecta solo (probado con el simulador); si cae el proceso del gateway no hay redundancia ni reinicio automático | Argumentado |
| Mantenibilidad | Cambiar un módulo sin tocar los demás | Módulos separados (`gateway/`, `infra/`, `loadtest/`), ADR y 72 pruebas. No se midió complejidad ni cobertura | Argumentado |
| Modificabilidad | Sustituir el almacenamiento sin tocar el gateway | Timestream a DynamoDB afectó a la Lambda, la plantilla, los scripts de verificación y la documentación; el gateway no cambió | Observado |
| Testeabilidad | Probar la lógica sin acceso a AWS ni a un servidor OPC UA | 72 pruebas unitarias sin red (`tests/`), listas para el flujo de CI definido (el repositorio aún no tiene remoto, así que no se ha ejecutado allí). No se midió cobertura | Observado |
| Reutilización | Usar el gateway en otro sitio | Se configura por variables de entorno (`SITE_ID`, endpoints, certificados). Un solo sitio probado | Argumentado |
| Interoperabilidad | Integrar otro servidor OPC UA o consumidor | Origen estándar (IEC 62541) y destino MQTT con JSON. Solo se probó con el servidor simulado | Parcial |
| Soportabilidad | Diagnosticar un fallo en producción | Latido del gateway cada 15 s, logs y métricas EMF, siete alarmas y un panel de CloudWatch, README. Latido y alarmas verificados en AWS, incluido un corte real de 5 min: la ausencia de latido salta a los ~3.5 min y la cola solo se ve al reconectar; aviso por correo sin verificar | Argumentado |

## Cómo se midió

El método, los límites y la corrección por desfase de reloj están en el
[ADR 0004](../adr/0004-metodo-de-medicion.md). Las latencias tienen una incertidumbre de unos 40 ms.
