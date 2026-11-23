"""Punto de entrada del gateway: OPC UA -> lotes -> buffer SQLite -> AWS IoT Core."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from gateway.buffer import StoreAndForwardBuffer
from gateway.config import GatewayConfig
from gateway.opc_reader import OpcReader
from gateway.payload import Reading, encode_batch, status_topic_for, topic_for
from gateway.publisher import IotPublisher
from gateway.status import build_status, encode_status

logger = logging.getLogger("gateway")
QUEUE_MAXSIZE = 100_000


@dataclass
class Stats:
    readings_in: int = 0
    readings_dropped: int = 0
    batches_buffered: int = 0
    batches_sent: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


async def batcher(
    cfg: GatewayConfig,
    queue: asyncio.Queue[Reading],
    buffer: StoreAndForwardBuffer,
    stats: Stats,
) -> None:
    """Agrupa lecturas en lotes y los escribe en el buffer antes de confirmar nada."""
    pending: list[Reading] = []
    last_flush = time.monotonic()
    while True:
        if not pending:
            # Sin lecturas pendientes no hay nada que vaciar: reiniciar el reloj evita que el
            # tiempo de espera llegue a 0, lo que cancelaba queue.get() antes de ejecutarse.
            last_flush = time.monotonic()
        remaining = max(0.0, cfg.flush_interval_s - (time.monotonic() - last_flush))
        try:
            pending.append(await asyncio.wait_for(queue.get(), timeout=remaining))
            while not queue.empty() and len(pending) < cfg.batch_size:
                pending.append(queue.get_nowait())
        except asyncio.TimeoutError:
            pass

        due = time.monotonic() - last_flush >= cfg.flush_interval_s
        if pending and (len(pending) >= cfg.batch_size or due):
            while pending:
                chunk, pending = pending[: cfg.batch_size], pending[cfg.batch_size :]
                buffer.append(encode_batch(cfg.site_id, chunk), _now_ms())
                stats.batches_buffered += 1
            last_flush = time.monotonic()


async def forwarder(
    cfg: GatewayConfig,
    buffer: StoreAndForwardBuffer,
    publisher: IotPublisher,
    stats: Stats,
) -> None:
    """Publica en ventanas de mensajes en vuelo y elimina del buffer solo lo confirmado."""
    topic = topic_for(cfg.site_id)
    backoff_s = 0.5
    while True:
        rows = buffer.peek(cfg.inflight_window)
        if not rows:
            await asyncio.sleep(0.2)
            continue
        results = await asyncio.gather(*(asyncio.to_thread(publisher.publish, topic, payload) for _, payload in rows))
        acked = [row_id for (row_id, _), ok in zip(rows, results, strict=True) if ok]
        buffer.ack(acked)
        stats.batches_sent += len(acked)
        if all(results):
            backoff_s = 0.5
        else:
            logger.warning("Fallo de publicación; %d mensajes permanecen en el buffer", len(rows) - len(acked))
            await asyncio.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 30.0)


async def heartbeat(
    cfg: GatewayConfig,
    buffer: StoreAndForwardBuffer,
    publisher: IotPublisher,
    reader: OpcReader,
    stats: Stats,
) -> None:
    """Publica el estado del gateway cada `status_interval_s`. Es de mejor esfuerzo: no se guarda en el buffer.

    Un latido atrasado no sirve, así que si no hay conexión simplemente se omite; la ausencia de latidos es,
    justamente, lo que dispara la alarma en la nube.
    """
    topic = status_topic_for(cfg.site_id)
    while True:
        await asyncio.sleep(cfg.status_interval_s)
        try:
            status = build_status(
                cfg.site_id,
                stats,
                pending=buffer.pending(),
                mqtt_connected=publisher.is_connected(),
                opc_connected=reader.connected,
                now_ms=_now_ms(),
            )
            await asyncio.to_thread(publisher.publish, topic, encode_status(status))
        except Exception as exc:  # el latido nunca debe detener al gateway
            logger.warning("No se pudo publicar el estado (%s: %s)", type(exc).__name__, exc)


async def report(buffer: StoreAndForwardBuffer, stats: Stats, interval_s: float = 10.0) -> None:
    while True:
        await asyncio.sleep(interval_s)
        logger.info(
            "lecturas=%d descartadas=%d lotes_bufferizados=%d lotes_enviados=%d pendientes=%d",
            stats.readings_in,
            stats.readings_dropped,
            stats.batches_buffered,
            stats.batches_sent,
            buffer.pending(),
        )


async def run_gateway(cfg: GatewayConfig) -> None:
    queue: asyncio.Queue[Reading] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    stats = Stats()
    buffer = StoreAndForwardBuffer(cfg.buffer_path)
    publisher = IotPublisher(cfg)

    def sink(reading: Reading) -> None:
        stats.readings_in += 1
        try:
            queue.put_nowait(reading)
        except asyncio.QueueFull:
            stats.readings_dropped += 1

    reader = OpcReader(cfg, sink)
    await asyncio.to_thread(publisher.start)
    logger.info("Gateway iniciado para el sitio %s", cfg.site_id)
    try:
        await asyncio.gather(
            reader.run(),
            batcher(cfg, queue, buffer, stats),
            forwarder(cfg, buffer, publisher, stats),
            report(buffer, stats),
            heartbeat(cfg, buffer, publisher, reader, stats),
        )
    finally:
        publisher.stop()
        buffer.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = GatewayConfig.from_env()
    try:
        asyncio.run(run_gateway(cfg))
    except KeyboardInterrupt:
        logger.info("Gateway detenido por el usuario")


if __name__ == "__main__":
    main()
