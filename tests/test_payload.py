import json
import math

import pytest

from gateway.payload import Reading, encode_batch, to_numeric, topic_for


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, 1.0),
        (False, 0.0),
        (3, 3.0),
        (2.5, 2.5),
        ("texto", None),
        (None, None),
        (math.nan, None),
        (math.inf, None),
    ],
)
def test_to_numeric(raw, expected):
    assert to_numeric(raw) == expected


def test_encode_batch_is_valid_json_without_nan():
    body = encode_batch("PLANTA-01", [Reading("Tag_0001", 1.5, 1000), Reading("Tag_0002", 2.0, 1001, "BAD")])
    parsed = json.loads(body)
    assert parsed["site"] == "PLANTA-01"
    assert parsed["readings"][1] == {"tag": "Tag_0002", "value": 2.0, "ts_ms": 1001, "quality": "BAD"}


def test_topic_for_site():
    assert topic_for("PLANTA-01") == "plant/PLANTA-01/telemetry"
