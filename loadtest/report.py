"""Estadística para las pruebas de carga (percentiles por rango más cercano)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(sorted_values: Sequence[float], pct: float) -> float | None:
    """Percentil por el método del rango más cercano. Requiere la lista ya ordenada."""
    if not sorted_values:
        return None
    if not 0 < pct <= 100:
        raise ValueError("pct debe estar en (0, 100]")
    rank = math.ceil(pct / 100.0 * len(sorted_values))
    return sorted_values[rank - 1]


def summarize_latencies(latencies_ms: Sequence[float]) -> dict[str, float | None]:
    ordered = sorted(latencies_ms)
    return {
        "p50_ms": percentile(ordered, 50),
        "p95_ms": percentile(ordered, 95),
        "p99_ms": percentile(ordered, 99),
        "max_ms": ordered[-1] if ordered else None,
        "mean_ms": (sum(ordered) / len(ordered)) if ordered else None,
    }
