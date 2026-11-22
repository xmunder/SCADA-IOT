"""Recolección de métricas de la nube para una ventana de prueba (consultas y resumen, sin AWS)."""

import pytest

from loadtest.collect_cloud_metrics import build_queries, summarize

NAMES = {"function": "scada-ingest-fn", "table": "scada_telemetry_q", "queue": "ingest-q", "dlq": "ingest-dlq"}


def _ids(queries):
    return {q["Id"] for q in queries}


def test_queries_cover_lambda_dynamodb_and_both_queues_in_queue_mode():
    queries = build_queries(**NAMES)
    assert _ids(queries) == {
        "lambda_errors",
        "lambda_throttles",
        "lambda_invocations",
        "ddb_write_throttles",
        "queue_age",
        "queue_depth",
        "dlq_depth",
    }
    age = next(q for q in queries if q["Id"] == "queue_age")["MetricStat"]
    assert age["Metric"]["Namespace"] == "AWS/SQS"
    assert age["Metric"]["Dimensions"] == [{"Name": "QueueName", "Value": "ingest-q"}]
    assert age["Stat"] == "Maximum"


def test_queries_omit_the_queue_metrics_when_there_is_no_queue():
    queries = build_queries(function="f", table="t", queue=None, dlq=None)
    assert _ids(queries) == {"lambda_errors", "lambda_throttles", "lambda_invocations", "ddb_write_throttles"}


def test_counts_are_summed_and_depth_and_age_take_the_maximum():
    results = [
        {"Id": "lambda_errors", "Timestamps": [1, 2], "Values": [0.0, 3.0]},
        {"Id": "lambda_throttles", "Timestamps": [1, 2], "Values": [10.0, 5.0]},
        {"Id": "lambda_invocations", "Timestamps": [1, 2], "Values": [100.0, 200.0]},
        {"Id": "ddb_write_throttles", "Timestamps": [1], "Values": [7.0]},
        {"Id": "queue_age", "Timestamps": [1, 2, 3], "Values": [0.0, 95.0, 12.0]},
        {"Id": "queue_depth", "Timestamps": [1, 2], "Values": [40.0, 900.0]},
        {"Id": "dlq_depth", "Timestamps": [1], "Values": [0.0]},
    ]
    summary = summarize(results)
    assert summary["lambda_errors"] == 3
    assert summary["lambda_throttles"] == 15
    assert summary["lambda_invocations"] == 300
    assert summary["ddb_write_throttles"] == 7
    assert summary["queue_age_max_s"] == 95
    assert summary["queue_depth_max"] == 900
    assert summary["dlq_depth_max"] == 0


def test_a_metric_without_datapoints_counts_as_zero_not_as_missing():
    summary = summarize([{"Id": "lambda_errors", "Timestamps": [], "Values": []}])
    assert summary["lambda_errors"] == 0
    assert "queue_age_max_s" not in summary  # no hubo consulta de cola


def test_a_window_that_ends_before_it_starts_is_rejected():
    from loadtest.collect_cloud_metrics import check_window

    with pytest.raises(ValueError):
        check_window(start_ms=2_000, end_ms=1_000)
    check_window(start_ms=1_000, end_ms=2_000)
