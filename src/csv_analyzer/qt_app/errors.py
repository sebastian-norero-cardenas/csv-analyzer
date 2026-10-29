"""Qt-facing error presentation without scientific or parsing logic."""

from __future__ import annotations

import logging

from ..data import CancelledError, DataError

logger = logging.getLogger(__name__)


def user_error_text(error: BaseException) -> str:
    """Return a concise user-facing message and log unexpected failures."""
    if isinstance(error, CancelledError):
        return "Operation cancelled."
    if isinstance(error, (DataError, ValueError, OSError)):
        return str(error)
    logger.error(
        "Unexpected Qt operation failure",
        exc_info=(type(error), error, error.__traceback__),
    )
    return f"Unexpected error: {error}"
