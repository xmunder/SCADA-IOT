import pytest

from loadtest.report import percentile, summarize_latencies


def test_percentile_nearest_rank():
    values = sorted([10, 20, 30, 40, 50])
    assert percentile(values, 50) == 30
    assert percentile(values, 95) == 50
    assert percentile(values, 100) == 50


def test_percentile_empty_returns_none():
    assert percentile([], 50) is None


def test_percentile_rejects_invalid_pct():
    with pytest.raises(ValueError):
        percentile([1, 2], 0)


def test_summarize_latencies_keys():
    result = summarize_latencies([1.0, 2.0, 3.0])
    assert result["p50_ms"] == 2.0
    assert result["max_ms"] == 3.0
    assert result["mean_ms"] == pytest.approx(2.0)
