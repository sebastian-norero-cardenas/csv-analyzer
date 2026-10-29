"""Bootstrap and CLI for the independent Qt entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .coordinator import QtCoordinator
from .shell import QtShell


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Varia Studio Qt foundation: load and identify a dataset."
    )
    parser.add_argument("file", nargs="?", type=Path, help="Dataset to open at startup")
    parser.add_argument("--debug", action="store_true", help="Log diagnostic details to stderr")
    return parser


def create_application() -> tuple[QApplication, QtCoordinator, QtShell]:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    shell = QtShell()
    coordinator = QtCoordinator(shell)
    return app, coordinator, shell


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app, coordinator, shell = create_application()
    shell.show()
    coordinator.start()
    if args.file:
        coordinator.load_path(args.file)
    return app.exec()
