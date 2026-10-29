"""Central dataset ownership and atomic, revision-checked state transitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

import numpy as np

from .data import Dataset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterRule:
    column: str
    lower: int | float | Decimal | None = None
    upper: int | float | Decimal | None = None


@dataclass(frozen=True)
class StateSnapshot:
    """Toolkit-neutral immutable view of committed application state."""

    dataset: Dataset | None
    revision: int
    dataset_revision: int
    selection_revision: int
    filters: tuple[FilterRule, ...]
    mask: np.ndarray | None

    @property
    def selected_count(self) -> int:
        if self.dataset is None:
            return 0
        return len(self.dataset.frame) if self.mask is None else int(self.mask.sum())


class AppState:
    """Own committed data; mutate only on the GUI thread.

    Workers receive StateSnapshot values or stable dataset references, never this
    live object. The compatibility revision remains available to the CTk app;
    dataset and selection revisions provide finer Qt invalidation domains.
    """

    def __init__(self) -> None:
        self.dataset: Dataset | None = None
        self.revision = 0
        self.dataset_revision = 0
        self.selection_revision = 0
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

    def snapshot(self) -> StateSnapshot:
        """Return stable committed references plus revision tokens."""
        return StateSnapshot(
            dataset=self.dataset,
            revision=self.revision,
            dataset_revision=self.dataset_revision,
            selection_revision=self.selection_revision,
            filters=self.filters,
            mask=self.mask,
        )

    @property
    def load_ticket(self) -> int:
        return self._load_ticket

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
        self.dataset_revision += 1
        self.selection_revision += 1
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
        self.selection_revision += 1
        self._notify("filters")
        return True

    @property
    def selected_count(self) -> int:
        if self.dataset is None:
            return 0
        return len(self.dataset.frame) if self.mask is None else int(self.mask.sum())
