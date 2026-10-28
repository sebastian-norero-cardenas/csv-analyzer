#!/usr/bin/env python3
"""Launch the installed Qt spike and load a supplied 500x61 dataset outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

FROZEN_F500_SHA256 = "947eb8dc651a83df4c1edabb7f1c6a21a01680939fd7135613dba264b35df1ed"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--expected-sha256", default=FROZEN_F500_SHA256)
    args = parser.parse_args(argv)

    fixture = args.dataset.resolve()
    workspace_raw = os.environ.get("GITHUB_WORKSPACE")
    workspace = Path(workspace_raw).resolve() if workspace_raw else None

    if os.environ.get("PYTHONPATH"):
        raise RuntimeError(f"PYTHONPATH must be unset, got {os.environ['PYTHONPATH']!r}")
    cwd = Path.cwd().resolve()
    if workspace is not None and (cwd == workspace or workspace in cwd.parents):
        raise RuntimeError(f"cwd must be outside repository: {cwd}")
    actual_sha = sha256(fixture)
    if actual_sha != args.expected_sha256:
        raise RuntimeError(
            f"Dataset SHA-256 mismatch: expected {args.expected_sha256}, got {actual_sha}"
        )

    import csv_analyzer
    import spikes
    from PySide6 import __version__ as pyside_version
    from PySide6.QtWidgets import QApplication
    from spikes.sp_gui_01.qt.app import QtSpikeWindow

    if workspace is not None:
        for module in (csv_analyzer, spikes):
            origin = Path(module.__file__).resolve()
            if origin == workspace or workspace in origin.parents:
                raise RuntimeError(
                    f"Imported {module.__name__} from source checkout: {origin}"
                )

    app = QApplication.instance() or QApplication(["sp-gui-01-qt-smoke"])
    window = QtSpikeWindow()
    errors: list[str] = []
    window._show_error = lambda error: errors.append(str(error))
    window.show()
    app.processEvents()
    visible = window.isVisible()
    window.load_path(fixture)

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        app.processEvents()
        if errors:
            break
        dataset = window.model.dataset
        if dataset is not None and not window.runner.jobs:
            break
        time.sleep(0.02)

    dataset = window.model.dataset
    if errors:
        raise RuntimeError("Qt launch/load reported error: " + " | ".join(errors))
    if dataset is None:
        raise RuntimeError("Dataset did not become active before timeout")
    if dataset.frame.shape != (500, 61):
        raise RuntimeError(f"unexpected dataset shape: {dataset.frame.shape}")
    if not visible:
        raise RuntimeError("Qt top-level window never became visible under Xvfb")

    print("launch=PASS")
    print("window_visible=true")
    print("shape=500x61")
    print(f"dataset_sha256={actual_sha}")
    print(f"pyside6={pyside_version}")
    print(f"cwd={cwd}")
    print("PYTHONPATH=<unset>")
    print(f"spikes_origin={Path(spikes.__file__).resolve()}")
    print(f"csv_analyzer_origin={Path(csv_analyzer.__file__).resolve()}")

    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
