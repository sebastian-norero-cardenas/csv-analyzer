"""Shared GUI workflow for safe export replacement confirmation."""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox
from typing import Callable, TypeVar

from ..exports import ExportConflictError

T = TypeVar("T")


def export_with_confirmation(
    parent,
    path: str | Path,
    writer: Callable[[bool], T],
) -> T | None:
    """Export without replacement first; retry only after explicit confirmation."""
    try:
        return writer(False)
    except ExportConflictError:
        destination = Path(path).expanduser()
        confirmed = messagebox.askyesno(
            "Replace file?",
            (
                f"The destination now exists:\n{destination}\n\n"
                "Replace it with this export?"
            ),
            parent=parent,
            icon="warning",
            default="no",
        )
        if not confirmed:
            return None
        return writer(True)
