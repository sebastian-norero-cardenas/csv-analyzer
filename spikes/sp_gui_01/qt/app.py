"""PySide6/Qt Widgets candidate for the disposable SP-GUI-01 comparison."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from csv_analyzer.analysis import build_filter_mask
from csv_analyzer.data import CancelledError, ImportOptions, load_dataset
from csv_analyzer.plots import ScatterSpec, prepare_scatter, render_scatter
from csv_analyzer.state import FilterRule
from spikes.sp_gui_01.common.measure import Sample, current_rss_kib, write_json
from spikes.sp_gui_01.common.model import (
    CONTAINS,
    EXACT,
    SEARCH_SELECTED,
    SEARCH_WHOLE,
    SOURCE_ROW,
    TABLE_ALL,
    TABLE_SELECTED,
    SearchRequest,
    SpikeModel,
    s200k_import_options,
    search_source_ids,
    sort_source_ids,
)
from spikes.sp_gui_01.qt.table_model import FrameTableModel, MeasuredTableView
from spikes.sp_gui_01.qt.worker import QtTaskRunner

APP_TITLE = "Varia Studio — SP-GUI-01 PySide6/Qt"
DEFAULT_VISIBLE = (
    SOURCE_ROW,
    "int_exact",
    "sort_key",
    "int_nullable_00",
    "float_00",
    "float_01",
    "text_00",
    "category_00",
    "bool_00",
    "timestamp_00",
)


class QtSpikeWindow(QMainWindow):
    """Same functional shell as CTk, using native Qt model/view."""

    def __init__(self, metrics_path: Path | None = None):
        started = time.perf_counter()
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1366, 768)
        self.setMinimumSize(1050, 660)
        self.metrics_path = metrics_path
        self.samples: list[Sample] = []
        self.model = SpikeModel(DEFAULT_VISIBLE)
        self.runner = QtTaskRunner(self)
        self.runner.activity_changed.connect(self._activity_changed)
        self._active_path: Path | None = None
        self._ordered_ids = np.array([], dtype=np.int64)
        self._sort_column: str | None = None
        self._sort_descending = False
        self._search_ordered_matches: tuple[int, ...] = ()
        self._plot_figure = None
        self._plot_canvas = None
        self._plot_toolbar = None
        self._plot_revision: int | None = None
        self._build_shell()
        self._install_shortcuts()
        self._record("startup", started, toolkit="qt")

    def _build_shell(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(6)
        self.setCentralWidget(central)

        top = QHBoxLayout()
        brand = QLabel("Varia Studio")
        font = brand.font()
        font.setPointSize(18)
        font.setBold(True)
        brand.setFont(font)
        top.addWidget(brand)
        self.dataset_label = QLabel("No dataset loaded")
        top.addWidget(self.dataset_label, 1)
        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(self.open_file)
        top.addWidget(self.open_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_current)
        top.addWidget(self.cancel_button)
        root.addLayout(top)

        nav = QHBoxLayout()
        self.tabs = QTabBar()
        for name in ("Data", "Charts", "Statistics"):
            self.tabs.addTab(name)
        self.tabs.currentChanged.connect(self._show_area)
        nav.addWidget(self.tabs)
        self.status = QLabel("Ready")
        self.status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        nav.addWidget(self.status, 1)
        root.addLayout(nav)

        self.stack = QStackedWidget()
        self.data_area = self._build_data_area()
        self.chart_area = self._build_chart_area()
        self.statistics_area = QWidget()
        stats_layout = QVBoxLayout(self.statistics_area)
        stats_label = QLabel("Statistics\nNot implemented in spike")
        stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_font = stats_label.font()
        stats_font.setPointSize(16)
        stats_font.setBold(True)
        stats_label.setFont(stats_font)
        stats_layout.addWidget(stats_label)
        for widget in (
            self.data_area,
            self.chart_area,
            self.statistics_area,
        ):
            self.stack.addWidget(widget)
        root.addWidget(self.stack, 1)

    def _build_data_area(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Table scope"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItems([TABLE_SELECTED, TABLE_ALL])
        self.scope_combo.currentTextChanged.connect(self._scope_changed)
        row1.addWidget(self.scope_combo)
        self.row_count = QLabel("0 selected / 0 total")
        row1.addWidget(self.row_count)
        row1.addSpacing(12)
        row1.addWidget(QLabel("Go to Source Row ID"))
        self.goto_entry = QLineEdit()
        self.goto_entry.setMaximumWidth(110)
        row1.addWidget(self.goto_entry)
        go = QPushButton("Go")
        go.clicked.connect(self.go_to_source_row)
        row1.addWidget(go)
        row1.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(self.copy_selection)
        row1.addWidget(copy)
        reset = QPushButton("Reset order")
        reset.clicked.connect(self.reset_order)
        row1.addWidget(reset)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("Search")
        self.search_entry.setMaximumWidth(180)
        row2.addWidget(self.search_entry)
        self.search_scope = QComboBox()
        self.search_scope.addItems([SEARCH_SELECTED, SEARCH_WHOLE])
        row2.addWidget(self.search_scope)
        self.search_target = QComboBox()
        self.search_target.addItems([SOURCE_ROW, "All visible columns"])
        row2.addWidget(self.search_target)
        self.search_mode = QComboBox()
        self.search_mode.addItems([CONTAINS, EXACT])
        row2.addWidget(self.search_mode)
        search = QPushButton("Search")
        search.clicked.connect(self.run_search)
        row2.addWidget(search)
        previous = QPushButton("Previous")
        previous.clicked.connect(lambda: self.navigate_match(-1))
        row2.addWidget(previous)
        next_button = QPushButton("Next")
        next_button.clicked.connect(lambda: self.navigate_match(1))
        row2.addWidget(next_button)
        self.match_label = QLabel("0 matches")
        row2.addWidget(self.match_label)
        clear_search = QPushButton("Clear search")
        clear_search.clicked.connect(self.clear_search)
        row2.addWidget(clear_search)
        row2.addSpacing(8)
        row2.addWidget(QLabel("Filter >="))
        self.filter_column = QComboBox()
        row2.addWidget(self.filter_column)
        self.filter_value = QLineEdit()
        self.filter_value.setPlaceholderText("value")
        self.filter_value.setMaximumWidth(90)
        row2.addWidget(self.filter_value)
        apply_filter = QPushButton("Apply")
        apply_filter.clicked.connect(self.apply_filter)
        row2.addWidget(apply_filter)
        clear_filter = QPushButton("Clear")
        clear_filter.clicked.connect(self.clear_filter)
        row2.addWidget(clear_filter)
        layout.addLayout(row2)

        self.table_model = FrameTableModel(self)
        self.table = MeasuredTableView()
        self.table.setModel(self.table_model)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.table.setWordWrap(False)
        self.table.horizontalHeader().sectionClicked.connect(
            self.request_sort_by_section
        )
        self.table.selectionModel().selectionChanged.connect(
            self._visual_selection_changed
        )
        self.table.scroll_measured.connect(
            lambda seconds: self._record_duration(
                "scroll", seconds, rows=len(self._ordered_ids)
            )
        )
        layout.addWidget(self.table, 1)
        return widget

    def _build_chart_area(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("X"))
        self.x_combo = QComboBox()
        self.x_combo.currentTextChanged.connect(
            lambda _: self._mark_plot_dirty()
        )
        bar.addWidget(self.x_combo)
        bar.addWidget(QLabel("Y"))
        self.y_combo = QComboBox()
        self.y_combo.currentTextChanged.connect(
            lambda _: self._mark_plot_dirty()
        )
        bar.addWidget(self.y_combo)
        generate = QPushButton("Generate/Update")
        generate.clicked.connect(self.generate_plot)
        bar.addWidget(generate)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: self.cancel_job("plot"))
        bar.addWidget(cancel)
        self.plot_state = QLabel("No plot")
        bar.addWidget(self.plot_state)
        bar.addStretch(1)
        self.plot_population = QLabel("")
        bar.addWidget(self.plot_population)
        layout.addLayout(bar)
        self.canvas_host = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas_host)
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas_host, 1)
        return widget

    def _install_shortcuts(self) -> None:
        self.shortcut_open = QShortcut(QKeySequence("Ctrl+O"), self)
        self.shortcut_open.activated.connect(self.open_file)
        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.activated.connect(self.search_entry.setFocus)
        self.shortcut_reload = QShortcut(QKeySequence("Ctrl+R"), self)
        self.shortcut_reload.activated.connect(self.reload_file)
        self.shortcut_cancel = QShortcut(QKeySequence("Esc"), self)
        self.shortcut_cancel.activated.connect(self.cancel_current)

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open dataset",
            "",
            "Data files (*.csv *.dat *.tsv *.txt);;All files (*)",
        )
        if path:
            self.load_path(Path(path))

    def reload_file(self) -> None:
        if self._active_path is not None:
            self.load_path(self._active_path)

    def load_path(self, path: Path) -> None:
        ticket = self.model.begin_load()
        options = (
            s200k_import_options()
            if path.name == "S-200K-mixed.csv"
            else ImportOptions()
        )
        self._set_status(f"Loading {path.name}…")
        started = time.perf_counter()

        def work(cancel):
            return load_dataset(path, options, cancel=cancel)

        def success(dataset):
            self._record("load", started, dataset=path.name)
            if not self.model.commit_candidate(ticket, dataset):
                return
            self._active_path = path
            self._dataset_committed()

        def failure(error):
            if isinstance(error, CancelledError):
                return
            suffix = (
                f" Active dataset {self._active_path.name} is unchanged."
                if self._active_path is not None
                else ""
            )
            self._show_error(
                f"Could not load {path.name}: {error}.{suffix}"
            )

        self.runner.submit("load", work, success, failure)

    def _dataset_committed(self) -> None:
        paint_started = time.perf_counter()
        dataset = self.model.dataset
        frame = dataset.frame
        columns = self._visible_columns_for(frame)
        self.model.view.visible_columns = columns
        self._sort_column = None
        self._sort_descending = False
        self.model.view.sort_column = None
        self.model.view.descending = False
        self.model.view.table_scope = TABLE_SELECTED
        self.scope_combo.blockSignals(True)
        self.scope_combo.setCurrentText(TABLE_SELECTED)
        self.scope_combo.blockSignals(False)
        self._ordered_ids = self.model.source_ids
        self.table_model.set_sort_indicator(None)
        self.table_model.set_data(frame, columns, self._ordered_ids)
        self.dataset_label.setText(dataset.path.name)
        self._update_dataset_controls()
        self._update_counts()
        self.model.clear_search()
        self._search_ordered_matches = ()
        self.match_label.setText("0 matches")
        self.plot_state.setText("No plot")
        self._plot_revision = None
        self._set_status(f"Loaded {dataset.path.name}")
        QTimer.singleShot(
            0,
            lambda: self._record(
                "first_paint", paint_started, dataset=dataset.path.name
            ),
        )

    def _visible_columns_for(self, frame) -> tuple[str, ...]:
        preferred = tuple(
            column
            for column in DEFAULT_VISIBLE
            if column == SOURCE_ROW or column in frame
        )
        if len(preferred) > 1:
            return preferred
        return (SOURCE_ROW, *tuple(frame.columns[:9]))

    def _update_dataset_controls(self) -> None:
        visible = self.model.view.visible_columns
        self.search_target.clear()
        self.search_target.addItems(
            [
                SOURCE_ROW,
                "All visible columns",
                *[c for c in visible if c != SOURCE_ROW],
            ]
        )
        numeric = list(self.model.dataset.numeric_columns)
        self.filter_column.clear()
        self.filter_column.addItems(numeric)
        if numeric:
            preferred = (
                "int_nullable_00"
                if "int_nullable_00" in numeric
                else numeric[0]
            )
            self.filter_column.setCurrentText(preferred)
        self.x_combo.clear()
        self.y_combo.clear()
        self.x_combo.addItems(numeric)
        self.y_combo.addItems(numeric)
        if numeric:
            self.x_combo.setCurrentText(
                "float_00" if "float_00" in numeric else numeric[0]
            )
            self.y_combo.setCurrentText(
                "float_01"
                if "float_01" in numeric
                else numeric[min(1, len(numeric) - 1)]
            )

    def _scope_changed(self, scope: str) -> None:
        if self.model.dataset is None:
            return
        self.model.set_scope(scope)
        self._refresh_order_async(event="scope")

    def _refresh_order_async(
        self, *, focus_id: int | None = None, event: str = "sort"
    ) -> None:
        if self.model.dataset is None:
            return
        frame = self.model.dataset.frame
        ids = self.model.scope_source_ids().copy()
        column = self._sort_column
        descending = self._sort_descending
        search_request = self.model.view.search
        search_matches = self.model.view.match_ids
        search_ids = (
            self.model.selected_source_ids().copy()
            if search_request is not None
            and search_request.scope == SEARCH_SELECTED
            else self.model.source_ids.copy()
        )
        started = time.perf_counter()

        def work(cancel):
            if cancel.is_set():
                raise CancelledError()
            ordered = sort_source_ids(
                frame, ids, column, descending
            )
            nav = self._ordered_matches_for(
                frame,
                search_ids,
                search_request,
                search_matches,
                column,
                descending,
            )
            return ordered, nav

        def success(result):
            ordered, nav = result
            self._record(event, started, rows=len(ids))
            self._ordered_ids = ordered
            self._search_ordered_matches = nav
            self.table_model.set_data(
                frame, self.model.view.visible_columns, ordered
            )
            self.table_model.set_sort_indicator(column, descending)
            self._update_counts()
            if focus_id is not None:
                self._focus_source_id(focus_id)
            self._set_status("Ready")

        self.runner.submit(
            "order", work, success, self._show_error
        )

    def request_sort_by_section(self, section: int) -> None:
        if (
            self.model.dataset is None
            or section >= len(self.table_model.columns)
        ):
            return
        column = self.table_model.columns[section]
        if column == self._sort_column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = False
        self.model.view.sort_column = self._sort_column
        self.model.view.descending = self._sort_descending
        self._set_status(f"Sorting by {column}…")
        self._refresh_order_async(event="sort")

    def reset_order(self) -> None:
        self._sort_column = None
        self._sort_descending = False
        self.model.view.sort_column = None
        self.model.view.descending = False
        self._refresh_order_async(event="sort")

    def go_to_source_row(self) -> None:
        if self.model.dataset is None:
            return
        try:
            source_id = int(self.goto_entry.text().strip())
        except ValueError:
            self._show_error("Enter a valid Source Row ID.")
            return
        if source_id < 1 or source_id > len(self.model.dataset.frame):
            self._show_error(
                "Source Row ID is outside the current dataset."
            )
            return
        started = time.perf_counter()
        mask = self.model.app_state.mask
        excluded = (
            mask is not None and not bool(mask[source_id - 1])
        )
        if (
            self.model.view.table_scope == TABLE_SELECTED
            and excluded
        ):
            self.model.set_scope(TABLE_ALL)
            self.scope_combo.blockSignals(True)
            self.scope_combo.setCurrentText(TABLE_ALL)
            self.scope_combo.blockSignals(False)
            self._refresh_order_async(
                focus_id=source_id, event="go_to_source_row"
            )
        elif self._focus_source_id(source_id):
            self._record(
                "go_to_source_row",
                started,
                source_row_id=source_id,
            )

    def _focus_source_id(self, source_id: int) -> bool:
        row = self.table_model.row_for_source_id(source_id)
        if row is None:
            return False
        index = self.table_model.index(row, 0)
        self.table.scrollTo(
            index, QAbstractItemView.ScrollHint.PositionAtCenter
        )
        selection = self.table.selectionModel()
        selection.clearSelection()
        selection.select(
            index,
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.table.setCurrentIndex(index)
        return True

    def run_search(self) -> None:
        if self.model.dataset is None:
            return
        request = SearchRequest(
            query=self.search_entry.text(),
            scope=self.search_scope.currentText(),
            target=self.search_target.currentText(),
            mode=self.search_mode.currentText(),
        )
        frame = self.model.dataset.frame
        ids = (
            self.model.selected_source_ids()
            if request.scope == SEARCH_SELECTED
            else self.model.source_ids
        )
        visible = self.model.view.visible_columns
        started = time.perf_counter()
        self._set_status("Searching…")

        def work(cancel):
            if cancel.is_set():
                raise CancelledError()
            matches = search_source_ids(
                frame, ids, request, visible
            )
            nav = self._ordered_matches_for(
                frame,
                ids,
                request,
                matches,
                self._sort_column,
                self._sort_descending,
            )
            return matches, nav

        def success(result):
            matches, nav = result
            self._record(
                "search",
                started,
                rows=len(ids),
                matches=len(matches),
            )
            self.model.view.search = request
            self.model.view.match_ids = matches
            self.model.view.match_cursor = -1
            self._search_ordered_matches = nav
            self.match_label.setText(
                f"{len(matches):,} matches"
            )
            self._set_status("Ready")
            if matches:
                self.navigate_match(1)

        self.runner.submit(
            "search", work, success, self._show_error
        )

    def navigate_match(self, step: int) -> None:
        matches = self.model.view.match_ids
        if not matches:
            return
        ordered_matches = list(
            self._search_ordered_matches or matches
        )
        cursor = self.model.view.match_cursor
        current = (
            self.model.view.match_ids[cursor]
            if 0 <= cursor < len(self.model.view.match_ids)
            else None
        )
        if current in ordered_matches:
            index = (
                ordered_matches.index(current) + step
            ) % len(ordered_matches)
        else:
            index = (
                0 if step >= 0 else len(ordered_matches) - 1
            )
        source_id = ordered_matches[index]
        self.model.view.match_ids = tuple(ordered_matches)
        self.model.view.match_cursor = index
        self.match_label.setText(
            f"{index + 1:,} / "
            f"{len(ordered_matches):,} matches"
        )
        self.goto_entry.setText(str(source_id))
        mask = self.model.app_state.mask
        excluded = (
            mask is not None and not bool(mask[source_id - 1])
        )
        if (
            self.model.view.table_scope == TABLE_SELECTED
            and excluded
        ):
            self.model.set_scope(TABLE_ALL)
            self.scope_combo.blockSignals(True)
            self.scope_combo.setCurrentText(TABLE_ALL)
            self.scope_combo.blockSignals(False)
            self._refresh_order_async(
                focus_id=source_id,
                event="go_to_source_row",
            )
        else:
            self._focus_source_id(source_id)

    def clear_search(self) -> None:
        self.model.clear_search()
        self._search_ordered_matches = ()
        self.search_entry.clear()
        self.match_label.setText("0 matches")
        self._set_status("Search cleared")

    def _visual_selection_changed(
        self, selected, deselected
    ) -> None:
        rows = sorted(
            {
                index.row()
                for index in self.table.selectionModel().selectedIndexes()
            }
        )
        self.model.view.visual_selection = tuple(
            self.table_model.source_id_at(row)
            for row in rows
        )
        self._update_counts()

    def copy_selection(self) -> None:
        if self.model.dataset is None:
            return
        indexes = sorted(
            self.table.selectionModel().selectedIndexes(),
            key=lambda item: (item.row(), item.column()),
        )
        if not indexes:
            self._set_status(
                "Select one or more cells/rows to copy."
            )
            return
        lines: list[str] = []
        current_row = None
        values: list[str] = []
        for index in indexes:
            if (
                current_row is not None
                and index.row() != current_row
            ):
                lines.append("\t".join(values))
                values = []
            current_row = index.row()
            source_id = self.table_model.source_id_at(
                index.row()
            )
            column = self.table_model.columns[index.column()]
            value = (
                source_id
                if column == SOURCE_ROW
                else self.model.dataset.frame.iloc[
                    source_id - 1
                ][column]
            )
            values.append(self.model._canonical(value))
        if values:
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))
        self._set_status(
            f"Copied {len(indexes):,} underlying value(s)."
        )

    def apply_filter(self) -> None:
        if self.model.dataset is None:
            return
        column = self.filter_column.currentText()
        raw = self.filter_value.text().strip()
        series = self.model.dataset.frame[column]
        try:
            value = (
                int(raw)
                if pd.api.types.is_integer_dtype(series.dtype)
                else float(raw)
            )
        except ValueError:
            self._show_error(
                "Enter a numeric lower-bound filter value."
            )
            return
        revision = self.model.app_state.revision
        frame = self.model.dataset.frame
        rule = FilterRule(column, lower=value)
        started = time.perf_counter()
        self._set_status("Filtering…")

        def work(cancel):
            return build_filter_mask(
                frame, (rule,), cancel
            )

        def success(mask):
            if not self.model.app_state.commit_filters(
                revision, (rule,), mask
            ):
                return
            self._record(
                "filter", started, selected=int(mask.sum())
            )
            self.model.view.dirty_plot = True
            self._mark_plot_outdated()
            if self.model.view.search is not None:
                self.run_search()
            self._refresh_order_async(
                event="filter_view"
            )

        self.runner.submit(
            "filter", work, success, self._show_error
        )

    def clear_filter(self) -> None:
        if self.model.dataset is None:
            return
        revision = self.model.app_state.revision
        if self.model.app_state.commit_filters(
            revision, (), None
        ):
            self.model.view.dirty_plot = True
            self._mark_plot_outdated()
            if self.model.view.search is not None:
                self.run_search()
            self._refresh_order_async(
                event="filter_view"
            )

    @staticmethod
    def _ordered_matches_for(
        frame,
        source_ids,
        request,
        matches,
        column,
        descending,
    ) -> tuple[int, ...]:
        if request is None or not matches:
            return ()
        membership = set(int(item) for item in matches)
        ordered = sort_source_ids(
            frame, source_ids, column, descending
        )
        return tuple(
            int(item)
            for item in ordered
            if int(item) in membership
        )

    def _mark_plot_dirty(self) -> None:
        if self._plot_figure is not None:
            self.model.view.dirty_plot = True
            self.plot_state.setText("Changes not applied")

    def _mark_plot_outdated(self) -> None:
        if self._plot_figure is not None:
            self.plot_state.setText(
                "Outdated result — regenerate"
            )

    def generate_plot(self) -> None:
        if self.model.dataset is None:
            return
        x = self.x_combo.currentText()
        y = self.y_combo.currentText()
        if not x or not y:
            self._show_error("Choose X and Y columns.")
            return
        frame = self.model.dataset.frame
        mask = (
            None
            if self.model.app_state.mask is None
            else self.model.app_state.mask.copy()
        )
        revision = self.model.app_state.revision
        spec = ScatterSpec(
            x,
            y,
            sample_limit=5000,
            show_excluded=False,
        )
        started = time.perf_counter()
        self.plot_state.setText("Generating plot…")
        self._set_status("Generating plot…")

        def work(cancel):
            return prepare_scatter(
                frame, spec, mask, cancel=cancel
            )

        def success(data):
            if revision != self.model.app_state.revision:
                self.plot_state.setText(
                    "Outdated result — regenerate"
                )
                return
            self._record(
                "scatter_prepare",
                started,
                selected=data.selected,
                valid=data.valid,
            )
            render_started = time.perf_counter()
            figure = render_scatter(data, spec)
            self._install_figure(figure)
            self._record(
                "scatter_redraw",
                render_started,
                drawn=len(data.x),
            )
            self._plot_revision = revision
            self.model.view.dirty_plot = False
            self.plot_state.setText("Current")
            self.plot_population.setText(data.description)
            self._set_status("Plot updated")

        self.runner.submit(
            "plot", work, success, self._show_error
        )

    def _install_figure(self, figure) -> None:
        for widget in (
            self._plot_toolbar,
            self._plot_canvas,
        ):
            if widget is not None:
                self.canvas_layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._plot_figure = figure
        self._plot_canvas = FigureCanvasQTAgg(figure)
        self._plot_toolbar = NavigationToolbar2QT(
            self._plot_canvas, self
        )
        self.canvas_layout.addWidget(self._plot_toolbar)
        self.canvas_layout.addWidget(self._plot_canvas, 1)
        self._plot_canvas.draw()

    def _show_area(self, index: int) -> None:
        started = time.perf_counter()
        self.stack.setCurrentIndex(index)
        self._record(
            "area_switch",
            started,
            area=self.tabs.tabText(index),
        )

    def _update_counts(self) -> None:
        if self.model.dataset is None:
            self.row_count.setText(
                "0 selected / 0 total"
            )
            return
        total = len(self.model.dataset.frame)
        selected = self.model.app_state.selected_count
        visual = len(self.model.view.visual_selection)
        self.row_count.setText(
            f"{selected:,} selected / {total:,} total · "
            f"{visual:,} visually selected"
        )
        if selected == 0:
            self._set_status(
                f"0 selected / {total:,} total — "
                "Clear filter to restore rows."
            )

    def _activity_changed(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)

    def cancel_job(self, key: str) -> None:
        started = time.perf_counter()
        self.runner.cancel(key)
        if key == "load":
            self.model.cancel_load()
        self._set_status("Result cancelled")
        if key == "plot":
            self.plot_state.setText("Result cancelled")
        self._record(
            "cancel_feedback", started, key=key
        )

    def cancel_current(self) -> None:
        if "plot" in self.runner.jobs:
            self.cancel_job("plot")
        elif "load" in self.runner.jobs:
            self.cancel_job("load")
        elif self.runner.jobs:
            self.cancel_job(next(iter(self.runner.jobs)))

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _show_error(self, error) -> None:
        if isinstance(error, CancelledError):
            return
        self._set_status(str(error))
        QMessageBox.critical(
            self, "Varia Studio spike", str(error)
        )

    def _record(
        self, event: str, started: float, **metadata
    ) -> None:
        self.samples.append(
            Sample(
                event,
                time.perf_counter() - started,
                current_rss_kib(),
                metadata,
            )
        )

    def _record_duration(
        self, event: str, seconds: float, **metadata
    ) -> None:
        self.samples.append(
            Sample(
                event,
                seconds,
                current_rss_kib(),
                metadata,
            )
        )

    def resizeEvent(self, event) -> None:
        started = time.perf_counter()
        super().resizeEvent(event)
        self._record(
            "resize",
            started,
            width=event.size().width(),
            height=event.size().height(),
        )

    def closeEvent(self, event) -> None:
        if self.metrics_path:
            write_json(
                self.metrics_path,
                self.samples,
                {
                    "toolkit": "PySide6/Qt Widgets",
                    "dataset": str(self._active_path or ""),
                    "candidate": "SP-GUI-01",
                },
            )
        self.runner.close()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--metrics", type=Path)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = QtSpikeWindow(args.metrics)
    window.show()
    if args.file:
        QTimer.singleShot(
            100, lambda: window.load_path(args.file)
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
