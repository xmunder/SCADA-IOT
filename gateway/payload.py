"""Construcción del esquema de mensajes que consume la nube."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

GOOD = "GOOD"
BAD = "BAD"


@dataclass(frozen=True)
class Reading:
    """Una lectura de un tag en un instante de origen."""

    tag: str
    value: float
    ts_ms: int
    quality: str = GOOD


def to_numeric(value: Any) -> float | None:
    """Convierte el valor de un tag a float. Devuelve None si no es numérico finito."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def topic_for(site_id: str) -> str:
    return f"plant/{site_id}/telemetry"


def status_topic_for(site_id: str) -> str:
    return f"plant/{site_id}/status"


def build_batch(site_id: str, readings: Iterable[Reading]) -> dict[str, Any]:
    return {"site": site_id, "readings": [asdict(r) for r in readings]}


def encode_batch(site_id: str, readings: Iterable[Reading]) -> bytes:
    """Serializa un lote a JSON válido (sin NaN ni Infinity)."""
    body = build_batch(site_id, readings)
    return json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
