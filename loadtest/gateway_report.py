"""Latencia de ingesta y continuidad de las series con el gateway real (OPC UA -> lotes -> IoT Core).

A diferencia de ``mqtt_load``, aquí la carga proviene de la cadena completa: el simulador OPC UA, el
gateway con su agrupación en lotes y su buffer, IoT Core, la Lambda y DynamoDB. La latencia incluye
por tanto el intervalo de publicación de OPC UA y la espera del lote en el borde.

OPC UA solo notifica cuando un valor cambia, de modo que los tags de estado (binarios) no emiten en
cada ciclo y contar lecturas no sirve para medir pérdida. En su lugar se analiza la continuidad de la
serie de cada tag: si la mediana de sus intervalos es cercana al período de actualización (tag
analógico), cada intervalo mucho mayor que el período se interpreta como lecturas faltantes.

Uso:
    python -m loadtest.gateway_report --start-ms <ms> --end-ms <ms> --tags 200 --period 1.0 \
        --table scada_telemetry

La ventana debe excluir el arranque (suscripciones aún sin completar) y los últimos segundos antes de
detener el gateway. Los relojes del simulador/gateway y de la Lambda deben estar sincronizados (NTP).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from loadtest.clock_check import measure_clock
from loadtest.latency_report import correct_latencies
from loadtest.report import summarize_latencies
from loadtest.verify_counts import DEFAULT_WORKERS, READ_RETRIES

GAP_FACTOR = 1.5  # un intervalo mayor que 1.5 períodos se considera un hueco


def analyze_series(ts_ms: list[int], period_s: float) -> dict[str, Any]:
    """Cuenta lecturas y estima las faltantes en la serie de un tag.

    Un tag es analógico si la mediana de sus intervalos no supera ``GAP_FACTOR`` períodos; solo en
    esos tags un intervalo grande indica pérdida y no ausencia de cambio de valor.
    """
    ordered = sorted(set(ts_ms))
    if len(ordered) < 3:
        return {"count": len(ordered), "analog": False, "missing": 0}
    intervals = [(b - a) / 1000.0 for a, b in pairwise(ordered)]
    analog = statistics.median(intervals) <= GAP_FACTOR * period_s
    missing = 0
    if analog:
        missing = sum(round(i / period_s) - 1 for i in intervals if i > GAP_FACTOR * period_s)
    return {"count": len(ordered), "analog": analog, "missing": missing}


def collect_tag(table: Any, series: str, start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """(ts_ms, ingest_latency_ms) de una serie dentro de la ventana (Query paginado)."""
    kwargs: dict[str, Any] = {
        "ProjectionExpression": "ts_ms, ingest_latency_ms",
        "KeyConditionExpression": Key("series").eq(series) & Key("ts_ms").between(int(start_ms), int(end_ms)),
    }
    points: list[tuple[int, int]] = []
    while True:
        response = table.query(**kwargs)
        points.extend((int(i["ts_ms"]), int(i["ingest_latency_ms"])) for i in response["Items"])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return points
        kwargs["ExclusiveStartKey"] = last_key


def build_report(
    points_by_tag: dict[str, list[tuple[int, int]]], period_s: float, window_s: float, offset_s: float = 0.0
) -> dict[str, Any]:
    raw = [float(ms) for points in points_by_tag.values() for _, ms in points]
    latencies = correct_latencies(raw, offset_s)
    series = {tag: analyze_series([ts for ts, _ in points], period_s) for tag, points in points_by_tag.items()}
    analog = [s for s in series.values() if s["analog"]]
    stored = sum(s["count"] for s in analog)
    missing = sum(s["missing"] for s in analog)
    return {
        "window_s": round(window_s, 1),
        "tags_with_data": sum(1 for s in series.values() if s["count"]),
        "items": len(latencies),
        "items_per_s": round(len(latencies) / window_s, 1) if window_s else None,
        "ingest_latency": summarize_latencies(latencies),
        "raw_latency_min_ms": min(raw) if raw else None,
        "clock_offset_ms": round(offset_s * 1000.0, 1),
        "analog_tags": len(analog),
        "analog_readings": stored,
        "estimated_missing": missing,
        "estimated_loss_pct": round(100.0 * missing / (stored + missing), 4) if stored + missing else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Latencia y continuidad con el gateway real")
    parser.add_argument("--site", default="PLANTA-01")
    parser.add_argument("--table", required=True)
    parser.add_argument("--start-ms", required=True, type=int)
    parser.add_argument("--end-ms", required=True, type=int)
    parser.add_argument("--tags", type=int, default=200, help="número de tags del simulador")
    parser.add_argument("--period", type=float, default=1.0, help="período de actualización del simulador (s)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--clock-offset-ms",
        type=float,
        default=None,
        help="desfase de reloj (referencia - equipo) a aplicar; por defecto se mide ahora por SNTP",
    )
    args = parser.parse_args()
    if args.end_ms <= args.start_ms:
        parser.error("--end-ms debe ser mayor que --start-ms")

    table = boto3.resource(
        "dynamodb", config=Config(max_pool_connections=args.workers * 2, retries=READ_RETRIES)
    ).Table(args.table)
    names = [f"Tag_{i:04d}" for i in range(args.tags)]

    def one(name: str) -> list[tuple[int, int]]:
        return collect_tag(table, f"{args.site}#{name}", args.start_ms, args.end_ms)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        points_by_tag = dict(zip(names, pool.map(one, names), strict=True))
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
    report = build_report(points_by_tag, args.period, (args.end_ms - args.start_ms) / 1000.0, offset_s)
    report["verified_at_ms"] = int(time.time() * 1000)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
