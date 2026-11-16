"""Prueba causal de la hipótesis de la tabla fría: escrituras directas a una tabla DynamoDB recién creada.

La matriz de carga mostró que la primera corrida a 5,000 tags se saturó (p95 de 144 s) y las siguientes no.
Para aislar la causa, este script crea una tabla bajo demanda NUEVA y le escribe a una tasa fija durante
varias fases consecutivas, sin IoT Core ni Lambda de por medio. Si DynamoDB limita las escrituras de una tabla
fría y deja de hacerlo tras el primer pico, la fracción de items rechazados caerá entre la fase 1 y la fase 2.

Los reintentos del SDK se desactivan para observar la limitación en lugar de ocultarla. La tabla se elimina al
terminar (``--keep`` la conserva).

Uso (con las credenciales y la región de la cuenta):
    python -m loadtest.ddb_cold_test --rate 5000 --phase-seconds 150 --phases 2
"""

from __future__ import annotations

import argparse
import itertools
import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

BATCH_SIZE = 25  # límite de BatchWriteItem


def window_stats(records: list[tuple[int, int, int]], window_s: int = 10) -> list[dict[str, Any]]:
    """Agrupa (segundo, intentados, rechazados) por ventanas y calcula la fracción rechazada."""
    buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for second, attempted, throttled in records:
        bucket = buckets[second // window_s]
        bucket[0] += attempted
        bucket[1] += throttled
    result = []
    for index in sorted(buckets):
        attempted, throttled = buckets[index]
        result.append(
            {
                "from_s": index * window_s,
                "attempted": attempted,
                "served": attempted - throttled,
                "throttled": throttled,
                "served_per_s": round((attempted - throttled) / window_s, 1),
                "throttled_pct": round(100.0 * throttled / attempted, 2) if attempted else None,
            }
        )
    return result


def phase_summary(records: list[tuple[int, int, int]]) -> dict[str, Any]:
    attempted = sum(r[1] for r in records)
    throttled = sum(r[2] for r in records)
    return {
        "attempted": attempted,
        "served": attempted - throttled,
        "throttled": throttled,
        "throttled_pct": round(100.0 * throttled / attempted, 2) if attempted else None,
    }


def make_item(n: int, base_ms: int, tags: int) -> dict[str, Any]:
    tag = n % tags
    return {
        "series": {"S": f"COLD#Tag_{tag:04d}"},
        "ts_ms": {"N": str(base_ms + (n // tags) * 1000)},
        "site": {"S": "COLD"},
        "tag": {"S": f"Tag_{tag:04d}"},
        "value": {"N": "12.5"},
        "ingest_latency_ms": {"N": "120"},
    }


def run_phase(
    client: Any, table: str, rate: float, seconds: float, workers: int, counter: Any, base_ms: int, tags: int
) -> list[tuple[int, int, int]]:
    records: list[tuple[int, int, int]] = []
    lock = threading.Lock()
    batches_per_s = rate / BATCH_SIZE
    interval = workers / batches_per_s  # cada trabajador envía un lote cada ``interval`` segundos
    start = time.monotonic()

    def worker(offset: float) -> None:
        next_at = start + offset
        while True:
            now = time.monotonic()
            if next_at - start >= seconds:
                return
            if next_at > now:
                time.sleep(next_at - now)
            next_at += interval
            with lock:
                first = next(counter)
                for _ in range(BATCH_SIZE - 1):
                    next(counter)
            items = [{"PutRequest": {"Item": make_item(first + i, base_ms, tags)}} for i in range(BATCH_SIZE)]
            throttled = 0
            try:
                response = client.batch_write_item(RequestItems={table: items})
                throttled = sum(len(v) for v in response.get("UnprocessedItems", {}).values())
            except ClientError as err:
                if err.response["Error"]["Code"] in (
                    "ProvisionedThroughputExceededException",
                    "ThrottlingException",
                    "RequestLimitExceeded",
                ):
                    throttled = BATCH_SIZE
                else:
                    raise
            with lock:
                records.append((int(time.monotonic() - start), BATCH_SIZE, throttled))

    threads = [threading.Thread(target=worker, args=(i * interval / workers,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba de tabla fría en DynamoDB")
    parser.add_argument("--rate", type=float, default=5000.0, help="items por segundo")
    parser.add_argument("--phase-seconds", type=float, default=150.0)
    parser.add_argument("--phases", type=int, default=2)
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument("--tags", type=int, default=5000)
    parser.add_argument("--keep", action="store_true", help="no eliminar la tabla al terminar")
    parser.add_argument("--out", type=Path, default=Path("loadtest/results/cold-table-test"))
    args = parser.parse_args()

    name = f"scada_cold_test_{int(time.time())}"
    client = boto3.client(
        "dynamodb",
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, max_pool_connections=args.workers * 2),
    )
    client.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "series", "AttributeType": "S"},
            {"AttributeName": "ts_ms", "AttributeType": "N"},
        ],
        KeySchema=[{"AttributeName": "series", "KeyType": "HASH"}, {"AttributeName": "ts_ms", "KeyType": "RANGE"}],
    )
    client.get_waiter("table_exists").wait(TableName=name)
    created_ms = int(time.time() * 1000)
    print(f"Tabla {name} creada y activa; comienza la fase 1 sobre una tabla fría")

    counter = itertools.count()
    base_ms = int(time.time() * 1000)
    report: dict[str, Any] = {
        "table": name,
        "rate_target": args.rate,
        "phase_seconds": args.phase_seconds,
        "phases": [],
    }
    try:
        for phase in range(1, args.phases + 1):
            started_ms = int(time.time() * 1000)
            records = run_phase(client, name, args.rate, args.phase_seconds, args.workers, counter, base_ms, args.tags)
            summary = phase_summary(records)
            windows = window_stats(records)
            report["phases"].append(
                {
                    "phase": phase,
                    "started_ms": started_ms,
                    "since_creation_s": round((started_ms - created_ms) / 1000, 1),
                    **summary,
                    "windows": windows,
                }
            )
            print(
                f"Fase {phase}: intentados={summary['attempted']} rechazados={summary['throttled']} ({summary['throttled_pct']} %)"
            )
            print(
                "   ventana(s)  servidos/s  rechazados %  -> "
                + "  ".join(f"{w['from_s']}:{w['served_per_s']:.0f}/{w['throttled_pct']}%" for w in windows)
            )
    finally:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        if not args.keep:
            client.delete_table(TableName=name)
            print(f"Tabla {name} eliminada")
    print(
        json.dumps(
            {
                p["phase"]: {k: p[k] for k in ("attempted", "served", "throttled", "throttled_pct")}
                for p in report["phases"]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
