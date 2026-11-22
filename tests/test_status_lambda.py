"""Lambda de estado: convierte el latido del gateway en métricas EMF de CloudWatch."""

import io
import json
from contextlib import redirect_stdout

import status_app

from gateway.main import Stats
from gateway.status import build_status


def _status(**overrides):
    base = build_status(
        "PLANTA-01",
        Stats(readings_in=100, readings_dropped=2, batches_sent=9),
        pending=7,
        mqtt_connected=True,
        opc_connected=True,
        now_ms=1_700_000_000_000,
    )
    return {**base, **overrides}


def test_emf_declares_the_gateway_metrics_under_the_site_dimension():
    record = status_app.build_status_emf(_status(), timestamp_ms=1_700_000_005_000)
    declared = record["_aws"]["CloudWatchMetrics"][0]
    assert declared["Namespace"] == "ScadaIot" and declared["Dimensions"] == [["Site"]]
    assert [m["Name"] for m in declared["Metrics"]] == [
        "GatewayHeartbeat",
        "GatewayPendingBatches",
        "GatewayOpcConnected",
        "GatewayMqttConnected",
        "GatewayReadingsDropped",
    ]
    assert record["Site"] == "PLANTA-01"
    assert record["GatewayHeartbeat"] == 1
    assert record["GatewayPendingBatches"] == 7
    assert record["GatewayReadingsDropped"] == 2
    assert record["_aws"]["Timestamp"] == 1_700_000_005_000


def test_connection_flags_become_zero_or_one():
    record = status_app.build_status_emf(_status(opc_connected=False, mqtt_connected=True), timestamp_ms=1)
    assert record["GatewayOpcConnected"] == 0 and record["GatewayMqttConnected"] == 1


def test_handler_prints_one_emf_record_with_the_lambda_clock_not_the_gateway_clock():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = status_app.handler(_status(ts_ms=1), None)
    record = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert result == {"accepted": True}
    assert record["_aws"]["Timestamp"] > 1_600_000_000_000  # hora de la Lambda, no la del gateway


def test_handler_ignores_messages_that_are_not_a_status():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = status_app.handler({"site": "PLANTA-01", "readings": []}, None)
    assert result == {"accepted": False}
    assert buffer.getvalue() == ""


def test_handler_ignores_a_status_without_a_site():
    with redirect_stdout(io.StringIO()):
        assert status_app.handler({"type": "status"}, None) == {"accepted": False}
