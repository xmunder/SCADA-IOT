"""Lector OPC UA basado en suscripciones (data change), que evita el sondeo periódico."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from asyncua import Client, ua

from gateway.config import GatewayConfig
from gateway.payload import BAD, GOOD, Reading, to_numeric

logger = logging.getLogger(__name__)

ReadingSink = Callable[[Reading], None]

HEALTH_CHECK_INTERVAL_S = 2.0
HEALTH_CHECK_TIMEOUT_S = 5.0


class Backoff:
    """Retroceso exponencial: duplica la espera hasta un máximo y vuelve al inicio con reset()."""

    def __init__(self, initial_s: float, max_s: float) -> None:
        self._initial_s = initial_s
        self._max_s = max_s
        self._current_s = initial_s

    def next_delay(self) -> float:
        delay = self._current_s
        self._current_s = min(self._current_s * 2, self._max_s)
        return min(delay, self._max_s)

    def reset(self) -> None:
        self._current_s = self._initial_s


async def run_with_reconnect(
    session: Callable[[], Awaitable[None]],
    backoff: Backoff,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Reabre la sesión cada vez que termina o falla; solo la cancelación detiene el bucle."""
    while True:
        try:
            await session()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # cualquier fallo de red u OPC UA debe provocar un reintento
            task = asyncio.current_task()
            if task is not None and getattr(task, "cancelling", lambda: 0)():
                # La tarea fue cancelada y al cerrar la sesión asyncua lanzó otro error: no es una caída.
                raise asyncio.CancelledError from exc
            delay = backoff.next_delay()
            logger.warning("Sesión OPC UA perdida (%s: %s); reintentando en %.1f s", type(exc).__name__, exc, delay)
            await sleep(delay)
        else:
            delay = backoff.next_delay()
            logger.warning("La sesión OPC UA terminó; reabriendo en %.1f s", delay)
            await sleep(delay)


def _epoch_ms(moment: datetime | None) -> int:
    if moment is None:
        return int(time.time() * 1000)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


class _DataChangeHandler:
    def __init__(self, sink: ReadingSink, names: dict[ua.NodeId, str]) -> None:
        self._sink = sink
        self._names = names

    def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
        value_result = data.monitored_item.Value
        numeric = to_numeric(value_result.Value.Value)
        if numeric is None:
            logger.debug("Valor no numérico descartado en %s", node)
            return
        quality = GOOD if value_result.StatusCode.is_good() else BAD
        tag = self._names.get(node.nodeid, str(node.nodeid))
        self._sink(
            Reading(
                tag=tag,
                value=numeric,
                ts_ms=_epoch_ms(value_result.SourceTimestamp),
                quality=quality,
            )
        )


class OpcReader:
    def __init__(self, cfg: GatewayConfig, sink: ReadingSink, publishing_interval_ms: int = 500) -> None:
        self._cfg = cfg
        self._sink = sink
        self._interval_ms = publishing_interval_ms
        self._backoff = Backoff(initial_s=1.0, max_s=cfg.opc_reconnect_max_s)

    async def _discover_variables(self, client: Client) -> dict[Any, str]:
        """Devuelve un mapa nodo -> nombre (BrowseName) de cada variable del objeto raíz."""
        namespace_index = await client.get_namespace_index(self._cfg.opc_namespace_uri)
        for child in await client.nodes.objects.get_children():
            name = (await child.read_browse_name()).Name
            if name == self._cfg.opc_root_name and child.nodeid.NamespaceIndex == namespace_index:
                variables: dict[Any, str] = {}
                for node in await child.get_children():
                    if await node.read_node_class() == ua.NodeClass.Variable:
                        variables[node] = (await node.read_browse_name()).Name
                return variables
        raise RuntimeError(f"No se encontró el objeto '{self._cfg.opc_root_name}' en el espacio de nombres indicado")

    async def _session(self) -> None:
        """Mantiene una sesión abierta hasta que se cancele la tarea o falle la conexión."""
        async with Client(url=self._cfg.opc_endpoint) as client:
            variables = await self._discover_variables(client)
            names = {node.nodeid: name for node, name in variables.items()}
            subscription = await client.create_subscription(self._interval_ms, _DataChangeHandler(self._sink, names))
            await subscription.subscribe_data_change(list(variables))
            self._backoff.reset()
            logger.info("Suscritos a %d tags en %s", len(variables), self._cfg.opc_endpoint)
            # Si el servidor cae, las suscripciones dejan de notificar sin lanzar error: una lectura
            # periódica del estado del servidor (nodo estándar de OPC UA) detecta la caída.
            state_node = client.get_node(ua.ObjectIds.Server_ServerStatus_State)
            while True:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)
                await asyncio.wait_for(state_node.read_value(), timeout=HEALTH_CHECK_TIMEOUT_S)

    async def run(self) -> None:
        """Lee indefinidamente; si el servidor OPC UA no está disponible, reintenta con retroceso exponencial."""
        await run_with_reconnect(self._session, self._backoff)
