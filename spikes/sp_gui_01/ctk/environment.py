"""Installation/dependency inventory for the CTk spike."""
from __future__ import annotations

import importlib.metadata as metadata
import json
import platform
import sys
from pathlib import Path

PACKAGES = ("csv-analyzer-desktop", "customtkinter", "pandas", "numpy", "matplotlib")


def distribution_size(name: str) -> int | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    total = 0
    for item in dist.files or ():
        path = Path(dist.locate_file(item))
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def main() -> int:
    packages = {}
    for name in PACKAGES:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = None
        packages[name] = {"version": version, "bytes": distribution_size(name)}
    print(
        json.dumps(
            {
                "candidate": "CTk/ttk",
                "python": sys.version,
                "platform": platform.platform(),
                "packages": packages,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
