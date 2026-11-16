import pytest

from loadtest.clock_check import ClockReading
from loadtest.gateway_report import build_report as gateway_report
from loadtest.latency_report import build_report, correct_latencies
from loadtest.run_matrix import combine_clock, run_row


def test_ahead_clock_underestimates_latency_and_correction_restores_it():
    # reloj del equipo 180 ms adelantado: referencia - equipo = -0.18 s
    real = [100.0, 150.0, 200.0]
    measured = [ms - 180.0 for ms in real]
    assert correct_latencies(measured, offset_s=-0.18) == pytest.approx(real)


def test_behind_clock_overestimates_latency_and_correction_restores_it():
    real = [100.0, 150.0]
    measured = [ms + 200.0 for ms in real]
    assert correct_latencies(measured, offset_s=0.2) == pytest.approx(real)


def test_zero_offset_leaves_latencies_unchanged():
    assert correct_latencies([1.0, 2.0], 0.0) == [1.0, 2.0]


def test_report_applies_offset_and_keeps_the_raw_minimum():
    summary = {"run_name": "r", "tag_counts": {"A": 3}}
    report = build_report(summary, {"A": [-80, 20, 70]}, offset_s=-0.18)
    assert report["raw_latency_min_ms"] == -80.0
    assert report["clock_offset_ms"] == -180.0
    assert report["ingest_latency"]["p50_ms"] == pytest.approx(200.0)  # 20 + 180
    assert report["ingest_latency"]["max_ms"] == pytest.approx(250.0)


def test_gateway_report_applies_the_same_correction():
    points = {"Tag_0000": [(0, -100), (1000, -50), (2000, 0)]}
    report = gateway_report(points, period_s=1.0, window_s=10.0, offset_s=-0.2)
    assert report["clock_offset_ms"] == -200.0
    assert report["ingest_latency"]["p50_ms"] == pytest.approx(150.0)


def test_combine_clock_averages_offsets_and_reports_drift_and_uncertainty():
    before = ClockReading(offset_s=-0.18, method="sntp", uncertainty_s=0.04)
    after = ClockReading(offset_s=-0.20, method="sntp", uncertainty_s=0.07)
    combined = combine_clock(before, after)
    assert combined["offset_s"] == pytest.approx(-0.19)
    assert combined["offset_ms"] == -190.0
    assert combined["drift_ms"] == -20.0
    assert combined["uncertainty_ms"] == 70.0


def test_run_row_records_the_clock_used_for_the_correction():
    summary = {"achieved_rate_readings_s": 1.0, "msgs_unacked": 0, "publish_latency": {"p95_ms": 1.0}}
    report = {
        "readings_expected": 10,
        "readings_stored_distinct": 10,
        "lost_readings": 0,
        "loss_pct": 0.0,
        "ingest_latency": {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0, "max_ms": 4.0},
        "raw_latency_min_ms": -170.0,
    }
    row = run_row(200, 1, summary, report, 1.0, {"offset_ms": -190.0, "drift_ms": -20.0, "uncertainty_ms": 70.0})
    assert row["clock_offset_ms"] == -190.0 and row["clock_drift_ms"] == -20.0
    assert row["clock_uncertainty_ms"] == 70.0 and row["raw_latency_min_ms"] == -170.0
