"""Recoge de CloudWatch las métricas de la nube durante la ventana de una prueba.

Uso (con credenciales de AWS y la región del stack):
    python -m loadtest.collect_cloud_metrics --stack scada-iot-ingest --table scada_telemetry \
        --start-ms 1700000000000 --end-ms 1700000300000 --out loadtest/results/mi-prueba/cloud_metrics.json

Trae, por minuto: errores, limitaciones e invocaciones de la Lambda de ingesta, escrituras limitadas de DynamoDB y, si el
stack tiene cola, la edad del mensaje más antiguo y la profundidad de la cola y de la cola de fallidos. Guarda la serie y un
resumen (sumas de los conteos y máximos de edad y profundidad). Sirve para comparar el modo direct con el modo queue.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

PERIOD_S = 60
# Los máximos se resumen con el máximo; los conteos, con la suma.
MAX_METRICS = {"queue_age": "queue_age_max_s", "queue_depth": "queue_depth_max", "dlq_depth": "dlq_depth_max"}
SUM_METRICS = ("lambda_errors", "lambda_throttles", "lambda_invocations", "ddb_write_throttles")


def check_window(start_ms: int, end_ms: int) -> None:
    if end_ms <= start_ms:
        raise ValueError("--end-ms debe ser mayor que --start-ms")


def _query(query_id: str, namespace: str, metric: str, dimension: tuple[str, str], stat: str) -> dict[str, Any]:
    return {
        "Id": query_id,
        "MetricStat": {
            "Metric": {
                "Namespace": namespace,
                "MetricName": metric,
                "Dimensions": [{"Name": dimension[0], "Value": dimension[1]}],
            },
            "Period": PERIOD_S,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def build_queries(function: str, table: str, queue: str | None, dlq: str | None) -> list[dict[str, Any]]:
    queries = [
        _query("lambda_errors", "AWS/Lambda", "Errors", ("FunctionName", function), "Sum"),
        _query("lambda_throttles", "AWS/Lambda", "Throttles", ("FunctionName", function), "Sum"),
        _query("lambda_invocations", "AWS/Lambda", "Invocations", ("FunctionName", function), "Sum"),
        _query("ddb_write_throttles", "AWS/DynamoDB", "WriteThrottleEvents", ("TableName", table), "Sum"),
    ]
    if queue:
        queries.append(_query("queue_age", "AWS/SQS", "ApproximateAgeOfOldestMessage", ("QueueName", queue), "Maximum"))
        queries.append(
            _query("queue_depth", "AWS/SQS", "ApproximateNumberOfMessagesVisible", ("QueueName", queue), "Maximum")
        )
    if dlq:
        queries.append(
            _query("dlq_depth", "AWS/SQS", "ApproximateNumberOfMessagesVisible", ("QueueName", dlq), "Maximum")
        )
    return queries


def summarize(results: list[dict[str, Any]]) -> dict[str, float]:
    """Resume la serie: suma los conteos y toma el máximo de la edad y la profundidad. Sin datos cuenta como 0."""
    summary: dict[str, float] = {}
    for result in results:
        values = [float(v) for v in result.get("Values", [])]
        key = result["Id"]
        if key in SUM_METRICS:
            summary[key] = sum(values)
        elif key in MAX_METRICS:
            summary[MAX_METRICS[key]] = max(values) if values else 0.0
    return summary


def _resolve(stack: str, region: str | None) -> dict[str, str]:
    """Nombres físicos de la función y de las colas a partir de los recursos lógicos del stack."""
    cfn = boto3.client("cloudformation", region_name=region)
    found = {}
    for logical, key in (("IngestFunction", "function"), ("IngestQueue", "queue"), ("IngestDeadLetterQueue", "dlq")):
        detail = cfn.describe_stack_resource(StackName=stack, LogicalResourceId=logical)["StackResourceDetail"]
        physical = detail["PhysicalResourceId"]
        # El id físico de una cola SQS es su URL; el nombre es el último segmento.
        found[key] = physical.rsplit("/", 1)[-1] if key != "function" else physical
    return found


def collect(
    start_ms: int, end_ms: int, function: str, table: str, queue: str | None, dlq: str | None, region: str | None
):
    check_window(start_ms, end_ms)
    client = boto3.client("cloudwatch", region_name=region)
    # Un minuto de margen al final: CloudWatch publica los puntos con un pequeño retraso.
    kwargs = {
        "MetricDataQueries": build_queries(function, table, queue, dlq),
        "StartTime": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
        "EndTime": datetime.fromtimestamp((end_ms + 60_000) / 1000, tz=timezone.utc),
        "ScanBy": "TimestampAscending",
    }
    results: list[dict[str, Any]] = []
    while True:
        response = client.get_metric_data(**kwargs)
        results.extend(response["MetricDataResults"])
        if not response.get("NextToken"):
            break
        kwargs["NextToken"] = response["NextToken"]
    merged: dict[str, dict[str, Any]] = {}
    for part in results:  # get_metric_data pagina cada serie por separado: se vuelven a unir
        entry = merged.setdefault(part["Id"], {"Id": part["Id"], "Timestamps": [], "Values": []})
        entry["Timestamps"].extend(t.isoformat() for t in part["Timestamps"])
        entry["Values"].extend(part["Values"])
    return list(merged.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Métricas de la nube durante una prueba")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, help="por defecto, ahora")
    parser.add_argument(
        "--stack", default="scada-iot-ingest", help="stack del que se leen los nombres de la función y las colas"
    )
    parser.add_argument("--table", required=True)
    parser.add_argument("--no-queue", action="store_true", help="el stack está en modo direct: sin métricas de cola")
    parser.add_argument("--region", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    end_ms = args.end_ms or int(time.time() * 1000)
    names = _resolve(args.stack, args.region)
    queue, dlq = (None, None) if args.no_queue else (names["queue"], names["dlq"])
    series = collect(args.start_ms, end_ms, names["function"], args.table, queue, dlq, args.region)
    report = {
        "start_ms": args.start_ms,
        "end_ms": end_ms,
        "stack": args.stack,
        "summary": summarize(series),
        "series": series,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
