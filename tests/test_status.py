"""Latido del gateway: contenido del mensaje de estado y tarea que lo publica."""

import asyncio
import json

import pytest

from gateway.config import ConfigError, GatewayConfig
from gateway.main import Stats, heartbeat
from gateway.payload import status_topic_for
from gateway.status import build_status, encode_status

ENV = {
    "SITE_ID": "PLANTA-01",
    "OPC_ENDPOINT": "opc.tcp://localhost:4840/x",
    "OPC_NAMESPACE_URI": "urn:x",
    "IOT_ENDPOINT": "x",
    "CERT_FILE": "c",
    "KEY_FILE": "k",
    "CA_FILE": "a",
}


class FakeBuffer:
    def __init__(self, pending: int) -> None:
        self._pending = pending

    def pending(self) -> int:
        return self._pending


class FakePublisher:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.messages: list[tuple[str, dict]] = []

    def is_connected(self) -> bool:
        return self.connected

    def publish(self, topic: str, payload: bytes) -> bool:
        self.messages.append((topic, json.loads(payload)))
        return True


class FakeReader:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


def test_status_topic_is_separate_from_the_telemetry_topic():
    assert status_topic_for("PLANTA-01") == "plant/PLANTA-01/status"


def test_status_message_carries_the_fields_the_alarms_need():
    stats = Stats(readings_in=120, readings_dropped=3, batches_buffered=5, batches_sent=4)
    status = build_status(
        "PLANTA-01", stats, pending=17, mqtt_connected=True, opc_connected=False, now_ms=1_700_000_000_000
    )
    assert status == {
        "type": "status",
        "site": "PLANTA-01",
        "ts_ms": 1_700_000_000_000,
        "pending": 17,
        "batches_sent": 4,
        "readings_in": 120,
        "readings_dropped": 3,
        "mqtt_connected": True,
        "opc_connected": False,
    }


def test_encoded_status_is_valid_compact_json():
    stats = Stats()
    raw = encode_status(build_status("S", stats, pending=0, mqtt_connected=True, opc_connected=True, now_ms=1))
    assert b" " not in raw
    assert json.loads(raw)["site"] == "S"


def test_heartbeat_publishes_status_on_its_own_topic_with_the_live_values():
    cfg = GatewayConfig.from_env({**ENV, "GATEWAY_STATUS_INTERVAL_S": "0.05"})
    publisher = FakePublisher(connected=True)
    stats = Stats(readings_in=10, batches_sent=2)

    async def scenario() -> None:
        task = asyncio.create_task(heartbeat(cfg, FakeBuffer(42), publisher, FakeReader(connected=True), stats))
        await asyncio.sleep(0.2)
        task.cancel()
        await asyncio.wait([task], timeout=1)

    asyncio.run(scenario())
    assert len(publisher.messages) >= 2
    topic, body = publisher.messages[0]
    assert topic == "plant/PLANTA-01/status"
    assert body["pending"] == 42 and body["opc_connected"] is True and body["mqtt_connected"] is True


def test_heartbeat_reports_a_lost_opc_ua_session():
    cfg = GatewayConfig.from_env({**ENV, "GATEWAY_STATUS_INTERVAL_S": "0.05"})
    publisher = FakePublisher()

    async def scenario() -> None:
        task = asyncio.create_task(heartbeat(cfg, FakeBuffer(0), publisher, FakeReader(connected=False), Stats()))
        await asyncio.sleep(0.12)
        task.cancel()
        await asyncio.wait([task], timeout=1)

    asyncio.run(scenario())
    assert publisher.messages[0][1]["opc_connected"] is False


def test_heartbeat_survives_a_failing_publisher():
    cfg = GatewayConfig.from_env({**ENV, "GATEWAY_STATUS_INTERVAL_S": "0.05"})

    class Broken(FakePublisher):
        def publish(self, topic: str, payload: bytes) -> bool:
            raise OSError("sin red")

    async def scenario() -> bool:
        task = asyncio.create_task(heartbeat(cfg, FakeBuffer(0), Broken(), FakeReader(True), Stats()))
        await asyncio.sleep(0.2)
        alive = not task.done()
        task.cancel()
        await asyncio.wait([task], timeout=1)
        return alive

    assert asyncio.run(scenario())


def test_status_interval_defaults_to_fifteen_seconds_and_must_be_positive():
    assert GatewayConfig.from_env(ENV).status_interval_s == 15.0
    with pytest.raises(ConfigError):
        GatewayConfig.from_env({**ENV, "GATEWAY_STATUS_INTERVAL_S": "0"})
