"""Equivalent measurement primitives for both SP-GUI-01 candidates."""
from __future__ import annotations

import json
import os
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    event: str
    seconds: float
    rss_kib: int
    metadata: dict[str, object]


def current_rss_kib() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


@contextmanager
def measure(event: str, sink: list[Sample], **metadata):
    started = time.perf_counter()
    try:
        yield
    finally:
        sink.append(Sample(event, time.perf_counter() - started, current_rss_kib(), metadata))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def summarize(samples: list[Sample]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[float]] = {}
    for sample in samples:
        groups.setdefault(sample.event, []).append(sample.seconds)
    return {
        event: {
            "n": len(values),
            "median_seconds": statistics.median(values),
            "p95_seconds": percentile(values, 0.95),
            "min_seconds": min(values),
            "max_seconds": max(values),
        }
        for event, values in groups.items()
    }


def write_json(
    path: str | Path,
    samples: list[Sample],
    extra: dict[str, object] | None = None,
) -> None:
    payload = {
        "pid": os.getpid(),
        "samples": [sample.__dict__ for sample in samples],
        "summary": summarize(samples),
        "extra": extra or {},
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
