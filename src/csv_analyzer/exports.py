"""Atomic file exports shared by figures and statistics."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .data import DataError


@contextmanager
def atomic_destination(path: str | Path, *, overwrite: bool = False) -> Iterator[Path]:
    """Publish a complete file, leaving an existing destination intact on failure."""
    path = Path(path).expanduser().resolve()
    if path.exists() and not overwrite:
        raise DataError("This file already exists. Choose another name or confirm replacement.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as file:
            temporary = Path(file.name)
        yield temporary
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_statistics(frame: pd.DataFrame, path: str | Path, *, overwrite=False) -> Path:
    """UTF-8 CSV with headers, full computed precision, and no generated index."""
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".csv":
        raise DataError("Choose a .csv filename for statistics.")
    try:
        with atomic_destination(path, overwrite=overwrite) as temporary:
            frame.to_csv(temporary, index=False, encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise DataError(f"Could not export statistics: {exc}") from exc
    return path
