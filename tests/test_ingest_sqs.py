"""La Lambda de ingesta también recibe lotes de SQS y responde con fallos parciales."""

import io
import json
from contextlib import redirect_stdout

import app


class FakeTable:
    def __init__(self, fail: bool = False) -> None:
        self.items: list[dict] = []
        self.fail = fail
        self.writes = 0

    def batch_writer(self, **_kwargs):
        table = self

        class Writer:
            def __enter__(self_inner):
                table.writes += 1
                if table.fail:
                    raise RuntimeError("ProvisionedThroughputExceededException")
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def put_item(self_inner, Item):
                table.items.append(Item)

        return Writer()


def _record(message_id: str, body, site: str = "PLANTA-01", n: int = 2) -> dict:
    if body is None:
        body = {
            "site": site,
            "readings": [{"tag": f"T{i}", "value": float(i), "ts_ms": 1_000 + i, "quality": "GOOD"} for i in range(n)],
        }
    return {"messageId": message_id, "body": body if isinstance(body, str) else json.dumps(body)}


def _run(records: list[dict], table: FakeTable):
    app._table = table
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = app.handler({"Records": records}, None)
    finally:
        app._table = None
    emf = [json.loads(line) for line in buffer.getvalue().splitlines() if line.startswith("{")]
    return result, emf


def test_all_records_of_a_batch_are_written_together_and_reported_per_site():
    table = FakeTable()
    result, emf = _run([_record("a", None, "PLANTA-01"), _record("b", None, "PLANTA-02", n=3)], table)
    assert result == {"batchItemFailures": []}
    assert len(table.items) == 5
    assert table.writes == 1  # un solo batch_writer para todo el lote de SQS
    written = {r["Site"]: r["ReadingsWritten"] for r in emf}
    assert written == {"PLANTA-01": 2, "PLANTA-02": 3}


def test_an_invalid_body_is_rejected_without_failing_the_batch():
    table = FakeTable()
    result, emf = _run([_record("a", None), _record("b", "esto no es json")], table)
    assert result == {"batchItemFailures": []}
    assert len(table.items) == 2
    unknown = next(r for r in emf if r["Site"] == "unknown")
    assert unknown["ReadingsRejected"] == 1 and unknown["ReadingsWritten"] == 0


def test_a_write_failure_returns_only_the_records_that_had_items_and_counts_nothing_twice():
    table = FakeTable(fail=True)
    only_rejected = _record("c", {"site": "PLANTA-01", "readings": [{"tag": "x", "quality": "BAD"}]})
    result, emf = _run([_record("a", None), _record("b", None), only_rejected], table)
    assert result == {"batchItemFailures": [{"itemIdentifier": "a"}, {"itemIdentifier": "b"}]}
    # lo escrito se contará en el reintento; solo se informan ahora los rechazos del registro que no se reintenta
    assert all(r["ReadingsWritten"] == 0 for r in emf)
    assert sum(r["ReadingsRejected"] for r in emf) == 1


def test_a_batch_where_nothing_is_valid_does_not_touch_the_table():
    table = FakeTable()
    result, _ = _run([_record("a", "{}")], table)
    assert result == {"batchItemFailures": []}
    assert table.writes == 0


def test_direct_iot_events_still_work_like_before():
    table = FakeTable()
    app._table = table
    with redirect_stdout(io.StringIO()):
        result = app.handler(json.loads(_record("a", None)["body"]), None)
    app._table = None
    assert result == {"written": 2, "rejected": 0}
    assert len(table.items) == 2
