import asyncio
from types import SimpleNamespace

from gateway.main import Stats, batcher
from gateway.payload import Reading


class FakeBuffer:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def append(self, payload: bytes, _ts_ms: int) -> None:
        self.payloads.append(payload)


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(site_id="PLANTA-01", batch_size=100, flush_interval_s=0.05)


def test_batcher_flushes_readings_that_arrive_after_an_idle_period():
    """Regresión: tras estar inactivo más que flush_interval_s, el batcher dejaba de consumir la cola."""

    async def scenario() -> tuple[Stats, int]:
        queue: asyncio.Queue[Reading] = asyncio.Queue()
        buffer = FakeBuffer()
        stats = Stats()
        task = asyncio.create_task(batcher(_cfg(), queue, buffer, stats))
        await asyncio.sleep(0.3)  # inactividad: seis veces el intervalo de vaciado
        for i in range(10):
            queue.put_nowait(Reading(tag="Tag_0001", value=1.0, ts_ms=i))
        await asyncio.sleep(0.3)
        task.cancel()
        return stats, queue.qsize()

    stats, left = asyncio.run(scenario())
    assert left == 0
    assert stats.batches_buffered == 1


def test_batcher_splits_large_backlog_into_batches_of_batch_size():
    async def scenario() -> Stats:
        queue: asyncio.Queue[Reading] = asyncio.Queue()
        stats = Stats()
        for i in range(250):
            queue.put_nowait(Reading(tag="Tag_0001", value=1.0, ts_ms=i))
        task = asyncio.create_task(batcher(_cfg(), queue, FakeBuffer(), stats))
        await asyncio.sleep(0.3)
        task.cancel()
        return stats

    assert asyncio.run(scenario()).batches_buffered == 3
