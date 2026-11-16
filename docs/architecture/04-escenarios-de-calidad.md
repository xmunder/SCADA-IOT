# Escenarios de calidad

Un escenario por cada uno de los once atributos de calidad considerados, con el formato de Bass, Clements y
Kazman (estímulo, respuesta y medida). **Los objetivos se fijaron al redactar este documento; no eran requisitos previos de un cliente.**

Estado de la evidencia: **Medido** (hay resultados experimentales), **Observado** (hecho verificable en el desarrollo,
sin experimento dedicado), **Parcial** y **Argumentado** (se apoya en el diseño, no en una medición).

| Atributo | Escenario (estímulo, respuesta, medida) | Resultado y evidencia | Estado |
|---|---|---|---|
| Rendimiento | 1,000 lecturas/s sostenidas: la lectura llega a la base con latencia de ingesta p95 ≤ 0.25 s (sin lote en el borde) | p95 de 0.17 s (3 corridas de 5 min). Con el gateway real a 200 tags, p95 de 1.67 s por la espera del lote. `loadtest/results/matriz-us-east-2-v2/`, `gateway-200-us-east-2/` | Medido |
| Escalabilidad | Subir de 200 a 5,000 tags: sin pérdida y con p95 ≤ 0.25 s | Sin pérdida en 9 corridas; p95 normal con la tabla ya ajustada. Una tabla nueva solo sostiene ~4,000 escrituras/s: la primera corrida a 5,000 tags tuvo p95 de 144 s. `cold-table-test/` | Medido |
| Fiabilidad | Corte de enlace de 5 min: ninguna lectura perdida y recuperación en menos de 30 s | 489 lotes acumulados; recuperación en 7.1 s (una sola prueba); 0 faltantes en 134 tags. `corte-5min-us-east-2/` | Medido |
| Seguridad | Un dispositivo intenta publicar en el tema de otro sitio: la nube lo rechaza | Rechazado, igual que conectar con un cliente ajeno. `infra/check_policy.py` | Medido |
| Disponibilidad | Cae el proceso del gateway: el servicio se restablece automáticamente | **No cubierto**: el gateway es un punto único de falla y se detiene si el servidor OPC UA no responde | Argumentado |
| Mantenibilidad | Cambiar un módulo sin tocar los demás | Módulos separados (`gateway/`, `infra/`, `loadtest/`), ADR y 72 pruebas. No se midió complejidad ni cobertura | Argumentado |
| Modificabilidad | Sustituir el almacenamiento sin tocar el gateway | Timestream a DynamoDB afectó a la Lambda, la plantilla, los scripts de verificación y la documentación; el gateway no cambió | Observado |
| Testeabilidad | Probar la lógica sin acceso a AWS ni a un servidor OPC UA | 72 pruebas unitarias sin red (`tests/`), listas para el flujo de CI definido (el repositorio aún no tiene remoto, así que no se ha ejecutado allí). No se midió cobertura | Observado |
| Reutilización | Usar el gateway en otro sitio | Se configura por variables de entorno (`SITE_ID`, endpoints, certificados). Un solo sitio probado | Argumentado |
| Interoperabilidad | Integrar otro servidor OPC UA o consumidor | Origen estándar (IEC 62541) y destino MQTT con JSON. Solo se probó con el servidor simulado | Parcial |
| Soportabilidad | Diagnosticar un fallo en producción | Estadísticas del gateway cada 10 s, logs y métricas EMF de la Lambda, README. Sin alarmas ni tablero | Argumentado |

## Cómo se midió

El método, los límites y la corrección por desfase de reloj están en el
[ADR 0004](../adr/0004-metodo-de-medicion.md). Las latencias tienen una incertidumbre de unos 40 ms.
