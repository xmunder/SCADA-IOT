"""Pruebas del agrupador y del reenviador con un publicador simulado (sin red)."""

import asyncio
import json

from gateway.buffer import StoreAndForwardBuffer
from gateway.config import GatewayConfig
from gateway.main import Stats, batcher, forwarder
from gateway.payload import Reading

ENV = {
    "SITE_ID": "PLANTA-01",
    "OPC_ENDPOINT": "opc.tcp://localhost:4840/x",
    "OPC_NAMESPACE_URI": "urn:x",
    "IOT_ENDPOINT": "x",
    "CERT_FILE": "c",
    "KEY_FILE": "k",
    "CA_FILE": "a",
}


def _cfg(tmp_path, batch_size=10, flush=0.2, window=4):
    env = dict(
        ENV,
        BUFFER_PATH=str(tmp_path / "b.db"),
        GATEWAY_BATCH_SIZE=str(batch_size),
        GATEWAY_FLUSH_INTERVAL_S=str(flush),
        GATEWAY_INFLIGHT_WINDOW=str(window),
    )
    return GatewayConfig.from_env(env)


class FakePublisher:
    """Acepta los mensajes cuyo número de secuencia no esté en `fail_on`."""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.published = []

    def publish(self, topic, payload):
        seq = json.loads(payload)["seq"]
        self.published.append(seq)
        return seq not in self.fail_on


def test_batcher_splits_into_batches_of_configured_size(tmp_path):
    cfg = _cfg(tmp_path, batch_size=10, flush=0.2)
    buf = StoreAndForwardBuffer(cfg.buffer_path)
    stats = Stats()

    async def scenario():
        queue = asyncio.Queue()
        for i in range(25):
            queue.put_nowait(Reading(tag=f"T{i}", value=float(i), ts_ms=i))
        task = asyncio.create_task(batcher(cfg, queue, buffer=buf, stats=stats))
        # 25 lecturas: dos lotes completos de 10 y un lote de 5 que se vacía al cumplirse el intervalo.
        await asyncio.sleep(0.6)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    sizes = [len(json.loads(p)["readings"]) for _, p in buf.peek(10)]
    assert sizes == [10, 10, 5]
    buf.close()


def test_forwarder_acks_only_confirmed_messages(tmp_path):
    cfg = _cfg(tmp_path, window=10)
    buf = StoreAndForwardBuffer(cfg.buffer_path)
    for seq in range(4):
        buf.append(json.dumps({"seq": seq}).encode(), seq)
    publisher = FakePublisher(fail_on={1, 3})
    stats = Stats()

    async def scenario():
        task = asyncio.create_task(forwarder(cfg, buf, publisher, stats))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert buf.pending() == 2  # los mensajes 1 y 3 siguen en el buffer
    remaining = [json.loads(p)["seq"] for _, p in buf.peek(10)]
    assert remaining == [1, 3]
    assert stats.batches_sent == 2
    buf.close()
