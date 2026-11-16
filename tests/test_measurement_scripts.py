import pytest

from loadtest.latency_report import build_report, collect_series
from loadtest.outage_test import Sample, analyze_timeline
from loadtest.run_matrix import aggregate, run_row, wait_for_ingest


class PagedTable:
    """Imita Table.query devolviendo items de latencia en páginas de 2."""

    def __init__(self, latencies):
        self.latencies = latencies

    def query(self, **kwargs):
        start = kwargs.get("ExclusiveStartKey", {}).get("offset", 0)
        page = self.latencies[start : start + 2]
        response = {"Items": [{"ingest_latency_ms": v} for v in page]}
        if start + 2 < len(self.latencies):
            response["LastEvaluatedKey"] = {"offset": start + 2}
        return response


def test_collect_series_follows_pagination_and_returns_ints():
    assert collect_series(PagedTable([5, 6, 7, 8, 9]), "S#T", 0, 10) == [5, 6, 7, 8, 9]


def test_build_report_combines_loss_and_latency_percentiles():
    summary = {"run_name": "r", "tag_counts": {"A": 10, "B": 10}}
    report = build_report(summary, {"A": list(range(1, 11)), "B": list(range(1, 8))})
    assert report["readings_expected"] == 20
    assert report["lost_readings"] == 3
    assert report["lost_by_tag"] == {"B": 3}
    assert report["latency_samples"] == 17
    assert report["ingest_latency"]["max_ms"] == 10.0
    assert report["ingest_latency"]["p95_ms"] == 10.0


def test_analyze_timeline_measures_backlog_detection_and_recovery():
    samples = (
        [Sample(float(t), 0) for t in range(0, 10)]  # línea base
        + [Sample(float(t), (t - 9) * 5) for t in range(10, 20)]  # corte: crece 5 por segundo
        + [Sample(20.0, 50), Sample(21.0, 20), Sample(22.0, 0)]  # tras restablecer
    )
    result = analyze_timeline(samples, cut_t=10.0, restore_t=19.5)
    assert result["baseline_pending_max"] == 0
    assert result["backlog_max"] == 50
    assert result["backlog_start_s"] == 0.0
    assert result["backlog_at_restore"] == 50
    assert result["recovered"] is True
    assert result["recovery_s"] == pytest.approx(2.5)


def test_analyze_timeline_reports_not_recovered():
    samples = [Sample(0.0, 0), Sample(5.0, 10), Sample(9.0, 40), Sample(12.0, 30)]
    result = analyze_timeline(samples, cut_t=4.0, restore_t=10.0)
    assert result["recovered"] is False and result["recovery_s"] is None


def test_analyze_timeline_requires_samples():
    with pytest.raises(ValueError):
        analyze_timeline([], 1.0, 2.0)


def _row(level, rep, p95, loss):
    summary = {"achieved_rate_readings_s": 100.0, "msgs_unacked": 0, "publish_latency": {"p95_ms": 5.0}}
    report = {
        "readings_expected": 100,
        "readings_stored_distinct": 100 - loss,
        "lost_readings": loss,
        "loss_pct": float(loss),
        "ingest_latency": {"p50_ms": 1.0, "p95_ms": p95, "p99_ms": p95, "max_ms": p95},
    }
    return run_row(level, rep, summary, report)


def test_aggregate_computes_mean_and_sample_std_per_level():
    rows = [_row(200, 1, 100.0, 0), _row(200, 2, 200.0, 2), _row(1000, 1, 50.0, 0)]
    by_level = {entry["level_tags"]: entry for entry in aggregate(rows)}
    assert by_level[200]["repetitions"] == 2
    assert by_level[200]["ingest_p95_ms_mean"] == 150.0
    assert by_level[200]["ingest_p95_ms_std"] == pytest.approx(70.711, abs=1e-3)
    assert by_level[200]["loss_pct_mean"] == 1.0
    assert by_level[1000]["ingest_p95_ms_std"] == 0.0  # una sola repetición


def test_run_row_estimates_write_cost_from_stored_items():
    row = _row(200, 1, 10.0, 0)
    assert row["readings_stored"] == 100
    assert row["est_write_cost_usd"] == round(100 * 0.625 / 1_000_000, 4)


def test_wait_for_ingest_retries_until_nothing_is_missing():
    reports = iter([{"lost_readings": 50}, {"lost_readings": 5}, {"lost_readings": 0}])
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    report, waited = wait_for_ingest(
        lambda: next(reports), max_wait_s=100, poll_s=10, sleep=sleep, clock=lambda: now[0]
    )
    assert report["lost_readings"] == 0
    assert sleeps == [10, 10] and waited == 20


def test_wait_for_ingest_gives_up_after_max_wait_and_reports_the_loss():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    report, waited = wait_for_ingest(
        lambda: {"lost_readings": 7}, max_wait_s=25, poll_s=10, sleep=sleep, clock=lambda: now[0]
    )
    assert report["lost_readings"] == 7
    assert waited >= 25


def test_analyze_timeline_without_backlog_recovers_immediately():
    samples = [Sample(0.0, 0), Sample(5.0, 1), Sample(10.0, 1), Sample(20.0, 0)]
    result = analyze_timeline(samples, cut_t=5.0, restore_t=20.0)
    assert result["recovered"] is True and result["recovery_s"] == 0.0


def test_analyze_timeline_tolerance_ignores_normal_in_flight_noise():
    samples = [Sample(0.0, 0), Sample(5.0, 0), Sample(10.0, 40), Sample(20.0, 2), Sample(22.0, 0)]
    strict = analyze_timeline(samples, cut_t=5.0, restore_t=15.0, tolerance=0)
    tolerant = analyze_timeline(samples, cut_t=5.0, restore_t=15.0, tolerance=2)
    assert strict["recovery_s"] == 7.0
    assert tolerant["recovery_s"] == 5.0


def test_gateway_series_with_regular_updates_has_no_missing():
    from loadtest.gateway_report import analyze_series

    result = analyze_series([0, 1000, 2010, 2990, 4000, 5005], period_s=1.0)
    assert result == {"count": 6, "analog": True, "missing": 0}


def test_gateway_series_gap_is_counted_as_missing_readings():
    from loadtest.gateway_report import analyze_series

    # faltan 3 lecturas entre 3000 y 7000
    result = analyze_series([0, 1000, 2000, 3000, 7000, 8000, 9000], period_s=1.0)
    assert result["analog"] is True and result["missing"] == 3


def test_gateway_sparse_state_tag_is_not_treated_as_loss():
    from loadtest.gateway_report import analyze_series

    # tag binario: cambia cada ~30 s, no emite en cada ciclo
    result = analyze_series([0, 30_000, 61_000, 90_000], period_s=1.0)
    assert result["analog"] is False and result["missing"] == 0


def test_gateway_report_aggregates_latency_and_loss_only_over_analog_tags():
    from loadtest.gateway_report import build_report

    points = {
        "Tag_0000": [(0, 100), (1000, 200), (2000, 300), (5000, 400)],  # analógico, faltan 2
        "Tag_0001": [(0, 50), (40_000, 60), (80_000, 70)],  # estado, disperso
    }
    report = build_report(points, period_s=1.0, window_s=10.0)
    assert report["items"] == 7 and report["items_per_s"] == 0.7
    assert report["analog_tags"] == 1 and report["analog_readings"] == 4
    assert report["estimated_missing"] == 2
    assert report["estimated_loss_pct"] == round(100 * 2 / 6, 4)
    assert report["ingest_latency"]["max_ms"] == 400.0


def test_policy_check_requires_allowed_actions_to_work_and_forbidden_ones_to_fail():
    from infra.check_policy import evaluate

    assert evaluate(True, False, False)["policy_enforced"] is True
    assert evaluate(True, True, False)["policy_enforced"] is False  # publicó en tema ajeno
    assert evaluate(True, False, True)["policy_enforced"] is False  # conectó con client_id ajeno
    assert evaluate(False, False, False)["policy_enforced"] is False  # ni su propio tema funciona


def test_cold_table_window_stats_report_served_rate_and_throttled_fraction():
    from loadtest.ddb_cold_test import phase_summary, window_stats

    # 10 s con 100 items/s, de los cuales 40 se rechazan en los primeros 5 s y ninguno después
    records = [(s, 100, 40 if s < 5 else 0) for s in range(10)]
    (first,) = window_stats(records, window_s=10)
    assert first["attempted"] == 1000 and first["throttled"] == 200
    assert first["served_per_s"] == 80.0 and first["throttled_pct"] == 20.0
    assert phase_summary(records) == {"attempted": 1000, "served": 800, "throttled": 200, "throttled_pct": 20.0}


def test_cold_table_items_have_unique_keys_and_the_expected_shape():
    from loadtest.ddb_cold_test import make_item

    keys = {(i["series"]["S"], i["ts_ms"]["N"]) for i in (make_item(n, 1_000, tags=10) for n in range(100))}
    assert len(keys) == 100
    item = make_item(0, 1_000, tags=10)
    assert set(item) == {"series", "ts_ms", "site", "tag", "value", "ingest_latency_ms"}
