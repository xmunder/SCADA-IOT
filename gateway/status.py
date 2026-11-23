"""Mensaje de estado (latido) del gateway: lo que la nube necesita para saber si sigue vivo y cómo está."""

from __future__ import annotations

import json
from typing import Any, Protocol


class StatsLike(Protocol):
    readings_in: int
    readings_dropped: int
    batches_sent: int


def build_status(
    site_id: str,
    stats: StatsLike,
    *,
    pending: int,
    mqtt_connected: bool,
    opc_connected: bool,
    now_ms: int,
) -> dict[str, Any]:
    return {
        "type": "status",
        "site": site_id,
        "ts_ms": now_ms,
        "pending": pending,
        "batches_sent": stats.batches_sent,
        "readings_in": stats.readings_in,
        "readings_dropped": stats.readings_dropped,
        "mqtt_connected": mqtt_connected,
        "opc_connected": opc_connected,
    }


def encode_status(status: dict[str, Any]) -> bytes:
    return json.dumps(status, separators=(",", ":"), allow_nan=False).encode("utf-8")
