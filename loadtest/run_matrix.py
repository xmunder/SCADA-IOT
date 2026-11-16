"""Ejecuta la matriz de pruebas de estrés (niveles de tags x repeticiones) y agrega resultados.

Para cada nivel L se publican L lecturas por segundo (L tags actualizados una vez por segundo),
agrupadas en mensajes de ``--readings-per-msg`` lecturas. Tras cada corrida espera ``--settle``
segundos a que la Lambda termine, lee DynamoDB y registra pérdida y latencia. Cada corrida se
escribe en ``runs.csv`` al terminar, de modo que una interrupción no pierde lo ya medido.
Al final calcula media y desviación estándar por nivel en ``summary.csv``.

Uso (con las variables de entorno del gateway):
    python -m loadtest.run_matrix --levels 200,1000,5000 --duration 60 --repetitions 3 \
        --table scada_telemetry --name matriz-1
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from gateway.config import GatewayConfig
from loadtest.clock_check import ClockReading, measure_clock
from loadtest.latency_report import build_report, collect_run
from loadtest.mqtt_load import run_load
from loadtest.verify_counts import DEFAULT_WORKERS, READ_RETRIES

logger = logging.getLogger("loadtest.matrix")

# Estimación del costo de escritura de DynamoDB bajo demanda (us-east-1 y us-east-2): 1 unidad de escritura
# por item de hasta 1 KB. Verifique el valor vigente en la calculadora de precios de AWS.
WRITE_REQUEST_UNIT_USD = 0.625 / 1_000_000

RUN_FIELDS = [
    "level_tags",
    "repetition",
    "readings_sent",
    "readings_stored",
    "lost_readings",
    "loss_pct",
    "achieved_rate_readings_s",
    "msgs_unacked",
    "puback_p95_ms",
    "ingest_wait_s",
    "clock_offset_ms",
    "clock_drift_ms",
    "clock_uncertainty_ms",
    "raw_latency_min_ms",
    "ingest_p50_ms",
    "ingest_p95_ms",
    "ingest_p99_ms",
    "ingest_max_ms",
    "est_write_cost_usd",
]
METRICS = ["loss_pct", "achieved_rate_readings_s", "puback_p95_ms", "ingest_p50_ms", "ingest_p95_ms", "ingest_p99_ms"]


def run_row(
    level: int,
    repetition: int,
    summary: dict[str, Any],
    report: dict[str, Any],
    ingest_wait_s: float = 0.0,
    clock: dict[str, float] | None = None,
) -> dict[str, Any]:
    clock = clock or {}
    latency = report["ingest_latency"]
    stored = report["readings_stored_distinct"]
    return {
        "level_tags": level,
        "repetition": repetition,
        "readings_sent": report["readings_expected"],
        "readings_stored": stored,
        "lost_readings": report["lost_readings"],
        "loss_pct": report["loss_pct"],
        "achieved_rate_readings_s": summary["achieved_rate_readings_s"],
        "msgs_unacked": summary["msgs_unacked"],
        "puback_p95_ms": summary["publish_latency"]["p95_ms"],
        "ingest_wait_s": round(ingest_wait_s, 1),
        "clock_offset_ms": clock.get("offset_ms"),
        "clock_drift_ms": clock.get("drift_ms"),
        "clock_uncertainty_ms": clock.get("uncertainty_ms"),
        "raw_latency_min_ms": report.get("raw_latency_min_ms"),
        "ingest_p50_ms": latency["p50_ms"],
        "ingest_p95_ms": latency["p95_ms"],
        "ingest_p99_ms": latency["p99_ms"],
        "ingest_max_ms": latency["max_ms"],
        "est_write_cost_usd": round(stored * WRITE_REQUEST_UNIT_USD, 4),
    }


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Media y desviación estándar muestral por nivel. Con una sola repetición la desviación es 0."""
    result: list[dict[str, Any]] = []
    for level in sorted({row["level_tags"] for row in rows}):
        group = [row for row in rows if row["level_tags"] == level]
        entry: dict[str, Any] = {"level_tags": level, "repetitions": len(group)}
        for metric in METRICS:
            values = [row[metric] for row in group if row[metric] is not None]
            entry[f"{metric}_mean"] = round(statistics.mean(values), 3) if values else None
            entry[f"{metric}_std"] = (
                round(statistics.stdev(values), 3) if len(values) > 1 else (0.0 if values else None)
            )
        entry["est_write_cost_usd_total"] = round(sum(row["est_write_cost_usd"] for row in group), 4)
        result.append(entry)
    return result


def combine_clock(before: ClockReading, after: ClockReading) -> dict[str, float]:
    """Desfase de una corrida: media de las lecturas previa y posterior, con su deriva e incertidumbre."""
    return {
        "offset_s": (before.offset_s + after.offset_s) / 2.0,
        "offset_ms": round((before.offset_s + after.offset_s) * 500.0, 1),
        "drift_ms": round((after.offset_s - before.offset_s) * 1000.0, 1),
        "uncertainty_ms": round(max(before.uncertainty_s, after.uncertainty_s) * 1000.0, 1),
    }


def wait_for_ingest(
    read_report: Callable[[], dict[str, Any]],
    max_wait_s: float,
    poll_s: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], float]:
    """Relee hasta que no falte ninguna lectura o se agote ``max_wait_s``.

    Con carga alta la Lambda puede reintentar (límites de DynamoDB o de concurrencia) y los items
    llegan tarde; medir demasiado pronto contaría como pérdida lo que solo está retrasado.
    Devuelve (informe, segundos esperados).
    """
    start = clock()
    while True:
        report = read_report()
        waited = clock() - start
        if report["lost_readings"] <= 0 or waited >= max_wait_s:
            return report, waited
        sleep(poll_s)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Matriz de pruebas de estrés")
    parser.add_argument("--levels", default="200,1000,5000", help="tags por nivel, separados por coma")
    parser.add_argument("--duration", type=float, default=60.0, help="segundos de publicación por corrida")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--readings-per-msg", type=int, default=100)
    parser.add_argument("--settle", type=float, default=20.0, help="segundos de espera tras cada corrida")
    parser.add_argument("--max-wait", type=float, default=180.0, help="espera máxima a que termine la ingesta (s)")
    parser.add_argument(
        "--max-clock-skew", type=float, default=0.25, help="desfase máximo tolerado del reloj (s); más allá se aborta"
    )
    parser.add_argument("--ignore-clock-skew", action="store_true", help="continuar aunque el reloj esté desfasado")
    parser.add_argument("--table", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, default=Path("loadtest/results"))
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",")]
    if min(levels) < args.readings_per_msg:
        parser.error("cada nivel debe tener al menos --readings-per-msg tags")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = GatewayConfig.from_env()
    clock = measure_clock()
    offset = clock.offset_s
    logger.info(
        "Desfase de reloj (referencia - equipo): %+.0f ms, método %s, incertidumbre ~%.0f ms",
        offset * 1000,
        clock.method,
        clock.uncertainty_s * 1000,
    )
    if (abs(offset) > args.max_clock_skew or clock.uncertainty_s > args.max_clock_skew) and not args.ignore_clock_skew:
        raise SystemExit(
            f"Reloj desfasado {offset * 1000:+.0f} ms (método {clock.method}, incertidumbre "
            f"~{clock.uncertainty_s * 1000:.0f} ms): las latencias serían inválidas. Sincronice con NTP "
            "(w32tm /resync), compruebe que UDP 123 no esté bloqueado o use --ignore-clock-skew."
        )
    table = boto3.resource(
        "dynamodb", config=Config(max_pool_connections=DEFAULT_WORKERS * 2, retries=READ_RETRIES)
    ).Table(args.table)
    out_dir = args.out / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clock.json").write_text(
        json.dumps(
            {"offset_s": round(offset, 4), "method": clock.method, "uncertainty_s": round(clock.uncertainty_s, 4)}
        ),
        encoding="utf8",
    )
    rows: list[dict[str, Any]] = []

    for level in levels:
        for repetition in range(1, args.repetitions + 1):
            logger.info("Nivel %d tags, repetición %d/%d", level, repetition, args.repetitions)
            clock_before = measure_clock(3)
            summary, _ = run_load(cfg, float(level), args.readings_per_msg, args.duration, level)
            clock_after = measure_clock(3)
            run_clock = combine_clock(clock_before, clock_after)
            logger.info(
                "Desfase de reloj de la corrida: %+.0f ms (deriva %+.0f ms, incertidumbre ~%.0f ms)",
                run_clock["offset_ms"],
                run_clock["drift_ms"],
                run_clock["uncertainty_ms"],
            )
            summary["run_name"] = f"{args.name}-{level}-r{repetition}"
            time.sleep(args.settle)

            def read_report(
                summary: dict[str, Any] = summary, offset_s: float = run_clock["offset_s"]
            ) -> dict[str, Any]:
                # Se atan summary y offset_s a esta iteración (se evalúa dentro de ella, pero así es explícito).
                return build_report(summary, collect_run(table, summary, grace_ms=int(args.settle * 1000)), offset_s)

            report, waited = wait_for_ingest(read_report, args.max_wait)
            rows.append(run_row(level, repetition, summary, report, waited, run_clock))
            (out_dir / f"{summary['run_name']}.json").write_text(
                json.dumps({"summary": summary, "report": report}, indent=2), encoding="utf8"
            )
            write_csv(out_dir / "runs.csv", RUN_FIELDS, rows)

    aggregated = aggregate(rows)
    fields = list(aggregated[0].keys())
    write_csv(out_dir / "summary.csv", fields, aggregated)
    logger.info("Resultados en %s", out_dir)
    print(json.dumps(aggregated, indent=2))


if __name__ == "__main__":
    main()
