"""PySide6 foundation for the staged Varia Studio desktop migration."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Start the optional Qt interface without importing Qt at package import time."""
    from .bootstrap import main as bootstrap_main

    return bootstrap_main(argv)


__all__ = ["main"]
