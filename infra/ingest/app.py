"""Lambda that receives telemetry batches from AWS IoT Core and writes them to Amazon DynamoDB.

One item is written per GOOD reading, keyed by (series, ts_ms) so replays are idempotent:
  - series: "<site>#<tag>" (partition key)
  - ts_ms: source timestamp in milliseconds (sort key)
  - value: the tag value.
  - ingest_latency_ms: time between the source instant and the cloud write. Stored raw (it can be
    negative if the source clock is ahead of the Lambda clock) so measurements can be corrected.
Readings with a quality other than GOOD are counted as rejected and not stored.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

METRICS_NAMESPACE = "ScadaIot"
MAX_EMF_VALUES = 100  # límite de valores por métrica en un registro EMF

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_table: Any = None


def _dynamodb_table() -> Any:
    global _table
    if _table is None:
        resource = boto3.resource(
            "dynamodb",
            config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        )
        _table = resource.Table(os.environ["TELEMETRY_TABLE"])
    return _table


def build_items(event: dict[str, Any], received_ms: int) -> tuple[list[dict[str, Any]], int]:
    """Convert a batch into DynamoDB items. Returns (items, rejected_readings)."""
    site = str(event["site"])
    items: list[dict[str, Any]] = []
    rejected = 0
    readings = event.get("readings", [])
    if not isinstance(readings, list):
        return items, 1
    for reading in readings:
        if not isinstance(reading, dict):
            rejected += 1
            continue
        try:
            if reading.get("quality") != "GOOD":
                rejected += 1
                continue
            tag = str(reading["tag"])
            ts_ms = int(reading["ts_ms"])
            value = float(reading["value"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue

        items.append(
            {
                "series": f"{site}#{tag}",
                "ts_ms": ts_ms,
                "site": site,
                "tag": tag,
                "value": Decimal(repr(value)),
                # Sin recortar: con relojes desfasados puede ser negativo, y conservar el valor crudo
                # permite corregirlo después con el desfase medido (un recorte a 0 lo destruye).
                "ingest_latency_ms": received_ms - ts_ms,
            }
        )
    return items, rejected


def write_items(table: Any, items: list[dict[str, Any]]) -> None:
    # batch_writer chunks into 25-item requests, retries unprocessed items and
    # de-duplicates keys within a buffer.
    with table.batch_writer(overwrite_by_pkeys=["series", "ts_ms"]) as batch:
        for item in items:
            batch.put_item(Item=item)


def build_emf(site: str, written: int, rejected: int, latencies_ms: list[int], timestamp_ms: int) -> dict[str, Any]:
    """Registro en Embedded Metric Format: CloudWatch lo convierte en métricas sin llamar a su API.

    La latencia se emite como lista de valores (máximo 100, submuestreada de forma uniforme) para
    que CloudWatch pueda calcular percentiles (p50, p95, p99) sobre las lecturas individuales.
    """
    metrics = [
        {"Name": "ReadingsWritten", "Unit": "Count"},
        {"Name": "ReadingsRejected", "Unit": "Count"},
    ]
    record: dict[str, Any] = {"Site": site, "ReadingsWritten": written, "ReadingsRejected": rejected}
    if latencies_ms:
        stride = math.ceil(len(latencies_ms) / MAX_EMF_VALUES)
        record["IngestLatencyMs"] = latencies_ms[::stride]
        metrics.append({"Name": "IngestLatencyMs", "Unit": "Milliseconds"})
    record["_aws"] = {
        "Timestamp": timestamp_ms,
        "CloudWatchMetrics": [{"Namespace": METRICS_NAMESPACE, "Dimensions": [["Site"]], "Metrics": metrics}],
    }
    return record


def _handle_sqs(event: dict[str, Any], received_ms: int) -> dict[str, list[dict[str, str]]]:
    """Procesa un lote de mensajes de SQS (cada uno es un mensaje del gateway) con una sola escritura a la tabla.

    Devuelve fallos parciales: si la escritura falla, solo se reintentan los mensajes que tenían lecturas válidas, y
    como la clave (serie, tiempo) es idempotente, reescribirlas no duplica nada. Un cuerpo inválido no se reintenta
    (seguiría igual de inválido): se cuenta como rechazado. Lo escrito solo se informa cuando la escritura salió bien,
    para no contarlo dos veces en el reintento.
    """
    parsed: list[tuple[str, str, list[dict[str, Any]], int]] = []  # (id del mensaje, sitio, items, rechazadas)
    for record in event["Records"]:
        message_id = str(record.get("messageId", ""))
        try:
            body = json.loads(record["body"])
        except (KeyError, TypeError, ValueError):
            body = None
        if not isinstance(body, dict) or not body.get("site"):
            parsed.append((message_id, "unknown", [], 1))
            continue
        items, rejected = build_items(body, received_ms)
        parsed.append((message_id, str(body["site"]), items, rejected))

    all_items = [item for _, _, items, _ in parsed for item in items]
    write_ok = True
    if all_items:
        try:
            write_items(_dynamodb_table(), all_items)
        except Exception:  # cualquier fallo de escritura (límites de DynamoDB, red) se reintenta desde la cola
            logger.exception("fallo al escribir %d items; el lote vuelve a la cola", len(all_items))
            write_ok = False

    per_site: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for message_id, site, items, rejected in parsed:
        if items and not write_ok:
            failures.append({"itemIdentifier": message_id})
            continue
        totals = per_site.setdefault(site, {"written": 0, "rejected": 0, "latencies": []})
        totals["written"] += len(items)
        totals["rejected"] += rejected
        totals["latencies"].extend(max(0, item["ingest_latency_ms"]) for item in items)
    for site, totals in per_site.items():
        print(json.dumps(build_emf(site, totals["written"], totals["rejected"], totals["latencies"], received_ms)))
    logger.info("sqs records=%d failed=%d", len(parsed), len(failures))
    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], context: Any) -> Any:
    received_ms = int(time.time() * 1000)
    if isinstance(event, dict) and "Records" in event:
        return _handle_sqs(event, received_ms)
    if not isinstance(event, dict) or not event.get("site"):
        # Un mensaje sin sitio no se puede atribuir ni reintentar con éxito: se cuenta como rechazado y no falla la
        # invocación (IoT Core reintentaría dos veces un mensaje que seguiría igual de inválido).
        logger.warning("mensaje sin sitio descartado")
        print(json.dumps(build_emf("unknown", 0, 1, [], received_ms)))
        return {"written": 0, "rejected": 1}
    items, rejected = build_items(event, received_ms)
    if items:
        write_items(_dynamodb_table(), items)
    logger.info("batch site=%s written=%d rejected=%d", event.get("site"), len(items), rejected)
    # La métrica operativa de CloudWatch no admite latencias negativas: se recorta solo ahí.
    emf_latencies = [max(0, i["ingest_latency_ms"]) for i in items]
    print(json.dumps(build_emf(str(event["site"]), len(items), rejected, emf_latencies, received_ms)))
    return {"written": len(items), "rejected": rejected}
