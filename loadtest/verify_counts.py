"""Verifica en Amazon DynamoDB cuántas lecturas llegaron frente a las enviadas.

Uso:
    python -m loadtest.verify_counts --summary loadtest/results/carga-1000/summary.json         --table scada_telemetry

Hace un Query por serie (``site#tag``) con ``Select=COUNT``, en paralelo, usando el
conteo por tag que ``mqtt_load`` guarda en ``summary.json``. Así lee solo las claves
de la prueba y reporta qué tags perdieron lecturas. Como la clave es (serie, tiempo),
un reenvío sobrescribe el mismo item: la tabla no conserva duplicados.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

INGEST_GRACE_MS = 120_000  # margen para que las últimas escrituras sean visibles
DEFAULT_WORKERS = 20
# Bajo carga alta DynamoDB puede devolver errores transitorios: reintentos adaptativos en las lecturas.
READ_RETRIES = {"max_attempts": 10, "mode": "adaptive"}


def count_series(table: Any, series: str, start_ms: int, end_ms: int) -> int:
    """Cuenta los items de una serie dentro del rango de tiempo (Query paginado)."""
    kwargs: dict[str, Any] = {
        "Select": "COUNT",
        "KeyConditionExpression": Key("series").eq(series) & Key("ts_ms").between(int(start_ms), int(end_ms)),
    }
    total = 0
    while True:
        response = table.query(**kwargs)
        total += response["Count"]
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return total
        kwargs["ExclusiveStartKey"] = last_key


def compare_counts(expected: dict[str, int], stored: dict[str, int]) -> dict[str, Any]:
    """Compara lo enviado con lo almacenado, por tag y en total."""
    expected_total = sum(expected.values())
    stored_total = sum(min(stored.get(tag, 0), n) for tag, n in expected.items())
    lost_by_tag = {tag: n - stored.get(tag, 0) for tag, n in sorted(expected.items()) if stored.get(tag, 0) < n}
    lost = expected_total - stored_total
    return {
        "readings_expected": expected_total,
        "readings_stored_distinct": stored_total,
        "lost_readings": lost,
        "loss_pct": round(100.0 * lost / expected_total, 4) if expected_total else None,
        "tags_with_loss": len(lost_by_tag),
        "lost_by_tag": lost_by_tag,
    }


def verify(table: Any, summary: dict[str, Any], workers: int = DEFAULT_WORKERS) -> dict[str, Any]:
    tag_counts = summary.get("tag_counts")
    if not tag_counts:
        raise SystemExit("summary.json no incluye 'tag_counts'; repita la prueba con la versión actual de mqtt_load")
    site = summary["site"]
    start_ms = summary["started_ms"] - 1000
    end_ms = summary["ended_ms"] + INGEST_GRACE_MS
    tags = list(tag_counts)

    def one(tag: str) -> int:
        return count_series(table, f"{site}#{tag}", start_ms, end_ms)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        stored = dict(zip(tags, pool.map(one, tags), strict=True))
    return compare_counts(tag_counts, stored)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica la pérdida de lecturas en DynamoDB")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--table", required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf8"))
    table = boto3.resource(
        "dynamodb", config=Config(max_pool_connections=args.workers * 2, retries=READ_RETRIES)
    ).Table(args.table)
    result = {"run_name": summary.get("run_name"), **verify(table, summary, args.workers)}
    result["verified_at_ms"] = int(time.time() * 1000)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
