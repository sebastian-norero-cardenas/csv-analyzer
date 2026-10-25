"""Toolkit-neutral state/view semantics for SP-GUI-01."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from csv_analyzer.analysis import build_filter_mask
from csv_analyzer.data import Dataset, ImportOptions, load_dataset
from csv_analyzer.plots import ScatterData, ScatterSpec, prepare_scatter
from csv_analyzer.state import AppState, FilterRule

TABLE_SELECTED = "Selected rows"
TABLE_ALL = "All rows"
SEARCH_SELECTED = "Selected rows"
SEARCH_WHOLE = "Whole dataset"
CONTAINS = "Contains"
EXACT = "Exact"
SOURCE_ROW = "Source Row ID"


@dataclass(frozen=True)
class SearchRequest:
    query: str
    scope: str = SEARCH_SELECTED
    target: str = SOURCE_ROW
    mode: str = CONTAINS


@dataclass
class ViewState:
    table_scope: str = TABLE_SELECTED
    sort_column: str | None = None
    descending: bool = False
    search: SearchRequest | None = None
    match_ids: tuple[int, ...] = ()
    match_cursor: int = -1
    visual_selection: tuple[int, ...] = ()
    visible_columns: tuple[str, ...] = ()
    dirty_plot: bool = False


class SpikeModel:
    """Common semantic oracle/adaptor used by both GUI spikes."""

    def __init__(self, visible_columns: Iterable[str] = ()) -> None:
        self.app_state = AppState()
        self.view = ViewState(visible_columns=tuple(visible_columns))
        self.dataset_serial = 0

    @property
    def dataset(self) -> Dataset | None:
        return self.app_state.dataset

    @property
    def source_ids(self) -> np.ndarray:
        if self.dataset is None:
            return np.array([], dtype=np.int64)
        return np.arange(1, len(self.dataset.frame) + 1, dtype=np.int64)

    def load(self, path: str | Path, options: ImportOptions = ImportOptions()) -> Dataset:
        ticket = self.app_state.begin_load()
        candidate = load_dataset(path, options)
        if not self.app_state.commit_dataset(ticket, candidate):
            raise RuntimeError("Dataset candidate became stale before commit")
        self.dataset_serial += 1
        self.view = ViewState(visible_columns=self.view.visible_columns)
        return candidate

    def begin_load(self) -> int:
        return self.app_state.begin_load()

    def cancel_load(self) -> None:
        self.app_state.cancel_load()

    def commit_candidate(self, ticket: int, candidate: Dataset) -> bool:
        committed = self.app_state.commit_dataset(ticket, candidate)
        if committed:
            self.dataset_serial += 1
            self.view = ViewState(visible_columns=self.view.visible_columns)
        return committed

    def apply_lower_filter(self, column: str, lower: int | float) -> int:
        self._require_dataset()
        revision = self.app_state.revision
        rule = FilterRule(column, lower=lower)
        mask = build_filter_mask(self.dataset.frame, (rule,))
        if not self.app_state.commit_filters(revision, (rule,), mask):
            raise RuntimeError("Filter result became stale before commit")
        self._recompute_search()
        self.view.dirty_plot = True
        return self.app_state.selected_count

    def clear_filter(self) -> None:
        self._require_dataset()
        revision = self.app_state.revision
        if not self.app_state.commit_filters(revision, (), None):
            raise RuntimeError("Filter clear became stale before commit")
        self._recompute_search()
        self.view.dirty_plot = True

    def set_scope(self, scope: str) -> None:
        if scope not in (TABLE_SELECTED, TABLE_ALL):
            raise ValueError(scope)
        self.view.table_scope = scope

    def selected_source_ids(self) -> np.ndarray:
        self._require_dataset()
        if self.app_state.mask is None:
            return self.source_ids
        return self.source_ids[self.app_state.mask]

    def scope_source_ids(self, scope: str | None = None) -> np.ndarray:
        scope = scope or self.view.table_scope
        if scope == TABLE_SELECTED:
            return self.selected_source_ids()
        if scope == TABLE_ALL:
            return self.source_ids
        raise ValueError(scope)

    def ordered_source_ids(self, scope: str | None = None) -> np.ndarray:
        ids = self.scope_source_ids(scope)
        if self.view.sort_column is None:
            return np.sort(ids)
        frame = self.dataset.frame.iloc[ids - 1]
        column = self.view.sort_column
        if column == SOURCE_ROW:
            return np.sort(ids)[::-1] if self.view.descending else np.sort(ids)
        work = pd.DataFrame({"value": frame[column].array, "source": ids})
        ordered = work.sort_values(
            ["value", "source"],
            ascending=[not self.view.descending, True],
            na_position="last",
            kind="mergesort",
        )
        return ordered["source"].to_numpy(dtype=np.int64)

    def sort(self, column: str, descending: bool = False) -> None:
        self._require_column(column)
        self.view.sort_column = column
        self.view.descending = bool(descending)
        self._recompute_search()

    def reset_order(self) -> None:
        self.view.sort_column = None
        self.view.descending = False
        self._recompute_search()

    def go_to_source_row(self, source_row_id: int) -> int:
        self._require_dataset()
        if source_row_id < 1 or source_row_id > len(self.dataset.frame):
            raise ValueError("Source Row ID is outside the current dataset")
        if self.view.table_scope == TABLE_SELECTED:
            mask = self.app_state.mask
            if mask is not None and not bool(mask[source_row_id - 1]):
                self.view.table_scope = TABLE_ALL
        ordered = self.ordered_source_ids()
        return int(np.flatnonzero(ordered == source_row_id)[0])

    def search(self, request: SearchRequest) -> tuple[int, ...]:
        self._validate_search(request)
        self.view.search = request
        self._recompute_search()
        return self.view.match_ids

    def clear_search(self) -> None:
        self.view.search = None
        self.view.match_ids = ()
        self.view.match_cursor = -1

    def navigate_match(self, step: int) -> int | None:
        if not self.view.match_ids:
            return None
        ordered = self._ordered_match_ids()
        current_id = None
        if 0 <= self.view.match_cursor < len(self.view.match_ids):
            current_id = self.view.match_ids[self.view.match_cursor]
        if current_id in ordered:
            index = ordered.index(current_id)
            index = (index + step) % len(ordered)
        else:
            index = 0 if step >= 0 else len(ordered) - 1
        source_row_id = ordered[index]
        self.view.match_ids = tuple(ordered)
        self.view.match_cursor = index
        self.go_to_source_row(source_row_id)
        return source_row_id

    def set_visual_selection(self, source_ids: Iterable[int]) -> None:
        available = set(self.scope_source_ids().tolist())
        selected = tuple(int(item) for item in source_ids if int(item) in available)
        self.view.visual_selection = selected

    def copy_tsv(self, source_ids: Iterable[int], columns: Iterable[str]) -> str:
        self._require_dataset()
        ids = [int(item) for item in source_ids]
        columns = tuple(columns)
        selected_ids = set(ids)
        ordered = [
            item for item in self.ordered_source_ids() if int(item) in selected_ids
        ]
        lines: list[str] = []
        for source_id in ordered:
            values = []
            for column in columns:
                value = (
                    source_id
                    if column == SOURCE_ROW
                    else self.dataset.frame.iloc[source_id - 1][column]
                )
                values.append(self._canonical(value))
            lines.append("\t".join(values))
        return "\n".join(lines)

    def scatter(self, spec: ScatterSpec) -> ScatterData:
        self._require_dataset()
        return prepare_scatter(self.dataset.frame, spec, self.app_state.mask)

    def _recompute_search(self) -> None:
        request = self.view.search
        if request is None or self.dataset is None:
            return
        scope_ids = (
            self.selected_source_ids()
            if request.scope == SEARCH_SELECTED
            else self.source_ids
        )
        matches = [
            source_id
            for source_id in scope_ids
            if self._row_matches(int(source_id), request)
        ]
        self.view.match_ids = tuple(int(item) for item in matches)
        self.view.match_cursor = -1

    def _ordered_match_ids(self) -> list[int]:
        membership = set(self.view.match_ids)
        scope = (
            TABLE_ALL
            if self.view.search and self.view.search.scope == SEARCH_WHOLE
            else TABLE_SELECTED
        )
        return [
            int(item)
            for item in self.ordered_source_ids(scope)
            if int(item) in membership
        ]

    def _row_matches(self, source_id: int, request: SearchRequest) -> bool:
        targets = (
            self.view.visible_columns
            if request.target == "All visible columns"
            else (request.target,)
        )
        for target in targets:
            value = (
                source_id
                if target == SOURCE_ROW
                else self.dataset.frame.iloc[source_id - 1][target]
            )
            if self._matches(value, request.query, request.mode):
                return True
        return False

    @staticmethod
    def _matches(value, query: str, mode: str) -> bool:
        if pd.isna(value):
            return False
        if mode == CONTAINS:
            return query.casefold() in SpikeModel._canonical(value).casefold()
        if mode != EXACT:
            raise ValueError(mode)
        if isinstance(value, (bool, np.bool_)):
            normalized = query.strip().casefold()
            if normalized not in ("true", "false"):
                return False
            return bool(value) is (normalized == "true")
        if isinstance(value, (int, np.integer)):
            try:
                return int(query.strip()) == int(value)
            except ValueError:
                return False
        if isinstance(value, (float, np.floating)):
            try:
                return float(query.strip()) == float(value)
            except ValueError:
                return False
        if isinstance(value, pd.Timestamp):
            try:
                return pd.Timestamp(query) == value
            except (TypeError, ValueError):
                return False
        return str(value) == query

    @staticmethod
    def _canonical(value) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, (bool, np.bool_)):
            return "True" if bool(value) else "False"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            return format(float(value), ".17g")
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return str(value)

    def _validate_search(self, request: SearchRequest) -> None:
        if request.scope not in (SEARCH_SELECTED, SEARCH_WHOLE):
            raise ValueError(request.scope)
        if request.mode not in (CONTAINS, EXACT):
            raise ValueError(request.mode)
        if request.target != "All visible columns":
            self._require_column(request.target)

    def _require_column(self, column: str) -> None:
        if column == SOURCE_ROW:
            return
        self._require_dataset()
        if column not in self.dataset.frame:
            raise KeyError(column)

    def _require_dataset(self) -> None:
        if self.dataset is None:
            raise RuntimeError("No dataset loaded")


def s200k_import_options() -> ImportOptions:
    return ImportOptions(date_columns=tuple(f"timestamp_{index:02d}" for index in range(6)))
