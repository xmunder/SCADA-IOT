"""Integración local sin AWS: simulador OPC UA real -> gateway -> lógica de la Lambda.

Se usa el servidor OPC UA y el cliente reales; solo el publicador MQTT se sustituye por uno que entrega el
mensaje a la lógica de ingesta de la Lambda. La prueba cubre también la caída y el reinicio del servidor.
"""

import asyncio
import json
import socket
import time

import app as ingest
import pytest

from gateway import opc_reader
from gateway.config import GatewayConfig
from gateway.main import run_gateway
from simulator.plant_simulator import run as run_simulator

pytestmark = pytest.mark.integration

TAGS = 6
PERIOD_S = 0.2


class LambdaPublisher:
    """Sustituye al cliente MQTT: cada mensaje publicado se procesa como lo haría la Lambda."""

    items: list[dict] = []

    def __init__(self, _cfg) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def publish(self, _topic: str, payload: bytes) -> bool:
        items, _rejected = ingest.build_items(json.loads(payload), int(time.time() * 1000))
        LambdaPublisher.items.extend(items)
        return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_until(condition, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"tiempo agotado esperando: {what}")
        await asyncio.sleep(0.1)


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.wait([task], timeout=10)


def test_readings_flow_end_to_end_and_survive_an_opc_ua_server_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.main.IotPublisher", LambdaPublisher)
    monkeypatch.setattr(opc_reader, "HEALTH_CHECK_INTERVAL_S", 0.2)
    LambdaPublisher.items = []
    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/scada-iot/simulador"
    cfg = GatewayConfig.from_env(
        {
            "SITE_ID": "PLANTA-01",
            "OPC_ENDPOINT": endpoint,
            "OPC_NAMESPACE_URI": "urn:scada-iot:planta",
            "IOT_ENDPOINT": "unused",
            "CERT_FILE": "unused",
            "KEY_FILE": "unused",
            "CA_FILE": "unused",
            "BUFFER_PATH": str(tmp_path / "buffer.db"),
            "GATEWAY_FLUSH_INTERVAL_S": "0.2",
            "OPC_RECONNECT_MAX_S": "1",
        }
    )

    async def scenario() -> None:
        simulator = asyncio.create_task(
            run_simulator(f"opc.tcp://127.0.0.1:{port}/scada-iot/simulador", TAGS, PERIOD_S)
        )
        await asyncio.sleep(1.0)  # el servidor tarda en abrir el puerto
        gateway = asyncio.create_task(run_gateway(cfg))
        try:
            await _wait_until(lambda: len({i["tag"] for i in LambdaPublisher.items}) == TAGS, 15, "los 6 tags")
            first = LambdaPublisher.items[0]
            assert first["series"] == f"PLANTA-01#{first['tag']}"

            await _stop(simulator)  # cae el servidor OPC UA
            await asyncio.sleep(1.5)
            assert not gateway.done(), "el gateway no debe detenerse si cae el servidor OPC UA"
            restart_ms = int(time.time() * 1000)
            before = len(LambdaPublisher.items)

            simulator = asyncio.create_task(
                run_simulator(f"opc.tcp://127.0.0.1:{port}/scada-iot/simulador", TAGS, PERIOD_S)
            )
            await _wait_until(
                lambda: any(i["ts_ms"] > restart_ms for i in LambdaPublisher.items[before:]),
                30,
                "lecturas nuevas tras reiniciar el servidor",
            )
        finally:
            await _stop(gateway)
            await _stop(simulator)
        assert gateway.done(), "el gateway debe detenerse al cancelarlo"

    asyncio.run(scenario())
