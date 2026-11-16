"""Lector OPC UA basado en suscripciones (data change), que evita el sondeo periódico."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from asyncua import Client, ua

from gateway.config import GatewayConfig
from gateway.payload import BAD, GOOD, Reading, to_numeric

logger = logging.getLogger(__name__)

ReadingSink = Callable[[Reading], None]


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

    async def run(self) -> None:
        """Mantiene la sesión abierta hasta que la tarea sea cancelada o falle la conexión."""
        async with Client(url=self._cfg.opc_endpoint) as client:
            variables = await self._discover_variables(client)
            names = {node.nodeid: name for node, name in variables.items()}
            subscription = await client.create_subscription(self._interval_ms, _DataChangeHandler(self._sink, names))
            await subscription.subscribe_data_change(list(variables))
            logger.info("Suscritos a %d tags en %s", len(variables), self._cfg.opc_endpoint)
            await asyncio.Event().wait()
