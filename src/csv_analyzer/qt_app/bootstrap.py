"""Bootstrap and CLI for the independent optional Qt entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

QT_EXTRA_MESSAGE = (
    "PySide6 is required for the Qt interface. "
    "Install Varia Studio with the 'qt' extra."
)


class QtDependencyError(RuntimeError):
    """The optional Qt runtime is not installed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Varia Studio Qt foundation: load and identify a dataset."
    )
    parser.add_argument("file", nargs="?", type=Path, help="Dataset to open at startup")
    parser.add_argument("--debug", action="store_true", help="Log diagnostic details to stderr")
    return parser


def _load_qt_components() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from PySide6.QtWidgets import QApplication

        from .coordinator import QtCoordinator
        from .shell import QtShell
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing == "PySide6" or missing.startswith("PySide6.") or missing == "shiboken6":
            raise QtDependencyError(QT_EXTRA_MESSAGE) from None
        raise
    return QApplication, QtCoordinator, QtShell


def create_application() -> tuple[Any, Any, Any]:
    QApplication, QtCoordinator, QtShell = _load_qt_components()
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
    try:
        app, coordinator, shell = create_application()
    except QtDependencyError as error:
        print(str(error), file=sys.stderr)
        return 2

    shell.show()
    coordinator.start()
    if args.file:
        coordinator.load_path(args.file)
    return app.exec()
