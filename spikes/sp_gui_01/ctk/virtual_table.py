"""Virtualized ttk.Treeview adapter for the CTk SP-GUI-01 candidate."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import time

import customtkinter as ctk
import numpy as np
import pandas as pd
from tkinter import ttk

from spikes.sp_gui_01.common.model import SOURCE_ROW


class VirtualTreeTable(ctk.CTkFrame):
    """Fixed-size Treeview window over a global Source Row ordering."""

    def __init__(
        self,
        master,
        *,
        window_rows: int = 120,
        on_sort: Callable[[str], None] | None = None,
        on_selection: Callable[[tuple[int, ...]], None] | None = None,
        on_scroll: Callable[[float], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self.window_rows = window_rows
        self.on_sort = on_sort or (lambda column: None)
        self.on_selection = on_selection or (lambda source_ids: None)
        self.on_scroll = on_scroll or (lambda seconds: None)
        self.frame: pd.DataFrame | None = None
        self.columns: tuple[str, ...] = ()
        self._column_positions: dict[str, int] = {}
        self.ordered_ids = np.array([], dtype=np.int64)
        self.offset = 0
        self._slot_ids: dict[str, int] = {}
        self._selected_ids: set[int] = set()
        self._suppress_selection = False
        self._sort_column: str | None = None
        self._sort_descending = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, show="headings", selectmode="extended")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self._scroll_command)
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.hscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=self.hscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<MouseWheel>", self._mousewheel)
        self.tree.bind("<Button-4>", lambda event: self._scroll_units(-3))
        self.tree.bind("<Button-5>", lambda event: self._scroll_units(3))
        self.tree.bind("<Prior>", lambda event: self._page(-1))
        self.tree.bind("<Next>", lambda event: self._page(1))

        for index in range(window_rows):
            self.tree.insert("", "end", iid=f"slot-{index}", values=())

    def set_data(
        self,
        frame: pd.DataFrame | None,
        columns: Iterable[str],
        ordered_ids: np.ndarray | Iterable[int],
        *,
        keep_position: bool = False,
    ) -> None:
        self.frame = frame
        self.columns = tuple(columns)
        self._column_positions = (
            {name: int(frame.columns.get_loc(name)) for name in self.columns if name != SOURCE_ROW}
            if frame is not None
            else {}
        )
        self.ordered_ids = np.asarray(ordered_ids, dtype=np.int64)
        if not keep_position:
            self.offset = 0
        self.offset = min(self.offset, self._max_offset())
        self.tree.configure(columns=self.columns)
        for column in self.columns:
            marker = ""
            if column == self._sort_column:
                marker = " ▼" if self._sort_descending else " ▲"
            self.tree.heading(
                column,
                text=column + marker,
                command=lambda col=column: self.on_sort(col),
            )
            width = 110 if column != SOURCE_ROW else 105
            self.tree.column(column, width=width, minwidth=70, stretch=True, anchor="w")
        self.refresh()

    def set_sort_indicator(self, column: str | None, descending: bool = False) -> None:
        self._sort_column = column
        self._sort_descending = bool(descending)
        for current in self.columns:
            marker = ""
            if current == column:
                marker = " ▼" if descending else " ▲"
            self.tree.heading(
                current,
                text=current + marker,
                command=lambda col=current: self.on_sort(col),
            )

    def refresh(self) -> None:
        self._selected_ids.update(self.selected_source_ids())
        self._slot_ids.clear()
        total = len(self.ordered_ids)
        for slot in range(self.window_rows):
            iid = f"slot-{slot}"
            logical = self.offset + slot
            if self.frame is None or logical >= total:
                self.tree.item(iid, values=("",) * len(self.columns), tags=("empty",))
                continue
            source_id = int(self.ordered_ids[logical])
            self._slot_ids[iid] = source_id
            self.tree.item(
                iid,
                values=[self._display_value(source_id, column) for column in self.columns],
                tags=(),
            )
        self._restore_visible_selection()
        self._update_scrollbar()

    def selected_source_ids(self) -> tuple[int, ...]:
        ids = []
        for iid in self.tree.selection():
            source_id = self._slot_ids.get(iid)
            if source_id is not None:
                ids.append(source_id)
        return tuple(ids)

    def all_selected_source_ids(self) -> tuple[int, ...]:
        self._selected_ids.update(self.selected_source_ids())
        return tuple(sorted(self._selected_ids))

    def focus_source_id(self, source_id: int) -> bool:
        positions = np.flatnonzero(self.ordered_ids == int(source_id))
        if not len(positions):
            return False
        position = int(positions[0])
        centered = max(0, position - self.window_rows // 2)
        self.offset = min(centered, self._max_offset())
        self._selected_ids.add(int(source_id))
        self.refresh()
        for iid, current in self._slot_ids.items():
            if current == int(source_id):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                break
        return True

    def visible_source_ids(self) -> tuple[int, ...]:
        return tuple(self._slot_ids.values())

    def clear_visual_selection(self) -> None:
        self._selected_ids.clear()
        self.tree.selection_remove(self.tree.selection())
        self.on_selection(())

    def _selection_changed(self, event=None) -> None:
        if self._suppress_selection:
            return
        visible = set(self._slot_ids.values())
        self._selected_ids.difference_update(visible)
        self._selected_ids.update(self.selected_source_ids())
        self.on_selection(self.all_selected_source_ids())

    def _restore_visible_selection(self) -> None:
        self._suppress_selection = True
        try:
            self.tree.selection_remove(self.tree.selection())
            selected_iids = [
                iid
                for iid, source_id in self._slot_ids.items()
                if source_id in self._selected_ids
            ]
            if selected_iids:
                self.tree.selection_set(selected_iids)
        finally:
            self._suppress_selection = False

    def _display_value(self, source_id: int, column: str) -> str:
        if column == SOURCE_ROW:
            return str(source_id)
        value = self.frame.iat[source_id - 1, self._column_positions[column]]
        if pd.isna(value):
            return ""
        if isinstance(value, (bool, np.bool_)):
            return "True" if bool(value) else "False"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            return format(float(value), ".8g")
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        text = str(value)
        return text if len(text) <= 48 else text[:45] + "…"

    def _scroll_command(self, *args) -> None:
        if not args:
            return
        started = time.perf_counter()
        if args[0] == "moveto":
            self.offset = int(round(float(args[1]) * self._max_offset()))
        elif args[0] == "scroll":
            count = int(args[1])
            unit = args[2]
            step = max(1, self.window_rows - 3) if unit == "pages" else max(1, abs(count)) * 3
            self.offset += (-1 if count < 0 else 1) * step
        self.offset = min(max(0, self.offset), self._max_offset())
        self.refresh()
        self.on_scroll(time.perf_counter() - started)

    def _mousewheel(self, event):
        self._scroll_units((-1 if event.delta > 0 else 1) * 3)
        return "break"

    def _scroll_units(self, amount: int):
        started = time.perf_counter()
        self.offset = min(max(0, self.offset + amount), self._max_offset())
        self.refresh()
        self.on_scroll(time.perf_counter() - started)
        return "break"

    def _page(self, direction: int):
        self._scroll_units(direction * max(1, self.window_rows - 3))
        return "break"

    def _max_offset(self) -> int:
        return max(0, len(self.ordered_ids) - self.window_rows)

    def _update_scrollbar(self) -> None:
        total = len(self.ordered_ids)
        if total <= self.window_rows or total == 0:
            self.vscroll.set(0.0, 1.0)
            return
        self.vscroll.set(
            self.offset / total,
            min(1.0, (self.offset + self.window_rows) / total),
        )
