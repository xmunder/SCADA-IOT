"""Contrato entre el gateway (productor) y la Lambda de ingesta (consumidor).

Cada lado se prueba por separado en otros archivos; aquí se verifica que sigan alineados: lo que el
gateway serializa es exactamente lo que la Lambda sabe leer, y el tema publicado coincide con la regla de IoT Core.
"""

import json
import re
from pathlib import Path

import app as ingest
import pytest

from gateway.payload import BAD, GOOD, Reading, encode_batch, topic_for

TEMPLATE = Path(__file__).resolve().parents[1] / "infra" / "template.yaml"


def _topic_matches(topic_filter: str, topic: str) -> bool:
    """Coincidencia de filtros MQTT con el comodín de un nivel '+'."""
    filter_levels, topic_levels = topic_filter.split("/"), topic.split("/")
    return len(filter_levels) == len(topic_levels) and all(
        f in ("+", t) for f, t in zip(filter_levels, topic_levels, strict=True)
    )


def test_lambda_stores_every_good_reading_the_gateway_serializes():
    readings = [
        Reading(tag="Tag_0000", value=51.5, ts_ms=1_700_000_000_000),
        Reading(tag="Tag_0001", value=7.0, ts_ms=1_700_000_000_500),
        Reading(tag="Tag_0002", value=1.0, ts_ms=1_700_000_001_000),
    ]
    event = json.loads(encode_batch("PLANTA-01", readings))

    items, rejected = ingest.build_items(event, received_ms=1_700_000_002_000)

    assert rejected == 0
    assert [(i["series"], i["ts_ms"], float(i["value"])) for i in items] == [
        (f"PLANTA-01#{r.tag}", r.ts_ms, r.value) for r in readings
    ]
    assert all(i["site"] == "PLANTA-01" and "ingest_latency_ms" in i for i in items)


def test_lambda_rejects_what_the_gateway_marks_as_bad_quality():
    event = json.loads(
        encode_batch(
            "PLANTA-01",
            [Reading("Tag_0000", 1.0, 10, GOOD), Reading("Tag_0001", 2.0, 11, BAD)],
        )
    )

    items, rejected = ingest.build_items(event, received_ms=20)

    assert [i["tag"] for i in items] == ["Tag_0000"]
    assert rejected == 1


def test_batch_keys_are_the_ones_the_lambda_reads():
    event = json.loads(encode_batch("PLANTA-01", [Reading("Tag_0000", 1.0, 10)]))
    assert set(event) == {"site", "readings"}
    assert set(event["readings"][0]) == {"tag", "value", "ts_ms", "quality"}


@pytest.mark.parametrize("site_id", ["PLANTA-01", "SITE_2", "p1"])
def test_published_topic_matches_the_iot_rule_in_the_template(site_id):
    sql = re.search(r'Sql:\s*"SELECT \* FROM \'([^\']+)\'"', TEMPLATE.read_text(encoding="utf8"))
    assert sql, "la plantilla ya no define la regla de IoT Core esperada"
    assert _topic_matches(sql.group(1), topic_for(site_id))


@pytest.mark.parametrize("site_id", ["PLANTA-01", "SITE_2", "p1"])
def test_status_topic_matches_the_status_rule_in_the_template(site_id):
    from gateway.payload import status_topic_for

    sql = re.search(r"Sql:\s*\"SELECT \* FROM '(plant/\+/status)'\"", TEMPLATE.read_text(encoding="utf8"))
    assert sql, "la plantilla ya no define la regla de estado esperada"
    assert _topic_matches(sql.group(1), status_topic_for(site_id))
    assert not _topic_matches(sql.group(1), topic_for(site_id))  # la regla de estado no recibe telemetría


def test_the_ingest_lambda_reads_a_gateway_batch_delivered_through_sqs():
    from tests.test_ingest_sqs import FakeTable

    readings = [Reading("Tag_0000", 1.5, 1_700_000_000_000), Reading("Tag_0001", 2.5, 1_700_000_000_500)]
    body = encode_batch("PLANTA-01", readings).decode("utf-8")  # la regla de IoT deja el mensaje tal cual en la cola
    table = FakeTable()
    ingest._table = table
    try:
        result = ingest.handler({"Records": [{"messageId": "m1", "body": body}]}, None)
    finally:
        ingest._table = None
    assert result == {"batchItemFailures": []}
    assert sorted(i["series"] for i in table.items) == ["PLANTA-01#Tag_0000", "PLANTA-01#Tag_0001"]
