import json
import math
from decimal import Decimal
from pathlib import Path
from statistics import StatisticsError

import pytest

from stream_stats import WindowStatistics, list_window_statistics_backends

FIXTURES = Path(__file__).parents[1] / "conformance" / "window_statistics.json"


def _binary64(value):
    if value is None:
        return None
    return float.fromhex(value)


def _assert_float(actual, expected, tolerance):
    if expected is None:
        assert actual is None
        return
    target = float(Decimal(expected))
    assert math.isclose(
        actual,
        target,
        rel_tol=float(Decimal(tolerance["relative"])),
        abs_tol=float(Decimal(tolerance["absolute"])),
    )


def _assert_snapshot(actual, expected, tolerance):
    assert actual.count == expected["count"]
    for name in ("sum", "min", "max", "mean", "variance", "std"):
        _assert_float(getattr(actual, name), expected[name], tolerance)


def _run_command(window, command, tolerance):
    operation = command["operation"]
    error = command.get("expected_error")
    if command.get("requires_nonempty") and window.count == 0:
        window.add(0)

    def execute():
        if operation == "add":
            return window.add(_binary64(command["value"]))
        if operation == "add_many":
            return window.add_many(map(_binary64, command["values"]))
        if operation == "remove":
            return window.remove(_binary64(command["value"]))
        if operation == "remove_oldest":
            return window.remove_oldest()
        if operation == "snapshot":
            return window.snapshot()
        if operation == "mean":
            return window.mean
        if operation == "percentile":
            return window.percentile(float(Decimal(command["argument"])))
        if operation == "percentile_of":
            return window.percentile_of(_binary64(command["argument"]))
        raise AssertionError(f"unknown fixture operation: {operation}")

    if error:
        exception = {
            "empty": StatisticsError,
            "not_found": ValueError,
            "invalid_argument": ValueError,
        }[error]
        with pytest.raises(exception):
            execute()
        return

    actual = execute()
    if operation == "snapshot":
        _assert_snapshot(actual, command["expected"], tolerance)
    elif operation == "add":
        expected = _binary64(command["evicted"])
        assert actual == expected
        if expected == 0.0:
            assert actual.hex() == expected.hex()
    elif operation == "add_many":
        assert actual == [_binary64(value) for value in command["evicted"]]
    elif operation == "remove_oldest":
        expected = _binary64(command["expected"])
        assert actual == expected
        if expected == 0.0:
            assert actual.hex() == expected.hex()
    elif operation in {"percentile", "percentile_of"}:
        _assert_float(actual, command["expected"], tolerance)


BACKENDS = [
    info.name
    for info in list_window_statistics_backends()
    if info.available and info.name != "numpy"
]


@pytest.mark.parametrize("backend", BACKENDS)
def test_engine_matches_language_neutral_conformance_vectors(backend):
    fixtures = json.loads(FIXTURES.read_text())
    tolerance = fixtures["default_tolerance"]
    for vector in fixtures["vectors"]:
        window = WindowStatistics(vector["window_size"], backend=backend)
        for command in vector["commands"]:
            _run_command(window, command, tolerance)
        window._validate()
