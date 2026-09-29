"""Central dataset ownership and atomic, revision-checked state transitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .data import Dataset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterRule:
    column: str
    lower: float | None = None
    upper: float | None = None


class AppState:
    """Mutate on the GUI thread; workers receive stable dataset references.

    Consumers must not modify ``dataset.frame``. Loading candidates and derived
    arrays are built separately, then installed in one transition. The row mask
    is read-only and uses positional indexing, never potentially duplicate labels.
    """

    def __init__(self) -> None:
        self.dataset: Dataset | None = None
        self.revision = 0
        self.filters: tuple[FilterRule, ...] = ()
        self.mask: np.ndarray | None = None
        self._load_ticket = 0
        self._listeners: list[Callable[[str], None]] = []

    def subscribe(self, callback: Callable[[str], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self, reason: str) -> None:
        for callback in tuple(self._listeners):
            try:
                callback(reason)
            except Exception:
                logger.exception("View refresh failed after %s", reason)

    def begin_load(self) -> int:
        self._load_ticket += 1
        return self._load_ticket

    def cancel_load(self) -> None:
        self._load_ticket += 1

    def commit_dataset(self, ticket: int, dataset: Dataset) -> bool:
        if ticket != self._load_ticket:
            return False
        self.dataset = dataset
        self.filters = ()
        self.mask = None
        self.revision += 1
        self._notify("dataset")
        return True

    def commit_filters(
        self, revision: int, rules: tuple[FilterRule, ...], mask: np.ndarray | None
    ) -> bool:
        if revision != self.revision or self.dataset is None:
            return False
        if mask is not None:
            if mask.dtype != np.bool_ or mask.shape != (len(self.dataset.frame),):
                raise ValueError("Filter mask must contain one boolean per dataset row.")
            mask = mask.copy()
            mask.flags.writeable = False
        self.filters = rules
        self.mask = mask
        self.revision += 1
        self._notify("filters")
        return True

    @property
    def selected_count(self) -> int:
        if self.dataset is None:
            return 0
        return len(self.dataset.frame) if self.mask is None else int(self.mask.sum())
