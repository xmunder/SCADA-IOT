from loadtest.verify_counts import compare_counts, count_series, verify


class FakeTable:
    """Imita Table.query con paginación: devuelve páginas de 2 items."""

    def __init__(self, items_by_series):
        self.items = items_by_series
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        values = kwargs["KeyConditionExpression"].get_expression()["values"]
        series = values[0].get_expression()["values"][1]
        hits = self.items.get(series, [])
        start = kwargs.get("ExclusiveStartKey", {}).get("offset", 0)
        page = hits[start : start + 2]
        response = {"Count": len(page)}
        if start + 2 < len(hits):
            response["LastEvaluatedKey"] = {"offset": start + 2}
        return response


def test_count_series_follows_pagination():
    table = FakeTable({"S#T1": [1, 2, 3, 4, 5]})
    assert count_series(table, "S#T1", 0, 10) == 5
    assert table.calls == 3


def test_compare_counts_reports_loss_per_tag():
    result = compare_counts({"A": 10, "B": 10}, {"A": 10, "B": 7})
    assert result["readings_expected"] == 20
    assert result["readings_stored_distinct"] == 17
    assert result["lost_readings"] == 3
    assert result["loss_pct"] == 15.0
    assert result["lost_by_tag"] == {"B": 3}
    assert result["tags_with_loss"] == 1


def test_compare_counts_ignores_unrelated_extra_items():
    result = compare_counts({"A": 5}, {"A": 9})
    assert result["lost_readings"] == 0
    assert result["lost_by_tag"] == {}


def test_verify_queries_every_tag_of_the_site():
    table = FakeTable({"P#A": [1, 2, 3], "P#B": [1]})
    summary = {"site": "P", "started_ms": 5000, "ended_ms": 6000, "tag_counts": {"A": 3, "B": 2}}
    result = verify(table, summary, workers=2)
    assert result["lost_by_tag"] == {"B": 1}
    assert result["readings_expected"] == 5


def test_verify_requires_tag_counts():
    import pytest

    with pytest.raises(SystemExit):
        verify(FakeTable({}), {"site": "P", "started_ms": 0, "ended_ms": 1})
