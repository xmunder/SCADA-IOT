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

Espacio `ScadaIot`, dimensión `Site`: `ReadingsWritten`, `ReadingsRejected` e `IngestLatencyMs`. La latencia se emite
como lista de hasta 100 valores por invocación para que CloudWatch calcule percentiles; en la métrica se recorta a 0
(en DynamoDB se guarda cruda).

## Política de IoT Core

Cada dispositivo puede únicamente:

- `iot:Connect` con un identificador de cliente igual al nombre de su Thing;
- `iot:Publish` en `plant/<nombre del Thing>/telemetry`.

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
