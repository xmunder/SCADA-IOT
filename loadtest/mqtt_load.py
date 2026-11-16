"""Generador de carga MQTT hacia AWS IoT Core.

Publica lotes con la misma forma que el gateway a una tasa objetivo de lecturas
por segundo. Mide la latencia de PUBACK de cada mensaje y registra el intervalo
de la prueba para verificar después la pérdida en DynamoDB.

Uso (con las mismas variables de entorno del gateway):
    python -m loadtest.mqtt_load --rate 1000 --readings-per-msg 100 --duration 60 --run-name carga-1000
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from gateway.config import GatewayConfig
from gateway.payload import Reading, encode_batch, topic_for
from gateway.publisher import build_client
from loadtest.report import summarize_latencies

logger = logging.getLogger("loadtest")


def _now_ms() -> int:
    return int(time.time() * 1000)


def run_load(
    cfg: GatewayConfig,
    rate: float,
    readings_per_msg: int,
    duration_s: float,
    tag_count: int,
    drain_timeout_s: float = 60.0,
) -> tuple[dict, list[tuple[int, float]]]:
    sent_at: dict[int, float] = {}
    latencies: list[tuple[int, float]] = []
    counters = {"sent": 0, "errors": 0, "skipped": 0}
    tag_counts: dict[str, int] = {}

    def on_publish(_client, _userdata, mid, _reason_code, _properties) -> None:
        started = sent_at.pop(mid, None)
        if started is not None:
            latencies.append((mid, (time.perf_counter() - started) * 1000.0))

    client = build_client(cfg, on_publish=on_publish)
    topic = topic_for(cfg.site_id)
    interval = readings_per_msg / rate
    started_ms = _now_ms()
    t0 = time.perf_counter()
    message_index = 0

    try:
        while True:
            target = t0 + message_index * interval
            if target - t0 >= duration_s:
                break
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

            if not client.is_connected():
                counters["skipped"] += 1
                message_index += 1
                continue

            ts = _now_ms()
            readings = [
                Reading(
                    tag=f"Tag_{(message_index * readings_per_msg + j) % tag_count:04d}",
                    value=random.uniform(0.0, 100.0),
                    ts_ms=ts,
                )
                for j in range(readings_per_msg)
            ]
            info = client.publish(topic, encode_batch(cfg.site_id, readings), qos=1)
            if info.rc != 0:
                counters["errors"] += 1
            else:
                sent_at[info.mid] = time.perf_counter()
                counters["sent"] += 1
                for reading in readings:
                    tag_counts[reading.tag] = tag_counts.get(reading.tag, 0) + 1
            message_index += 1

        drain_deadline = time.perf_counter() + drain_timeout_s
        while sent_at and time.perf_counter() < drain_deadline:
            time.sleep(0.05)
    finally:
        ended_ms = _now_ms()
        client.loop_stop()
        client.disconnect()

    elapsed = time.perf_counter() - t0
    acked = len(latencies)
    summary = {
        "site": cfg.site_id,
        "target_rate_readings_s": rate,
        "readings_per_msg": readings_per_msg,
        "duration_s": duration_s,
        "elapsed_s": round(elapsed, 3),
        "started_ms": started_ms,
        "ended_ms": ended_ms,
        "msgs_sent": counters["sent"],
        "msgs_acked": acked,
        "msgs_unacked": counters["sent"] - acked,
        "publish_errors": counters["errors"],
        "skipped_disconnected": counters["skipped"],
        "readings_sent": counters["sent"] * readings_per_msg,
        "tag_counts": dict(sorted(tag_counts.items())),
        "achieved_rate_readings_s": round(counters["sent"] * readings_per_msg / elapsed, 2) if elapsed else 0,
        "publish_latency": summarize_latencies([ms for _, ms in latencies]),
    }
    return summary, latencies


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba de carga MQTT hacia AWS IoT Core")
    parser.add_argument("--rate", type=float, required=True, help="lecturas por segundo objetivo")
    parser.add_argument("--readings-per-msg", type=int, default=100)
    parser.add_argument("--duration", type=float, default=60.0, help="segundos de publicación")
    parser.add_argument("--tags", type=int, default=200, help="número de tags distintos a simular")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--out", default="loadtest/results")
    args = parser.parse_args()
    if args.rate <= 0 or args.readings_per_msg <= 0 or args.duration <= 0 or args.tags <= 0:
        parser.error("rate, readings-per-msg, duration y tags deben ser mayores que cero")

    if args.readings_per_msg > args.tags:
        parser.error(
            "readings-per-msg no puede superar tags: un tag repetido en el mismo lote colisiona en la clave (serie, tiempo)"
        )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = GatewayConfig.from_env()
    summary, latencies = run_load(cfg, args.rate, args.readings_per_msg, args.duration, args.tags)

    out_dir = Path(args.out) / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    summary["run_name"] = args.run_name
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
    with (out_dir / "latencies.csv").open("w", newline="", encoding="utf8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mid", "publish_latency_ms"])
        writer.writerows(latencies)

    logger.info("Resultados en %s", out_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
