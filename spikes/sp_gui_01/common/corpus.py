"""Deterministic corpus generation for SP-GUI-01."""
from __future__ import annotations

import argparse
import csv
import hashlib
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROWS = 200_000
SEED = 20261025
EXPECTED_SHA256 = "50d6cb79a7bfcc6085fc146ff51f167d981a6229e077b9f9173336a178b50ac4"
COLUMNS = (
    ["int_exact", "sort_key"]
    + [f"int_nullable_{i:02d}" for i in range(13)]
    + [f"float_{i:02d}" for i in range(20)]
    + [f"text_{i:02d}" for i in range(10)]
    + [f"category_{i:02d}" for i in range(5)]
    + [f"bool_{i:02d}" for i in range(5)]
    + [f"timestamp_{i:02d}" for i in range(6)]
)
assert len(COLUMNS) == 61


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(path: str | Path) -> Path:
    """Generate S-200K-mixed exactly; output is stable across toolkit candidates."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    labels = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
    categories = ("A", "B", "C", "D", "E")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for source_row_id in range(1, ROWS + 1):
            row: list[object] = []
            if source_row_id == 100_000:
                exact: object = 9007199254740992
            elif source_row_id == 100_001:
                exact = 9007199254740993
            elif source_row_id % 97 == 0:
                exact = ""
            else:
                exact = 9007199254000000 + source_row_id
            row.append(exact)
            row.append("" if source_row_id % 101 == 0 else (source_row_id * 17) % 100)
            for index in range(13):
                row.append(
                    ""
                    if source_row_id % (113 + index) == 0
                    else (source_row_id * (index + 3) + index * 11) % 1_000_003
                )
            for index in range(20):
                if source_row_id % (149 + index) == 0:
                    row.append("")
                else:
                    value = (
                        rng.uniform(-1000.0, 1000.0)
                        + (source_row_id % 37) * 0.001
                        + index * 0.01
                    )
                    row.append(format(value, ".12g"))
            for index in range(10):
                if source_row_id % (211 + index) == 0:
                    row.append("")
                elif index == 0 and source_row_id in (7, 100_000, 199_999):
                    row.append("Needle-Alpha")
                else:
                    row.append(
                        f"{labels[(source_row_id + index) % len(labels)]}_"
                        f"{(source_row_id * (index + 1)) % 251:03d}"
                    )
            for index in range(5):
                row.append(
                    ""
                    if source_row_id % (233 + index) == 0
                    else categories[(source_row_id + index) % len(categories)]
                )
            for index in range(5):
                row.append(
                    ""
                    if source_row_id % (251 + index) == 0
                    else ("True" if (source_row_id + index) & 1 else "False")
                )
            for index in range(6):
                if source_row_id % (271 + index) == 0:
                    row.append("")
                else:
                    value = base_dt + timedelta(minutes=source_row_id * (index + 1))
                    row.append(value.isoformat().replace("+00:00", "Z"))
            writer.writerow(row)
    actual = sha256(path)
    if actual != EXPECTED_SHA256:
        raise RuntimeError(f"S-200K SHA mismatch: expected {EXPECTED_SHA256}, got {actual}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    generated = generate(args.output)
    print(f"{sha256(generated)}  {generated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
