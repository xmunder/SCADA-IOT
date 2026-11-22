from decimal import Decimal

from app import build_items


def _event(readings):
    return {"site": "PLANTA-01", "readings": readings}


def test_good_reading_produces_one_item_with_value_and_latency():
    items, rejected = build_items(
        _event([{"tag": "Tag_0001", "value": 12.5, "ts_ms": 1_000, "quality": "GOOD"}]), received_ms=1_250
    )
    assert rejected == 0
    assert len(items) == 1
    item = items[0]
    assert item["series"] == "PLANTA-01#Tag_0001"
    assert item["ts_ms"] == 1_000
    assert item["value"] == Decimal("12.5")
    assert item["ingest_latency_ms"] == 250
    assert item["site"] == "PLANTA-01" and item["tag"] == "Tag_0001"


def test_bad_quality_is_rejected_and_not_written():
    items, rejected = build_items(
        _event([{"tag": "Tag_0002", "value": 1.0, "ts_ms": 1, "quality": "BAD"}]), received_ms=2
    )
    assert items == []
    assert rejected == 1


def test_malformed_reading_is_counted_as_rejected():
    items, rejected = build_items(
        _event([{"tag": "Tag_0003"}, {"tag": "x", "value": "no-numero", "ts_ms": 1, "quality": "GOOD"}]), received_ms=2
    )
    assert items == []
    assert rejected == 2


def test_latency_is_stored_raw_even_when_negative_so_it_can_be_corrected_for_clock_skew():
    # reloj de origen adelantado 1 s: la latencia cruda es negativa y NO debe recortarse a 0
    items, _ = build_items(_event([{"tag": "T", "value": 1.0, "ts_ms": 5_000, "quality": "GOOD"}]), received_ms=4_000)
    assert items[0]["ingest_latency_ms"] == -1000


def test_emf_latency_metric_is_not_negative_but_stored_value_stays_raw():
    from app import handler

    class FakeTable:
        def __init__(self):
            self.items = []

        def batch_writer(self, **_kwargs):
            table = self

            class Writer:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def put_item(self_inner, Item):
                    table.items.append(Item)

            return Writer()

    import io
    import json
    from contextlib import redirect_stdout

    import app

    fake = FakeTable()
    app._table = fake
    event = _event([{"tag": "T", "value": 1.0, "ts_ms": 4_102_444_800_000, "quality": "GOOD"}])  # año 2100: negativa
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        handler(event, None)
    app._table = None
    assert fake.items[0]["ingest_latency_ms"] < 0
    emf = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert emf["IngestLatencyMs"] == [0]


def test_emf_record_declares_metrics_and_latency_values():
    from app import build_emf

    record = build_emf("PLANTA-01", written=3, rejected=1, latencies_ms=[10, 20, 30], timestamp_ms=1_000)
    declared = record["_aws"]["CloudWatchMetrics"][0]
    assert declared["Namespace"] == "ScadaIot" and declared["Dimensions"] == [["Site"]]
    assert [m["Name"] for m in declared["Metrics"]] == ["ReadingsWritten", "ReadingsRejected", "IngestLatencyMs"]
    assert record["Site"] == "PLANTA-01" and record["ReadingsWritten"] == 3
    assert record["IngestLatencyMs"] == [10, 20, 30]


def test_emf_caps_latency_values_at_100_keeping_the_spread():
    from app import build_emf

    record = build_emf("S", written=250, rejected=0, latencies_ms=list(range(250)), timestamp_ms=1)
    values = record["IngestLatencyMs"]
    assert len(values) <= 100
    assert values[0] == 0 and values[-1] >= 200


def test_emf_without_latencies_does_not_declare_the_latency_metric():
    from app import build_emf

    record = build_emf("S", written=0, rejected=2, latencies_ms=[], timestamp_ms=1)
    assert "IngestLatencyMs" not in record
    assert [m["Name"] for m in record["_aws"]["CloudWatchMetrics"][0]["Metrics"]] == [
        "ReadingsWritten",
        "ReadingsRejected",
    ]


def _run_handler(event):
    import io
    from contextlib import redirect_stdout

    import app

    app._table = object()  # no debe usarse: un mensaje inválido no escribe nada
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = app.handler(event, None)
    finally:
        app._table = None
    return result, buffer.getvalue()


def test_a_message_without_site_is_counted_as_rejected_instead_of_failing_the_invocation():
    """Regresión: {"check": "policy"} provocaba KeyError y IoT Core reintentaba la invocación dos veces."""
    result, output = _run_handler({"check": "policy"})
    assert result == {"written": 0, "rejected": 1}
    assert '"ReadingsRejected": 1' in output


def test_readings_that_are_not_a_list_or_not_objects_are_rejected_not_fatal():
    items, rejected = build_items({"site": "PLANTA-01", "readings": "no-es-una-lista"}, received_ms=1)
    assert items == [] and rejected == 1
    items, rejected = build_items({"site": "PLANTA-01", "readings": ["x", 3, None]}, received_ms=1)
    assert items == [] and rejected == 3
