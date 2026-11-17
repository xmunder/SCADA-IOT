import asyncio

import pytest

from gateway.config import ConfigError, GatewayConfig
from gateway.opc_reader import Backoff, run_with_reconnect


def test_backoff_doubles_up_to_the_maximum_and_resets():
    backoff = Backoff(initial_s=1.0, max_s=5.0)
    assert [backoff.next_delay() for _ in range(5)] == [1.0, 2.0, 4.0, 5.0, 5.0]
    backoff.reset()
    assert backoff.next_delay() == 1.0


def test_reconnects_after_each_failure_and_waits_with_backoff():
    delays: list[float] = []
    attempts = 0

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    async def session() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise ConnectionError("servidor no disponible")
        raise asyncio.CancelledError  # fin de la prueba: el gateway se detiene

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_with_reconnect(session, Backoff(1.0, 30.0), sleep))

    assert attempts == 4
    assert delays == [1.0, 2.0, 4.0]


def test_a_successful_connection_resets_the_backoff():
    delays: list[float] = []
    backoff = Backoff(1.0, 30.0)
    attempts = 0

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    async def session() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            backoff.reset()  # la sesión llegó a conectar antes de caerse
        if attempts == 5:
            raise asyncio.CancelledError
        raise ConnectionError("caída")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_with_reconnect(session, backoff, sleep))

    assert delays == [1.0, 2.0, 1.0, 2.0]


def test_a_session_that_returns_is_also_reopened():
    attempts = 0

    async def sleep(_seconds: float) -> None:
        return None

    async def session() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_with_reconnect(session, Backoff(1.0, 30.0), sleep))

    assert attempts == 3


ENV = {
    "SITE_ID": "PLANTA-01",
    "OPC_ENDPOINT": "opc.tcp://localhost:4840",
    "OPC_NAMESPACE_URI": "urn:test",
    "IOT_ENDPOINT": "example.iot.us-east-2.amazonaws.com",
    "CERT_FILE": "c.pem",
    "KEY_FILE": "k.key",
    "CA_FILE": "ca.pem",
}


def test_reconnect_maximum_defaults_to_thirty_seconds_and_is_configurable():
    assert GatewayConfig.from_env(ENV).opc_reconnect_max_s == 30.0
    assert GatewayConfig.from_env({**ENV, "OPC_RECONNECT_MAX_S": "5"}).opc_reconnect_max_s == 5.0


def test_reconnect_maximum_must_be_positive():
    with pytest.raises(ConfigError):
        GatewayConfig.from_env({**ENV, "OPC_RECONNECT_MAX_S": "0"})


def test_cancellation_stops_the_loop_even_if_closing_the_session_raises():
    """Regresión: asyncua lanza un error de conexión al cerrar la sesión cancelada; no debe tomarse como caída."""
    attempts = 0

    async def sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    async def session() -> None:
        nonlocal attempts
        attempts += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ConnectionError("Connection is closed") from None

    async def scenario() -> bool:
        task = asyncio.create_task(run_with_reconnect(session, Backoff(1.0, 30.0), sleep))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.wait([task], timeout=1.0)
        return task.done()

    assert asyncio.run(scenario())
    assert attempts == 1
