# Interfaces y modelo de datos

## Mensaje MQTT

- **Tema:** `plant/<SITE_ID>/telemetry`. **QoS 1.** TLS 1.2 con autenticación mutua por certificado.
- **Carga útil:** JSON compacto, sin `NaN` ni `Infinity`.

```json
{
  "site": "PLANTA-01",
  "readings": [
    {"tag": "Tag_0001", "value": 12.5, "ts_ms": 1794861697033, "quality": "GOOD"}
  ]
}
```

| Campo | Significado |
|---|---|
| `site` | Identificador del sitio; coincide con el nombre del Thing |
| `tag` | Nombre del tag OPC UA |
| `value` | Valor numérico; los booleanos se envían como 1.0 y 0.0 |
| `ts_ms` | Instante de origen (`SourceTimestamp` de OPC UA) en milisegundos desde el epoch |
| `quality` | `GOOD` o `BAD`; la Lambda solo guarda `GOOD` |

Un lote lleva hasta 100 lecturas (`GATEWAY_BATCH_SIZE`) o las acumuladas durante `GATEWAY_FLUSH_INTERVAL_S`.

## Cola de ingesta (modo `queue`)

Con `IngestMode=queue` la regla de IoT deja cada mensaje del gateway, sin cambios, en una cola SQS. La Lambda de ingesta lee de ella
en lotes y escribe todas sus lecturas en una sola operación. Parámetros de la plantilla:

| Parámetro | Valor por defecto | Efecto |
|---|---|---|
| `IngestBatchSize` | 10 mensajes | Hasta 1,000 lecturas por invocación (cada mensaje trae hasta 100) |
| `IngestBatchWindowSeconds` | 1 | Cuánto espera la Lambda para juntar el lote |
| `IngestMaxConcurrency` | 10 | Copias de la Lambda leyendo a la vez: es el tope de la velocidad de escritura a DynamoDB |
| `QueueAgeThresholdSeconds` | 300 | Edad del mensaje más antiguo que dispara la alarma |

Si la escritura falla, solo vuelven a la cola los mensajes que tenían lecturas válidas (respuesta de fallos parciales). Tras cinco
intentos fallidos un mensaje pasa a la cola de fallidos, que conserva 14 días y tiene su propia alarma. Un cuerpo que no es JSON válido no
se reintenta: se cuenta como rechazado.

## Mensaje de estado (latido)

Cada `GATEWAY_STATUS_INTERVAL_S` el gateway publica su estado en `plant/<site>/status`. Es de mejor esfuerzo: no pasa por el
buffer, porque un estado atrasado no sirve, y su ausencia es lo que dispara la alarma de gateway caído.

| Campo | Contenido |
|---|---|
| `type` | Siempre `status` |
| `site`, `ts_ms` | Sitio e instante de la medición |
| `pending` | Lotes esperando en la cola local |
| `batches_sent`, `readings_in`, `readings_dropped` | Contadores desde el arranque |
| `mqtt_connected`, `opc_connected` | Estado de las dos conexiones |

## Almacenamiento en DynamoDB

Un item por lectura válida. Clave de partición `series` y clave de orden `ts_ms`.

| Atributo | Tipo | Contenido |
|---|---|---|
| `series` | S | `<site>#<tag>` (clave de partición) |
| `ts_ms` | N | Instante de origen (clave de orden) |
| `site`, `tag` | S | Componentes de la serie |
| `value` | N | Valor de la lectura |
| `ingest_latency_ms` | N | `hora de la Lambda - ts_ms`, **sin recortar**: puede ser negativa con un reloj de origen adelantado |

La clave `(series, ts_ms)` hace idempotente la escritura: reenviar un lote sobrescribe los mismos items.
Consulta típica: `series = :s AND ts_ms BETWEEN :a AND :b`.

## Buffer local del gateway (SQLite)

Tabla `outbox`: `id` (autoincremental), `created_ms` y `payload` (el mensaje ya serializado). Un registro se borra
solo tras el PUBACK de IoT Core.

## Métricas EMF (CloudWatch)

Espacio `ScadaIot`, dimensión `Site`. La Lambda de ingesta emite `ReadingsWritten`, `ReadingsRejected` e
`IngestLatencyMs`. La latencia se emite como lista de hasta 100 valores por invocación para que CloudWatch calcule
percentiles; en la métrica se recorta a 0 (en DynamoDB se guarda cruda).

La Lambda de estado emite, por cada latido, `GatewayHeartbeat` (siempre 1), `GatewayPendingBatches`,
`GatewayOpcConnected`, `GatewayMqttConnected` (0 o 1) y `GatewayReadingsDropped`. Usa la hora de la Lambda, no la del
gateway, para que un reloj desfasado no oculte un latido.

## Alarmas

| Alarma | Se activa cuando |
|---|---|
| Errores de la Lambda de ingesta | Hay errores en un minuto |
| Limitaciones de la Lambda de ingesta | Hay invocaciones limitadas en un minuto |
| Lecturas rechazadas | Hay lecturas rechazadas en un minuto |
| Latencia p95 | Supera `LatencyP95ThresholdMs` (5,000 ms) tres minutos seguidos |
| Cola del gateway | Supera `PendingBatchesThreshold` (200 lotes); con un enlace cortado solo se ve al reconectar |
| Edad de la cola de ingesta | El mensaje más antiguo espera más de `QueueAgeThresholdSeconds` (modo `queue`) |
| Mensajes en la cola de fallidos | Hay algún mensaje que falló cinco veces |
| Sesión OPC UA perdida | `GatewayOpcConnected` baja de 1 dos minutos seguidos |
| Ausencia de latido | No llega ningún latido en tres minutos (cada minuto sin latido se evalúa como 0): salta ~3 min después de detener el gateway |

## Política de IoT Core

Cada dispositivo puede únicamente:

- `iot:Connect` con un identificador de cliente igual al nombre de su Thing;
- `iot:Publish` en `plant/<nombre del Thing>/telemetry` y en `plant/<nombre del Thing>/status`.

## Configuración del gateway (variables de entorno)

| Variable | Obligatoria | Valor por defecto |
|---|---|---|
| `SITE_ID`, `OPC_ENDPOINT`, `OPC_NAMESPACE_URI`, `IOT_ENDPOINT`, `CERT_FILE`, `KEY_FILE`, `CA_FILE` | Sí | - |
| `OPC_ROOT_NAME` | No | `Planta` |
| `IOT_PORT` | No | `8883` |
| `BUFFER_PATH` | No | `data/buffer.db` |
| `GATEWAY_BATCH_SIZE` | No | `100` |
| `GATEWAY_FLUSH_INTERVAL_S` | No | `1.0` |
| `GATEWAY_INFLIGHT_WINDOW` | No | `16` |
| `OPC_RECONNECT_MAX_S` | No | `30` |
| `GATEWAY_STATUS_INTERVAL_S` | No | `15` |
