"""Latencia de ingesta (p50/p95/p99) y pérdida de lecturas de una corrida, leídas desde DynamoDB.

Uso:
    python -m loadtest.latency_report --summary loadtest/results/carga-1000/summary.json \
        --table scada_telemetry

``ingest_latency_ms`` es la diferencia entre el instante en que la Lambda escribe y el
``ts_ms`` de origen de la lectura. Con ``mqtt_load`` mide publicación + IoT Core + Lambda; con
el gateway real incluye además la espera del lote en el borde. Ambos relojes (equipo de
origen y Lambda) deben estar sincronizados con NTP.

Cada serie se lee con un Query que proyecta solo ``ingest_latency_ms``, de modo que una sola
pasada entrega el conteo (para la pérdida) y las latencias (para los percentiles).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from loadtest.clock_check import measure_clock
from loadtest.report import summarize_latencies
from loadtest.verify_counts import DEFAULT_WORKERS, INGEST_GRACE_MS, READ_RETRIES, compare_counts


def collect_series(table: Any, series: str, start_ms: int, end_ms: int) -> list[int]:
    """Latencias (ms) de cada item de una serie dentro del rango (Query paginado)."""
    kwargs: dict[str, Any] = {
        "ProjectionExpression": "ingest_latency_ms",
        "KeyConditionExpression": Key("series").eq(series) & Key("ts_ms").between(int(start_ms), int(end_ms)),
    }
    latencies: list[int] = []
    while True:
        response = table.query(**kwargs)
        latencies.extend(int(item["ingest_latency_ms"]) for item in response["Items"] if "ingest_latency_ms" in item)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return latencies
        kwargs["ExclusiveStartKey"] = last_key


def collect_run(
    table: Any,
    summary: dict[str, Any],
    workers: int = DEFAULT_WORKERS,
    grace_ms: int = INGEST_GRACE_MS,
) -> dict[str, list[int]]:
    """Lee, por tag, las latencias de los items que dejó una corrida de ``mqtt_load``."""
    tag_counts = summary.get("tag_counts")
    if not tag_counts:
        raise SystemExit("summary.json no incluye 'tag_counts'; repita la prueba con la versión actual de mqtt_load")
    site = summary["site"]
    start_ms = summary["started_ms"] - 1000
    end_ms = summary["ended_ms"] + grace_ms
    tags = list(tag_counts)

    def one(tag: str) -> list[int]:
        return collect_series(table, f"{site}#{tag}", start_ms, end_ms)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(tags, pool.map(one, tags), strict=True))


def correct_latencies(measured_ms: list[float], offset_s: float) -> list[float]:
    """Corrige las latencias por el desfase de reloj (``offset_s`` = referencia - equipo).

    latencia medida = latencia real + offset, de modo que la latencia real es ``medida - offset``.
    Un equipo adelantado (offset < 0) subestima la latencia; uno atrasado la sobrestima.
    """
    return [ms - offset_s * 1000.0 for ms in measured_ms]


def build_report(
    summary: dict[str, Any], latencies_by_tag: dict[str, list[int]], offset_s: float = 0.0
) -> dict[str, Any]:
    """Une pérdida y latencia en un único informe, con la latencia corregida por el desfase de reloj."""
    stored = {tag: len(values) for tag, values in latencies_by_tag.items()}
    raw = [float(ms) for values in latencies_by_tag.values() for ms in values]
    return {
        "run_name": summary.get("run_name"),
        **compare_counts(summary["tag_counts"], stored),
        "ingest_latency": summarize_latencies(correct_latencies(raw, offset_s)),
        "latency_samples": len(raw),
        "raw_latency_min_ms": min(raw) if raw else None,
        "clock_offset_ms": round(offset_s * 1000.0, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Latencia de ingesta y pérdida de una corrida")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--table", required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--grace-ms", type=int, default=INGEST_GRACE_MS)
    parser.add_argument(
        "--clock-offset-ms",
        type=float,
        default=None,
        help="desfase de reloj (referencia - equipo) a aplicar; por defecto se mide ahora por SNTP",
    )
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf8"))
    table = boto3.resource(
        "dynamodb", config=Config(max_pool_connections=args.workers * 2, retries=READ_RETRIES)
    ).Table(args.table)
    if args.clock_offset_ms is None:
        clock = measure_clock()
        offset_s = clock.offset_s
        # A stderr para que la salida estándar siga siendo JSON válido al redirigirla a un archivo.
        print(
            f"Desfase de reloj medido ahora: {offset_s * 1000:+.0f} ms ({clock.method}, ~{clock.uncertainty_s * 1000:.0f} ms)",
            file=sys.stderr,
        )
    else:
        offset_s = args.clock_offset_ms / 1000.0
    report = build_report(summary, collect_run(table, summary, args.workers, args.grace_ms), offset_s)
    report["verified_at_ms"] = int(time.time() * 1000)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
