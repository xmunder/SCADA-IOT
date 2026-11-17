"""Barrido del intervalo de vaciado del gateway real: latencia frente a mensajes enviados.

El gateway agrupa lecturas en lotes (hasta ``GATEWAY_BATCH_SIZE`` o cada ``GATEWAY_FLUSH_INTERVAL_S``). Un intervalo
más corto baja la latencia de ingesta a cambio de más mensajes MQTT e invocaciones de la Lambda. Este script
cuantifica ese compromiso con la cadena completa: levanta el simulador OPC UA, ejecuta el gateway con cada intervalo
durante ``--duration`` segundos, lee de DynamoDB y reporta latencia p50/p95/p99, continuidad y tasa de mensajes.

El desfase de reloj se mide por SNTP antes y después de cada intervalo y se resta de las latencias.

Uso (con las variables de entorno del gateway; ejecute desde la raíz del proyecto y detenga otros gateways antes):
    uv run --env-file .env python -m loadtest.flush_sweep --intervals 0.25,0.5,1.0 --tags 200 \
        --table scada_telemetry --name flush-sweep
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from loadtest.clock_check import ClockReading, measure_clock
from loadtest.gateway_report import build_report, collect_tag
from loadtest.verify_counts import DEFAULT_WORKERS, READ_RETRIES

OPC_PORT = 4840
STATS_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO gateway: "
    r"lecturas=(?P<readings>\d+) descartadas=\d+ lotes_bufferizados=\d+ lotes_enviados=(?P<sent>\d+) pendientes=(?P<pending>\d+)"
)


@dataclass(frozen=True)
class Stat:
    t_s: float  # epoch local
    readings: int
    batches_sent: int
    pending: int


def parse_stats(log_text: str) -> list[Stat]:
    """Extrae las líneas de estadísticas que el gateway escribe cada 10 s."""
    stats: list[Stat] = []
    for line in log_text.splitlines():
        match = STATS_LINE.match(line)
        if match:
            when = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
            stats.append(
                Stat(when, int(match.group("readings")), int(match.group("sent")), int(match.group("pending")))
            )
    return stats


def rates(stats: list[Stat], from_s: float, to_s: float) -> dict[str, float | None]:
    """Lecturas y mensajes por segundo entre la primera y la última estadística dentro de [from_s, to_s]."""
    inside = [s for s in stats if from_s <= s.t_s <= to_s]
    if len(inside) < 2 or inside[-1].t_s <= inside[0].t_s:
        return {"readings_per_s": None, "messages_per_s": None}
    span = inside[-1].t_s - inside[0].t_s
    return {
        "readings_per_s": round((inside[-1].readings - inside[0].readings) / span, 2),
        "messages_per_s": round((inside[-1].batches_sent - inside[0].batches_sent) / span, 3),
    }


def combine_offsets(before: ClockReading, after: ClockReading) -> tuple[float, float]:
    """(desfase medio en s, incertidumbre máxima en s) de dos lecturas de reloj."""
    return (before.offset_s + after.offset_s) / 2.0, max(before.uncertainty_s, after.uncertainty_s)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_for_port(port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def read_points(
    table: Any, site: str, tags: int, start_ms: int, end_ms: int, workers: int
) -> dict[str, list[tuple[int, int]]]:
    names = [f"Tag_{i:04d}" for i in range(tags)]

    def one(name: str) -> list[tuple[int, int]]:
        return collect_tag(table, f"{site}#{name}", start_ms, end_ms)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(names, pool.map(one, names), strict=True))


def run_interval(args: argparse.Namespace, table: Any, interval: float, out_dir: Path) -> dict[str, Any]:
    label = f"{interval:g}s"
    log_path = out_dir / f"gateway-{label}.log"
    buffer_path = out_dir / f"buffer-{label}.db"
    env = dict(os.environ, GATEWAY_FLUSH_INTERVAL_S=str(interval), BUFFER_PATH=str(buffer_path))
    clock_before = measure_clock(3)
    started = time.time()
    with log_path.open("w", encoding="utf8") as log:
        gateway = subprocess.Popen(
            [sys.executable, "-m", "gateway.main"], env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            time.sleep(args.duration)
        finally:
            gateway.terminate()
            gateway.wait(timeout=30)
    ended = time.time()
    clock_after = measure_clock(3)
    time.sleep(args.settle)

    offset_s, uncertainty_s = combine_offsets(clock_before, clock_after)
    window_start = int((started + args.warmup) * 1000)
    window_end = int((ended - args.cooldown) * 1000)
    site = os.environ["SITE_ID"]
    points = read_points(table, site, args.tags, window_start, window_end, args.workers)
    report = build_report(points, args.period, (window_end - window_start) / 1000.0, offset_s)
    traffic = rates(
        parse_stats(log_path.read_text(encoding="utf8", errors="replace")), started + args.warmup, ended - args.cooldown
    )
    result = {
        "flush_interval_s": interval,
        "tags": args.tags,
        "clock_offset_ms": round(offset_s * 1000, 1),
        "clock_uncertainty_ms": round(uncertainty_s * 1000, 1),
        **traffic,
        **report,
    }
    (out_dir / f"result-{label}.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    return result


SUMMARY_FIELDS = [
    "flush_interval_s",
    "tags",
    "items",
    "items_per_s",
    "readings_per_s",
    "messages_per_s",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "max_ms",
    "analog_tags",
    "estimated_missing",
    "clock_offset_ms",
    "clock_uncertainty_ms",
]


def summary_row(result: dict[str, Any]) -> dict[str, Any]:
    latency = result["ingest_latency"]
    return {
        "flush_interval_s": result["flush_interval_s"],
        "tags": result["tags"],
        "items": result["items"],
        "items_per_s": result["items_per_s"],
        "readings_per_s": result["readings_per_s"],
        "messages_per_s": result["messages_per_s"],
        "p50_ms": round(latency["p50_ms"], 1) if latency["p50_ms"] is not None else None,
        "p95_ms": round(latency["p95_ms"], 1) if latency["p95_ms"] is not None else None,
        "p99_ms": round(latency["p99_ms"], 1) if latency["p99_ms"] is not None else None,
        "max_ms": round(latency["max_ms"], 1) if latency["max_ms"] is not None else None,
        "analog_tags": result["analog_tags"],
        "estimated_missing": result["estimated_missing"],
        "clock_offset_ms": result["clock_offset_ms"],
        "clock_uncertainty_ms": result["clock_uncertainty_ms"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Barrido del intervalo de vaciado del gateway real")
    parser.add_argument(
        "--intervals", default="0.25,0.5,1.0", help="intervalos de vaciado en segundos, separados por coma"
    )
    parser.add_argument("--tags", type=int, default=200)
    parser.add_argument("--period", type=float, default=1.0, help="período de actualización del simulador (s)")
    parser.add_argument("--duration", type=float, default=240.0, help="segundos de gateway por intervalo")
    parser.add_argument("--warmup", type=float, default=30.0, help="segundos iniciales descartados del análisis")
    parser.add_argument("--cooldown", type=float, default=30.0, help="segundos finales descartados del análisis")
    parser.add_argument("--settle", type=float, default=40.0, help="espera a que termine la ingesta antes de leer (s)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-clock-skew", type=float, default=0.25)
    parser.add_argument("--table", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, default=Path("loadtest/results"))
    args = parser.parse_args()
    if "SITE_ID" not in os.environ:
        parser.error("defina las variables de entorno del gateway (SITE_ID, IOT_ENDPOINT, certificados...)")
    if port_open(OPC_PORT):
        parser.error(f"el puerto {OPC_PORT} ya está ocupado: detenga el simulador o el gateway que esté corriendo")

    clock = measure_clock()
    if clock.method != "sntp" or abs(clock.offset_s) > args.max_clock_skew:
        parser.error(
            f"reloj no verificable o desfasado ({clock.offset_s * 1000:+.0f} ms, {clock.method}); sincronice con NTP"
        )

    out_dir = args.out / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    table = boto3.resource(
        "dynamodb", config=Config(max_pool_connections=args.workers * 2, retries=READ_RETRIES)
    ).Table(args.table)
    simulator = subprocess.Popen(
        [sys.executable, "-m", "simulator.plant_simulator", "--tags", str(args.tags), "--period", str(args.period)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rows: list[dict[str, Any]] = []
    try:
        if not wait_for_port(OPC_PORT, 40):
            raise SystemExit("el simulador OPC UA no abrió el puerto a tiempo")
        for interval in [float(v) for v in args.intervals.split(",")]:
            print(f"Intervalo de vaciado {interval:g} s: {args.duration:.0f} s de gateway...", flush=True)
            result = run_interval(args, table, interval, out_dir)
            rows.append(summary_row(result))
            print(
                f"  p50 {rows[-1]['p50_ms']} ms | p95 {rows[-1]['p95_ms']} ms | {rows[-1]['messages_per_s']} mensajes/s | faltantes {rows[-1]['estimated_missing']}",
                flush=True,
            )
            with (out_dir / "summary.csv").open("w", newline="", encoding="utf8") as handle:
                writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
    finally:
        simulator.terminate()
        simulator.wait(timeout=30)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
