"""Small shared controls, numeric parsing, and bounded table display."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk
import numpy as np
import pandas as pd

from .. import config as C
from ..data import DataError
from ..plots import AxisSpec


def label(parent, text, *, size=12, color=C.TEXT, bold=False, **kwargs):
    return ctk.CTkLabel(
        parent,
        text=text,
        text_color=color,
        font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
        **kwargs,
    )


def button(parent, text, command, *, primary=False, **kwargs):
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        height=34,
        fg_color=C.ACCENT if primary else C.PANEL_ALT,
        hover_color=C.ACCENT_HOVER if primary else "#304859",
        text_color=C.BG if primary else C.TEXT,
        **kwargs,
    )


def entry_value(entry, name: str, *, optional=False, integer=False):
    text = entry.get().strip()
    if optional and not text:
        return None
    try:
        value = int(text) if integer else float(text)
        if not math.isfinite(value):
            raise ValueError
        return value
    except (ValueError, OverflowError) as exc:
        raise DataError(f"{name}: enter a finite {'integer' if integer else 'number'}.") from exc


def set_entry(entry, value):
    entry.delete(0, "end")
    entry.insert(0, "" if value is None else str(value))


def format_value(value):
    if pd.isna(value):
        return "—"
    if isinstance(value, (float, np.floating)):
        if np.isinf(value):
            return "∞" if value > 0 else "−∞"
        return f"{value:.6g}"
    return str(value)


class ColumnSelector(ctk.CTkComboBox):
    """Typing narrows the dropdown; the analysis layer validates final selection."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, values=[], height=32, **kwargs)
        self.choices: list[str] = []
        self.set("")
        self.bind("<KeyRelease>", self._search)

    def set_choices(self, values, preferred=None):
        self.choices = list(values)
        self.configure(values=self.choices, state="normal" if self.choices else "disabled")
        self.set(
            preferred if preferred in self.choices else (self.choices[0] if self.choices else "")
        )

    def _search(self, event):
        if event.keysym in ("Up", "Down", "Return", "Tab", "Escape"):
            return
        query = self.get().casefold()
        self.configure(values=[choice for choice in self.choices if query in choice.casefold()])


class DataTable(ctk.CTkFrame):
    def __init__(self, parent, *, height=12, selectmode="browse"):
        super().__init__(parent, fg_color=C.PANEL)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            self, show="headings", height=height, selectmode=selectmode, style="Analyzer.Treeview"
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("odd", background="#1D2C38")
        self.tree.bind("<Control-c>", self.copy_selection)

    def show(self, columns, rows):
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        ids = [f"c{i}" for i in range(len(columns))]
        self.tree.configure(columns=ids)
        for index, (key, text) in enumerate(zip(ids, columns)):
            self.tree.heading(key, text=str(text))
            self.tree.column(
                key,
                width=max(100, min(240, len(str(text)) * 8 + 26)),
                minwidth=70,
                stretch=False,
                anchor="w",
            )
        for i, row in enumerate(rows):
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=[format_value(v) for v in row],
                tags=("odd",) if i % 2 else (),
            )

    def show_frame(self, frame: pd.DataFrame):
        self.show(list(frame.columns), frame.itertuples(index=False, name=None))

    def selected_index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def copy_selection(self, event=None):
        rows = [self.tree.item(i, "values") for i in self.tree.selection()]
        if rows:
            self.clipboard_clear()
            self.clipboard_append("\n".join("\t".join(row) for row in rows))
        return "break"


def configure_tables(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "Analyzer.Treeview",
        background=C.PANEL,
        fieldbackground=C.PANEL,
        foreground=C.TEXT,
        rowheight=29,
        borderwidth=0,
        font=("Arial", 10),
    )
    style.configure(
        "Analyzer.Treeview.Heading",
        background=C.PANEL_ALT,
        foreground=C.TEXT,
        relief="flat",
        font=("Arial", 10, "bold"),
        padding=(8, 8),
    )
    style.map(
        "Analyzer.Treeview",
        background=[("selected", "#245E60")],
        foreground=[("selected", "#FFFFFF")],
    )
    style.map("Analyzer.Treeview.Heading", background=[("active", "#304859")])


class AxisControls(ctk.CTkFrame):
    def __init__(self, parent, title):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        label(self, title, bold=True, size=12).grid(row=0, column=0, sticky="w", pady=(8, 4))
        self.column = ColumnSelector(self, width=260)
        self.column.grid(row=1, column=0, sticky="ew")
        options = ctk.CTkFrame(self, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", pady=7)
        self.scale = ctk.CTkOptionMenu(
            options,
            values=["Linear", "Logarithmic"],
            width=130,
            fg_color=C.PANEL_ALT,
            button_color=C.PANEL_ALT,
        )
        self.scale.pack(side="left")
        self.absolute = tk.BooleanVar(master=self, value=False)
        ctk.CTkCheckBox(
            options,
            text="Absolute",
            variable=self.absolute,
            width=90,
            checkbox_width=18,
            checkbox_height=18,
        ).pack(side="right")
        self.scientific = tk.BooleanVar(master=self, value=False)
        ctk.CTkCheckBox(
            self,
            text="Scientific tick labels",
            variable=self.scientific,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=3, column=0, sticky="w")
        limits = ctk.CTkFrame(self, fg_color="transparent")
        limits.grid(row=4, column=0, sticky="ew", pady=(8, 2))
        limits.grid_columnconfigure((0, 1), weight=1)
        self.lower = ctk.CTkEntry(limits, placeholder_text="Minimum · auto", width=120)
        self.upper = ctk.CTkEntry(limits, placeholder_text="Maximum · auto", width=120)
        self.lower.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.upper.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def reset(self, columns, preferred=None):
        self.column.set_choices(columns, preferred)
        self.scale.set("Linear")
        self.absolute.set(False)
        self.scientific.set(False)
        set_entry(self.lower, None)
        set_entry(self.upper, None)

    def spec(self, custom_label=""):
        return AxisSpec(
            scale="log" if self.scale.get() == "Logarithmic" else "linear",
            scientific=self.scientific.get(),
            absolute=self.absolute.get(),
            lower=entry_value(self.lower, "Minimum", optional=True),
            upper=entry_value(self.upper, "Maximum", optional=True),
            label=custom_label,
        )
