"""Performance comparisons require paired, complete and repeated measurements."""

from copy import deepcopy

import pytest
from scripts.benchmark import peak_memory, summarize


@pytest.fixture
def samples():
    return [
        {
            "task": "models",
            "implementation": implementation,
            "iteration": index,
            "status": "PASS",
            "elapsed_seconds": seconds,
            "peak_memory_bytes": 100,
        }
        for implementation, values in [("baseline", [10, 100, 11]), ("current", [12, 11, 13])]
        for index, seconds in enumerate(values, 1)
    ]


def test_repeated_median_flags_more_than_ten_percent(samples):
    rows = summarize(samples, ["models"], 3)
    assert rows[0]["medians"] == {"baseline": 11, "current": 12}
    assert not rows[0]["review_required"] and not rows[1]["review_required"]
    increased = deepcopy(samples)
    for item in increased:
        if item["implementation"] == "current":
            item["elapsed_seconds"] *= 2
    assert summarize(increased, ["models"], 3)[0]["review_required"]


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "failed", "zero", "nan", "infinity", "unknown_task"]
)
def test_incomplete_or_invalid_measurements_are_rejected(samples, fault):
    if fault == "missing":
        samples.pop()
    elif fault == "duplicate":
        samples.append(samples[0])
    elif fault == "failed":
        samples[0]["status"] = "FAIL"
    elif fault == "zero":
        samples[0]["peak_memory_bytes"] = 0
    elif fault == "nan":
        samples[0]["elapsed_seconds"] = float("nan")
    elif fault == "infinity":
        samples[0]["elapsed_seconds"] = float("inf")
    else:
        samples[0]["task"] = "unregistered"
    with pytest.raises(ValueError):
        summarize(samples, ["models"], 3)


def test_process_peak_memory_is_reported_in_bytes():
    assert peak_memory() > 1024 * 1024


def test_exact_ten_percent_does_not_trigger_the_over_ten_percent_gate(samples):
    for item in samples:
        item["elapsed_seconds"] = 10 if item["implementation"] == "baseline" else 11
    assert not summarize(samples, ["models"], 3)[0]["review_required"]
