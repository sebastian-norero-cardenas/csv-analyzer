"""CTk/ttk candidate for the disposable SP-GUI-01 comparison."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from tkinter import TclError, filedialog, messagebox

import customtkinter as ctk
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from csv_analyzer.analysis import build_filter_mask
from csv_analyzer.data import CancelledError, ImportOptions, load_dataset
from csv_analyzer.jobs import TaskRunner
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
from spikes.sp_gui_01.ctk.virtual_table import VirtualTreeTable

APP_TITLE = "Varia Studio — SP-GUI-01 CTk/ttk"
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


class CtkSpikeApp(ctk.CTk):
    """Functional comparison shell. This is intentionally not production UI."""

    def __init__(self, metrics_path: Path | None = None):
        started = time.perf_counter()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1366x768")
        self.minsize(1050, 660)
        self.metrics_path = metrics_path
        self.samples: list[Sample] = []
        self.model = SpikeModel(DEFAULT_VISIBLE)
        self._closed = False
        self.jobs = TaskRunner(self, self._activity_changed)
        self._active_path: Path | None = None
        self._ordered_ids = np.array([], dtype=np.int64)
        self._sort_column: str | None = None
        self._sort_descending = False
        self._plot_figure = None
        self._plot_canvas = None
        self._plot_toolbar = None
        self._plot_revision: int | None = None
        self._search_ordered_matches: tuple[int, ...] = ()
        self._resize_after = None
        self._last_size = None
        self._build_shell()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-o>", lambda event: self.open_file())
        self.bind("<Control-f>", lambda event: self.search_entry.focus_set())
        self.bind("<Control-r>", lambda event: self.reload_file())
        self.bind("<Escape>", lambda event: self.cancel_current())
        self.bind("<Configure>", self._resize_event)
        self.update_idletasks()
        self._record("startup", started, toolkit="ctk")

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Varia Studio", font=("", 24, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.dataset_label = ctk.CTkLabel(top, text="No dataset loaded", anchor="w")
        self.dataset_label.grid(row=0, column=1, sticky="ew", padx=18)
        ctk.CTkButton(top, text="Open…", width=92, command=self.open_file).grid(
            row=0, column=2, padx=(6, 0)
        )
        self.cancel_button = ctk.CTkButton(
            top, text="Cancel", width=92, command=self.cancel_current, state="disabled"
        )
        self.cancel_button.grid(row=0, column=3, padx=(6, 0))

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.area = ctk.CTkSegmentedButton(
            nav, values=["Data", "Charts", "Statistics"], command=self._show_area
        )
        self.area.pack(side="left")
        self.area.set("Data")
        self.status = ctk.CTkLabel(nav, text="Ready", anchor="e")
        self.status.pack(side="right", fill="x", expand=True, padx=(12, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)
        self.data_area = self._build_data_area(self.body)
        self.chart_area = self._build_chart_area(self.body)
        self.statistics_area = ctk.CTkFrame(self.body)
        ctk.CTkLabel(
            self.statistics_area,
            text="Statistics\nNot implemented in spike",
            font=("", 20, "bold"),
        ).place(relx=0.5, rely=0.45, anchor="center")
        for frame in (self.data_area, self.chart_area, self.statistics_area):
            frame.grid(row=0, column=0, sticky="nsew")
        self.data_area.tkraise()

    def _build_data_area(self, master):
        frame = ctk.CTkFrame(master)
        frame.grid_rowconfigure(3, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(row1, text="Table scope").pack(side="left")
        self.scope_menu = ctk.CTkOptionMenu(
            row1,
            values=[TABLE_SELECTED, TABLE_ALL],
            command=self._scope_changed,
            width=130,
        )
        self.scope_menu.set(TABLE_SELECTED)
        self.scope_menu.pack(side="left", padx=(5, 12))
        self.row_count = ctk.CTkLabel(row1, text="0 selected / 0 total")
        self.row_count.pack(side="left")
        ctk.CTkLabel(row1, text="Go to Source Row ID").pack(
            side="left", padx=(18, 4)
        )
        self.goto_entry = ctk.CTkEntry(row1, width=100)
        self.goto_entry.pack(side="left")
        ctk.CTkButton(
            row1, text="Go", width=55, command=self.go_to_source_row
        ).pack(side="left", padx=(4, 12))
        ctk.CTkButton(
            row1, text="Reset order", width=95, command=self.reset_order
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            row1, text="Copy", width=70, command=self.copy_selection
        ).pack(side="right")

        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.search_entry = ctk.CTkEntry(
            row2, width=170, placeholder_text="Search"
        )
        self.search_entry.pack(side="left")
        self.search_scope = ctk.CTkOptionMenu(
            row2, values=[SEARCH_SELECTED, SEARCH_WHOLE], width=125
        )
        self.search_scope.set(SEARCH_SELECTED)
        self.search_scope.pack(side="left", padx=3)
        self.search_target = ctk.CTkOptionMenu(
            row2, values=[SOURCE_ROW, "All visible columns"], width=150
        )
        self.search_target.set(SOURCE_ROW)
        self.search_target.pack(side="left", padx=3)
        self.search_mode = ctk.CTkOptionMenu(
            row2, values=[CONTAINS, EXACT], width=90
        )
        self.search_mode.set(CONTAINS)
        self.search_mode.pack(side="left", padx=3)
        ctk.CTkButton(
            row2, text="Search", width=70, command=self.run_search
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            row2, text="Previous", width=74, command=lambda: self.navigate_match(-1)
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            row2, text="Next", width=58, command=lambda: self.navigate_match(1)
        ).pack(side="left", padx=2)
        self.match_label = ctk.CTkLabel(row2, text="0 matches")
        self.match_label.pack(side="left", padx=4)
        ctk.CTkButton(
            row2, text="Clear search", width=88, command=self.clear_search
        ).pack(side="left", padx=(0, 8))

        filter_row = ctk.CTkFrame(frame, fg_color="transparent")
        filter_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))
        ctk.CTkLabel(filter_row, text="Filter >=").pack(side="left")
        self.filter_column = ctk.CTkOptionMenu(filter_row, values=[""], width=150)
        self.filter_column.pack(side="left", padx=4)
        self.filter_value = ctk.CTkEntry(
            filter_row, width=100, placeholder_text="value"
        )
        self.filter_value.pack(side="left")
        ctk.CTkButton(
            filter_row, text="Apply", width=64, command=self.apply_filter
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            filter_row, text="Clear", width=64, command=self.clear_filter
        ).pack(side="left")

        self.table = VirtualTreeTable(
            frame,
            on_sort=self.request_sort,
            on_selection=self._visual_selection_changed,
            on_scroll=lambda seconds: self._record_duration(
                "scroll", seconds, rows=len(self._ordered_ids)
            ),
        )
        self.table.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        return frame

    def _build_chart_area(self, master):
        frame = ctk.CTkFrame(master)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(bar, text="X").pack(side="left")
        self.x_menu = ctk.CTkOptionMenu(
            bar, values=[""], width=150, command=lambda _: self._mark_plot_dirty()
        )
        self.x_menu.pack(side="left", padx=4)
        ctk.CTkLabel(bar, text="Y").pack(side="left", padx=(10, 0))
        self.y_menu = ctk.CTkOptionMenu(
            bar, values=[""], width=150, command=lambda _: self._mark_plot_dirty()
        )
        self.y_menu.pack(side="left", padx=4)
        ctk.CTkButton(
            bar, text="Generate/Update", width=130, command=self.generate_plot
        ).pack(side="left", padx=(10, 4))
        ctk.CTkButton(
            bar, text="Cancel", width=72, command=lambda: self.cancel_job("plot")
        ).pack(side="left")
        self.plot_state = ctk.CTkLabel(bar, text="No plot")
        self.plot_state.pack(side="left", padx=12)
        self.plot_population = ctk.CTkLabel(bar, text="")
        self.plot_population.pack(side="right")
        self.canvas_host = ctk.CTkFrame(frame)
        self.canvas_host.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.canvas_host.grid_columnconfigure(0, weight=1)
        self.canvas_host.grid_rowconfigure(0, weight=1)
        return frame

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open dataset",
            filetypes=[
                ("Data files", "*.csv *.dat *.tsv *.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.load_path(Path(path))

    def reload_file(self) -> None:
        if self._active_path is not None:
            self.load_path(self._active_path)

    def load_path(self, path: Path) -> None:
        for key in ("filter", "search", "order", "plot"):
            self.jobs.cancel(key)
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
            self._show_error(f"Could not load {path.name}: {error}.{suffix}")

        self.jobs.submit("load", work, success, failure)

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
        self.scope_menu.set(TABLE_SELECTED)
        self._ordered_ids = self.model.source_ids
        self.table.set_sort_indicator(None)
        self.table.set_data(frame, columns, self._ordered_ids)
        self.dataset_label.configure(text=dataset.path.name)
        self._update_dataset_controls()
        self._update_counts()
        self.match_label.configure(text="0 matches")
        self._search_ordered_matches = ()
        self.plot_state.configure(text="No plot")
        self._plot_revision = None
        self._set_status(f"Loaded {dataset.path.name}")
        self.after_idle(
            lambda: self._record(
                "first_paint", paint_started, dataset=dataset.path.name
            )
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
        search_values = [
            SOURCE_ROW,
            "All visible columns",
            *[c for c in visible if c != SOURCE_ROW],
        ]
        self.search_target.configure(values=search_values)
        self.search_target.set(SOURCE_ROW)
        numeric = list(self.model.dataset.numeric_columns)
        self.filter_column.configure(values=numeric or [""])
        if numeric:
            preferred = (
                "int_nullable_00"
                if "int_nullable_00" in numeric
                else numeric[0]
            )
            self.filter_column.set(preferred)
        self.x_menu.configure(values=numeric or [""])
        self.y_menu.configure(values=numeric or [""])
        if numeric:
            x = "float_00" if "float_00" in numeric else numeric[0]
            y = (
                "float_01"
                if "float_01" in numeric
                else numeric[min(1, len(numeric) - 1)]
            )
            self.x_menu.set(x)
            self.y_menu.set(y)

    def _scope_changed(self, scope: str) -> None:
        if self.model.dataset is None:
            return
        self.model.set_scope(scope)
        self._refresh_order_async(event="scope")

    def _refresh_order_async(
        self,
        *,
        focus_id: int | None = None,
        event: str = "sort",
        after=None,
    ) -> None:
        if self.model.dataset is None:
            return
        frame = self.model.dataset.frame
        ids = self.model.scope_source_ids().copy()
        column = self._sort_column
        descending = self._sort_descending
        started = time.perf_counter()

        search_request = self.model.view.search
        search_matches = self.model.view.match_ids
        search_ids = (
            self.model.selected_source_ids().copy()
            if search_request is not None and search_request.scope == SEARCH_SELECTED
            else self.model.source_ids.copy()
        )

        def work(cancel):
            if cancel.is_set():
                raise CancelledError()
            ordered = sort_source_ids(frame, ids, column, descending)
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
            self.table.set_data(
                frame,
                self.model.view.visible_columns,
                ordered,
                keep_position=False,
            )
            self.table.set_sort_indicator(column, descending)
            self._update_counts()
            if focus_id is not None:
                self.table.focus_source_id(focus_id)
            self._set_status("Ready")
            if after is not None:
                after()

        self.jobs.submit("order", work, success, self._show_error)

    def request_sort(self, column: str) -> None:
        if self.model.dataset is None:
            return
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
            source_id = int(self.goto_entry.get().strip())
        except ValueError:
            self._show_error("Enter a valid Source Row ID.")
            return
        if source_id < 1 or source_id > len(self.model.dataset.frame):
            self._show_error("Source Row ID is outside the current dataset.")
            return
        started = time.perf_counter()
        mask = self.model.app_state.mask
        excluded = mask is not None and not bool(mask[source_id - 1])
        if self.model.view.table_scope == TABLE_SELECTED and excluded:
            self.model.set_scope(TABLE_ALL)
            self.scope_menu.set(TABLE_ALL)
            self._refresh_order_async(
                focus_id=source_id, event="go_to_source_row"
            )
            return
        if self.table.focus_source_id(source_id):
            self._record(
                "go_to_source_row", started, source_row_id=source_id
            )

    def run_search(self) -> None:
        if self.model.dataset is None:
            return
        request = SearchRequest(
            query=self.search_entry.get(),
            scope=self.search_scope.get(),
            target=self.search_target.get(),
            mode=self.search_mode.get(),
        )
        frame = self.model.dataset.frame
        ids = (
            self.model.selected_source_ids()
            if request.scope == SEARCH_SELECTED
            else self.model.source_ids
        )
        visible = self.model.view.visible_columns
        sort_snapshot = (self._sort_column, self._sort_descending)
        started = time.perf_counter()
        self._set_status("Searching…")

        def work(cancel):
            if cancel.is_set():
                raise CancelledError()
            matches = search_source_ids(frame, ids, request, visible)
            nav = self._ordered_matches_for(
                frame,
                ids,
                request,
                matches,
                sort_snapshot[0],
                sort_snapshot[1],
            )
            return matches, nav

        def success(result):
            matches, nav = result
            self._record(
                "search", started, rows=len(ids), matches=len(matches)
            )
            self.model.view.search = request
            self.model.view.match_ids = matches
            self.model.view.match_cursor = -1
            self._search_ordered_matches = nav
            self.match_label.configure(text=f"{len(matches):,} matches")
            self._set_status("Ready")
            if sort_snapshot != (self._sort_column, self._sort_descending):
                self._refresh_order_async(event="search_reorder")
                return
            if matches:
                self.navigate_match(1)

        self.jobs.submit("search", work, success, self._show_error)

    def navigate_match(self, step: int) -> None:
        matches = self.model.view.match_ids
        if not matches:
            return
        ordered_matches = list(self._search_ordered_matches or matches)
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
            index = 0 if step >= 0 else len(ordered_matches) - 1
        source_id = ordered_matches[index]
        self.model.view.match_ids = tuple(ordered_matches)
        self.model.view.match_cursor = index
        self.match_label.configure(
            text=f"{index + 1:,} / {len(ordered_matches):,} matches"
        )
        self.goto_entry.delete(0, "end")
        self.goto_entry.insert(0, str(source_id))
        mask = self.model.app_state.mask
        excluded = mask is not None and not bool(mask[source_id - 1])
        if self.model.view.table_scope == TABLE_SELECTED and excluded:
            self.model.set_scope(TABLE_ALL)
            self.scope_menu.set(TABLE_ALL)
            self._refresh_order_async(
                focus_id=source_id, event="go_to_source_row"
            )
        else:
            self.table.focus_source_id(source_id)

    def clear_search(self) -> None:
        self.model.clear_search()
        self.search_entry.delete(0, "end")
        self.match_label.configure(text="0 matches")
        self._search_ordered_matches = ()
        self._set_status("Search cleared")

    def _visual_selection_changed(
        self, source_ids: tuple[int, ...]
    ) -> None:
        self.model.view.visual_selection = source_ids
        self._update_counts()

    def copy_selection(self) -> None:
        if self.model.dataset is None:
            return
        selected = self.table.all_selected_source_ids()
        if not selected:
            self._set_status("Select one or more rows to copy.")
            return
        selected_set = set(selected)
        lines = []
        for source_id in self._ordered_ids:
            source_id = int(source_id)
            if source_id not in selected_set:
                continue
            values = []
            for column in self.model.view.visible_columns:
                value = (
                    source_id
                    if column == SOURCE_ROW
                    else self.model.dataset.frame.iloc[source_id - 1][column]
                )
                values.append(self.model._canonical(value))
            lines.append("\t".join(values))
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status(
            f"Copied {len(lines):,} row(s) with underlying values."
        )

    def apply_filter(self) -> None:
        if self.model.dataset is None:
            return
        column = self.filter_column.get()
        raw = self.filter_value.get().strip()
        series = self.model.dataset.frame[column]
        try:
            value = (
                int(raw)
                if pd.api.types.is_integer_dtype(series.dtype)
                else float(raw)
            )
        except ValueError:
            self._show_error("Enter a numeric lower-bound filter value.")
            return
        self.jobs.cancel("order")
        self.jobs.cancel("search")
        revision = self.model.app_state.revision
        frame = self.model.dataset.frame
        rule = FilterRule(column, lower=value)
        started = time.perf_counter()
        self._set_status("Filtering…")

        def work(cancel):
            return build_filter_mask(frame, (rule,), cancel)

        def success(mask):
            if not self.model.app_state.commit_filters(
                revision, (rule,), mask
            ):
                return
            self._record("filter", started, selected=int(mask.sum()))
            self.model.view.dirty_plot = True
            self._mark_plot_outdated()
            search_active = self.model.view.search is not None
            self._refresh_order_async(
                event="filter_view",
                after=self.run_search if search_active else None,
            )

        self.jobs.submit("filter", work, success, self._show_error)

    def clear_filter(self) -> None:
        if self.model.dataset is None:
            return
        revision = self.model.app_state.revision
        if self.model.app_state.commit_filters(revision, (), None):
            self.jobs.cancel("order")
            self.jobs.cancel("search")
            self.model.view.dirty_plot = True
            self._mark_plot_outdated()
            search_active = self.model.view.search is not None
            self._refresh_order_async(
                event="filter_view",
                after=self.run_search if search_active else None,
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
        ordered = sort_source_ids(frame, source_ids, column, descending)
        return tuple(int(item) for item in ordered if int(item) in membership)

    def _mark_plot_dirty(self) -> None:
        if self._plot_figure is not None:
            self.model.view.dirty_plot = True
            self.plot_state.configure(text="Changes not applied")

    def _mark_plot_outdated(self) -> None:
        if self._plot_figure is not None:
            self.plot_state.configure(
                text="Outdated result — regenerate"
            )

    def generate_plot(self) -> None:
        if self.model.dataset is None:
            return
        x, y = self.x_menu.get(), self.y_menu.get()
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
            x, y, sample_limit=5000, show_excluded=False
        )
        started = time.perf_counter()
        self.plot_state.configure(text="Generating plot…")
        self._set_status("Generating plot…")

        def work(cancel):
            return prepare_scatter(frame, spec, mask, cancel=cancel)

        def success(data):
            if revision != self.model.app_state.revision:
                self.plot_state.configure(
                    text="Outdated result — regenerate"
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
                "scatter_redraw", render_started, drawn=len(data.x)
            )
            self._plot_revision = revision
            self.model.view.dirty_plot = False
            self.plot_state.configure(text="Current")
            self.plot_population.configure(text=data.description)
            self._set_status("Plot updated")

        self.jobs.submit("plot", work, success, self._show_error)

    def _install_figure(self, figure) -> None:
        if self._plot_toolbar is not None:
            self._plot_toolbar.destroy()
            self._plot_toolbar = None
        if self._plot_canvas is not None:
            self._plot_canvas.get_tk_widget().destroy()
            self._plot_canvas = None
        self._plot_figure = figure
        self._plot_canvas = FigureCanvasTkAgg(
            figure, master=self.canvas_host
        )
        widget = self._plot_canvas.get_tk_widget()
        widget.grid(row=0, column=0, sticky="nsew")
        self._plot_canvas.draw()
        self._plot_toolbar = NavigationToolbar2Tk(
            self._plot_canvas, self.canvas_host, pack_toolbar=False
        )
        self._plot_toolbar.grid(row=1, column=0, sticky="ew")

    def _show_area(self, name: str) -> None:
        started = time.perf_counter()
        {
            "Data": self.data_area,
            "Charts": self.chart_area,
            "Statistics": self.statistics_area,
        }[name].tkraise()
        self._record("area_switch", started, area=name)

    def _update_counts(self) -> None:
        if self.model.dataset is None:
            self.row_count.configure(text="0 selected / 0 total")
            return
        total = len(self.model.dataset.frame)
        selected = self.model.app_state.selected_count
        visual = len(self.table.all_selected_source_ids())
        self.row_count.configure(
            text=(
                f"{selected:,} selected / {total:,} total · "
                f"{visual:,} visually selected"
            )
        )
        if selected == 0:
            self._set_status(
                f"0 selected / {total:,} total — Clear filter to restore rows."
            )

    def _activity_changed(self, busy: bool) -> None:
        if self._closed:
            return
        self.cancel_button.configure(
            state="normal" if busy else "disabled"
        )

    def cancel_job(self, key: str) -> None:
        started = time.perf_counter()
        self.jobs.cancel(key)
        if key == "load":
            self.model.cancel_load()
        self._set_status("Result cancelled")
        if key == "plot":
            self.plot_state.configure(text="Result cancelled")
        self._record("cancel_feedback", started, key=key)

    def cancel_current(self) -> None:
        if "plot" in self.jobs.jobs:
            self.cancel_job("plot")
        elif "load" in self.jobs.jobs:
            self.cancel_job("load")
        elif self.jobs.jobs:
            self.cancel_job(next(iter(self.jobs.jobs)))

    def _resize_event(self, event) -> None:
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except TclError:
                pass
        started = time.perf_counter()
        self._resize_after = self.after(
            120,
            lambda: self._record("resize", started, width=size[0], height=size[1]),
        )

    def _record_duration(self, event: str, seconds: float, **metadata) -> None:
        self.samples.append(Sample(event, seconds, current_rss_kib(), metadata))

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _show_error(self, error) -> None:
        if isinstance(error, CancelledError):
            return
        self._set_status(str(error))
        messagebox.showerror(
            "Varia Studio spike", str(error), parent=self
        )

    def _record(self, event: str, started: float, **metadata) -> None:
        self.samples.append(
            Sample(
                event,
                time.perf_counter() - started,
                current_rss_kib(),
                metadata,
            )
        )

    def _record_now(self, event: str, **metadata) -> None:
        self.samples.append(
            Sample(event, 0.0, current_rss_kib(), metadata)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.metrics_path:
            write_json(
                self.metrics_path,
                self.samples,
                {
                    "toolkit": "CTk/ttk",
                    "dataset": str(self._active_path or ""),
                    "candidate": "SP-GUI-01",
                },
            )
        self.jobs.close()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--metrics", type=Path)
    args = parser.parse_args(argv)
    try:
        app = CtkSpikeApp(args.metrics)
    except TclError as exc:
        print(f"Cannot start CTk spike: {exc}", file=sys.stderr)
        return 1
    if args.file:
        app.after(100, lambda: app.load_path(args.file))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
