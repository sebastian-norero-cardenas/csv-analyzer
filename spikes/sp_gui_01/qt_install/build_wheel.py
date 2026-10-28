#!/usr/bin/env python3
"""Build the disposable SP-GUI-01 Qt wheel without touching production metadata."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

DIST_NAME = "varia-studio-sp-gui-01-qt"
NORMALIZED = "varia_studio_sp_gui_01_qt"
VERSION = "0.1.0"
PYSIDE6_VERSION = "6.12.0"
ORIGIN_COMMIT = "9d54772c295e794747e60ebd50b582b6a393a0d4"
ORIGIN_TREE = "fbcd5e3c07100f7ef7257410c91e9279a68dace2"
SYSTEM_REQUIREMENTS = "spikes/sp_gui_01/qt_install/ubuntu-runtime-packages.txt"

CORE_FILES = (
    "src/csv_analyzer/__init__.py",
    "src/csv_analyzer/data.py",
    "src/csv_analyzer/analysis.py",
    "src/csv_analyzer/state.py",
    "src/csv_analyzer/exports.py",
    "src/csv_analyzer/plots.py",
)
SPIKE_FILES = (
    "spikes/sp_gui_01/__init__.py",
    "spikes/sp_gui_01/manifest.json",
    "spikes/sp_gui_01/common/__init__.py",
    "spikes/sp_gui_01/common/model.py",
    "spikes/sp_gui_01/common/measure.py",
    "spikes/sp_gui_01/qt/__init__.py",
    "spikes/sp_gui_01/qt/app.py",
    "spikes/sp_gui_01/qt/table_model.py",
    "spikes/sp_gui_01/qt/worker.py",
    "spikes/sp_gui_01/qt/environment.py",
)
ALLOWED_DIFF_PREFIXES = (
    ".github/workflows/sp-gui-01-qt-install.yml",
    "spikes/sp_gui_01/qt_install/",
)


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "csv_analyzer").is_dir() and (parent / ".git").exists():
            return parent
    raise RuntimeError("Run the builder from a Git checkout of varia-studio.")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def validate_source(root: Path) -> str:
    head = git(root, "rev-parse", "HEAD")
    parent = git(root, "rev-parse", "HEAD^")
    if parent != ORIGIN_COMMIT:
        raise RuntimeError(
            f"Packaging branch must have frozen Qt candidate as its direct parent; got {parent}"
        )
    changed = [
        line
        for line in git(root, "diff", "--name-only", f"{ORIGIN_COMMIT}..{head}").splitlines()
        if line
    ]
    invalid = [
        path
        for path in changed
        if not any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_DIFF_PREFIXES)
    ]
    if invalid:
        raise RuntimeError(f"Non-packaging files changed from frozen Qt candidate: {invalid}")
    return head


def wheel_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def add_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> tuple[str, str, str]:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)
    return name, wheel_hash(data), str(len(data))


def metadata() -> bytes:
    return f"""Metadata-Version: 2.1
Name: {DIST_NAME}
Version: {VERSION}
Summary: Disposable PySide6 packaging artifact for Varia Studio SP-GUI-01
Requires-Python: >=3.10
Requires-Dist: pandas>=2.2,<4
Requires-Dist: numpy>=1.26,<3
Requires-Dist: matplotlib>=3.8,<4
Requires-Dist: PySide6=={PYSIDE6_VERSION}

Experimental artifact only. Not the production Varia Studio distribution.
""".encode("utf-8")


def build(output: Path) -> Path:
    root = repo_root()
    head = validate_source(root)
    output.mkdir(parents=True, exist_ok=True)
    wheel_path = output / f"{NORMALIZED}-{VERSION}-py3-none-any.whl"
    dist_info = f"{NORMALIZED}-{VERSION}.dist-info"
    records: list[tuple[str, str, str]] = []

    with zipfile.ZipFile(wheel_path, "w") as archive:
        records.append(
            add_bytes(
                archive,
                "spikes/__init__.py",
                b'"""Namespace root for the disposable SP-GUI-01 wheel."""\n',
            )
        )
        for source in CORE_FILES:
            records.append(
                add_bytes(archive, source.removeprefix("src/"), (root / source).read_bytes())
            )
        for source in SPIKE_FILES:
            records.append(add_bytes(archive, source, (root / source).read_bytes()))

        records.append(add_bytes(archive, f"{dist_info}/METADATA", metadata()))
        records.append(
            add_bytes(
                archive,
                f"{dist_info}/WHEEL",
                (
                    "Wheel-Version: 1.0\n"
                    "Generator: SP-GUI-01-QT-PKG-01\n"
                    "Root-Is-Purelib: true\n"
                    "Tag: py3-none-any\n"
                ).encode("utf-8"),
            )
        )
        records.append(
            add_bytes(
                archive,
                f"{dist_info}/entry_points.txt",
                b"[console_scripts]\nvaria-sp-gui-01-qt = spikes.sp_gui_01.qt.app:main\n",
            )
        )
        records.append(
            add_bytes(archive, f"{dist_info}/top_level.txt", b"csv_analyzer\nspikes\n")
        )
        records.append(
            add_bytes(
                archive,
                f"{dist_info}/UBUNTU_RUNTIME_PACKAGES.txt",
                (root / SYSTEM_REQUIREMENTS).read_bytes(),
            )
        )
        origin = {
            "distribution": DIST_NAME,
            "version": VERSION,
            "frozen_qt_commit": ORIGIN_COMMIT,
            "frozen_qt_tree": ORIGIN_TREE,
            "pyside6": PYSIDE6_VERSION,
            "builder_commit": head,
        }
        records.append(
            add_bytes(
                archive,
                f"{dist_info}/SP_GUI_01_ORIGIN.json",
                (json.dumps(origin, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
        )

        record_name = f"{dist_info}/RECORD"
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows([*records, (record_name, "", "")])
        info = zipfile.ZipInfo(record_name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, stream.getvalue().encode("utf-8"))

    print(wheel_path)
    return wheel_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    build(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
