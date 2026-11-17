from datetime import datetime

import pytest

from loadtest.clock_check import ClockReading
from loadtest.flush_sweep import combine_offsets, parse_stats, rates, summary_row

LOG = """\
2026-11-16 17:20:10,000 INFO asyncua.common.subscription: Publish callback called
2026-11-16 17:20:20,010 INFO gateway: lecturas=1000 descartadas=0 lotes_bufferizados=10 lotes_enviados=10 pendientes=0
2026-11-16 17:20:30,010 INFO gateway: lecturas=2400 descartadas=0 lotes_bufferizados=24 lotes_enviados=24 pendientes=0
2026-11-16 17:20:40,010 INFO gateway: lecturas=3800 descartadas=0 lotes_bufferizados=38 lotes_enviados=37 pendientes=1
Traceback irrelevante
"""


def _epoch(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def test_parse_stats_reads_only_gateway_statistics_lines():
    stats = parse_stats(LOG)
    assert [s.readings for s in stats] == [1000, 2400, 3800]
    assert [s.batches_sent for s in stats] == [10, 24, 37]
    assert stats[2].pending == 1


def test_rates_use_the_first_and_last_statistics_inside_the_window():
    stats = parse_stats(LOG)
    result = rates(stats, _epoch("2026-11-16 17:20:15"), _epoch("2026-11-16 17:20:45"))
    # de 17:20:20 a 17:20:40: 2800 lecturas y 27 lotes en 20 s
    assert result["readings_per_s"] == pytest.approx(140.0)
    assert result["messages_per_s"] == pytest.approx(1.35)


def test_rates_are_none_without_enough_statistics_in_the_window():
    stats = parse_stats(LOG)
    assert rates(stats, _epoch("2026-11-16 17:20:35"), _epoch("2026-11-16 17:20:45")) == {
        "readings_per_s": None,
        "messages_per_s": None,
    }


def test_combine_offsets_averages_and_keeps_the_worst_uncertainty():
    before = ClockReading(offset_s=-0.18, method="sntp", uncertainty_s=0.03)
    after = ClockReading(offset_s=-0.16, method="sntp", uncertainty_s=0.05)
    offset, uncertainty = combine_offsets(before, after)
    assert offset == pytest.approx(-0.17)
    assert uncertainty == 0.05


def test_summary_row_flattens_latency_and_traffic():
    result = {
        "flush_interval_s": 0.5,
        "tags": 200,
        "items": 100,
        "items_per_s": 10.0,
        "readings_per_s": 140.0,
        "messages_per_s": 2.0,
        "ingest_latency": {"p50_ms": 301.23, "p95_ms": 702.0, "p99_ms": 800.0, "max_ms": 900.0},
        "analog_tags": 134,
        "estimated_missing": 0,
        "clock_offset_ms": -150.0,
        "clock_uncertainty_ms": 40.0,
    }
    row = summary_row(result)
    assert row["flush_interval_s"] == 0.5 and row["messages_per_s"] == 2.0
    assert row["p50_ms"] == 301.2 and row["p95_ms"] == 702.0
