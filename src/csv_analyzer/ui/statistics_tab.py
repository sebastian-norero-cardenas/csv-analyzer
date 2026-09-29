from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk
import pandas as pd

from .. import config as C
from ..analysis import categorical_summary, correlation_summary, numeric_summary
from ..exports import export_statistics
from .dialogs import ColumnsDialog
from .widgets import DataTable, button, label


class StatisticsTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.result: pd.DataFrame | None = None
        self.correlation_columns = ()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        label(self, "Understand your data", size=21, bold=True).grid(
            row=0, column=0, sticky="w", pady=(12, 10)
        )
        controls = ctk.CTkFrame(self, fg_color=C.PANEL)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.mode = ctk.CTkSegmentedButton(
            controls, values=["Numeric", "Categorical", "Correlation"], command=self.mode_changed
        )
        self.mode.set("Numeric")
        self.mode.pack(side="left", padx=12, pady=12)
        self.ddof = ctk.CTkOptionMenu(
            controls,
            values=["Sample (N−1)", "Population (N)"],
            width=145,
            command=lambda _: self.invalidate("Convention changed. Compute again."),
        )
        self.ddof.pack(side="left", padx=8)
        self.column_button = button(controls, "Choose columns…", self.choose_columns, width=140)
        self.column_button.pack(side="left", padx=4)
        self.compute_button = button(controls, "Compute", self.compute, primary=True, width=110)
        self.compute_button.pack(side="right", padx=12)
        self.export_button = button(
            controls, "Export CSV…", self.export, width=115, state="disabled"
        )
        self.export_button.pack(side="right", padx=2)
        self.summary = label(
            self, "Open a dataset to compute statistics.", color=C.MUTED, anchor="w"
        )
        self.summary.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.table = DataTable(self)
        self.table.grid(row=3, column=0, sticky="nsew")
        self.notes = label(self, "", color=C.MUTED, anchor="w", justify="left", wraplength=1000)
        self.notes.grid(row=4, column=0, sticky="ew", pady=(12, 10))
        self.bind("<Configure>", lambda e: self.notes.configure(wraplength=max(300, e.width - 30)))
        self.mode_changed()
        self.on_state("dataset")

    def mode_changed(self, value=None):
        mode = self.mode.get()
        self.ddof.configure(state="normal" if mode == "Numeric" else "disabled")
        self.column_button.configure(
            state="normal" if mode == "Correlation" and self.app.app_state.dataset else "disabled"
        )
        notes = {
            "Numeric": "Missing and infinite values are excluded per column. Quantiles use linear interpolation. "
            "Std and variance use the selected convention. — means undefined; ∞ indicates numeric overflow.\n"
            "The table displays 6 significant digits; CSV export preserves calculated precision.",
            "Categorical": "Text, boolean, and date columns. Missing values are not categories. "
            "One most frequent value is shown, with the number of tied modes.",
            "Correlation": "Pearson r uses pairwise finite observations. Paired N can differ across pairs. "
            "Constant columns and fewer than two pairs produce an undefined coefficient.",
        }
        self.notes.configure(text=notes[mode])
        self.invalidate("Choose settings, then compute.")

    def invalidate(self, message):
        if hasattr(self.app, "jobs"):
            self.app.jobs.cancel("statistics")
        self.result = None
        self.table.show([], [])
        self.export_button.configure(state="disabled")
        self.summary.configure(text=message)

    def on_state(self, reason):
        dataset = self.app.app_state.dataset
        if reason == "dataset":
            self.correlation_columns = dataset.numeric_columns[:2] if dataset else ()
        self.compute_button.configure(
            state="normal" if dataset is not None and len(dataset.frame) else "disabled"
        )
        self.column_button.configure(
            state="normal"
            if dataset is not None and self.mode.get() == "Correlation"
            else "disabled"
        )
        self.invalidate(
            "Dataset or selection changed. Compute to see current results."
            if dataset
            else "Open a dataset to compute statistics."
        )

    def choose_columns(self):
        if self.app.app_state.dataset:

            def accept(columns):
                self.correlation_columns = columns
                self.invalidate(f"{len(columns)} columns selected. Compute to see correlations.")

            ColumnsDialog(
                self.app,
                self.app.app_state.dataset.numeric_columns,
                self.correlation_columns,
                accept,
            )

    def compute(self):
        state = self.app.app_state
        if state.dataset is None:
            return
        dataset, mask, revision = state.dataset, state.mask, state.revision
        mode = self.mode.get()
        ddof = 1 if self.ddof.get().startswith("Sample") else 0
        columns = self.correlation_columns
        self.invalidate("Computing…")

        def work(cancel):
            if mode == "Numeric":
                return numeric_summary(dataset.frame, mask, ddof=ddof, cancel=cancel)
            if mode == "Categorical":
                return categorical_summary(dataset.frame, mask, cancel=cancel)
            return correlation_summary(dataset.frame, columns, mask, cancel=cancel)

        def success(result):
            if revision != state.revision:
                return
            self.result = result
            self.table.show_frame(result)
            self.export_button.configure(state="normal" if len(result) else "disabled")
            convention = (
                " · Sample (N−1)"
                if mode == "Numeric" and ddof == 1
                else (" · Population (N)" if mode == "Numeric" else "")
            )
            self.summary.configure(
                text=f"{mode}{convention} · {state.selected_count:,} selected rows · {len(result):,} result rows"
                if len(result)
                else f"No {mode.lower()} results for this dataset and selection."
            )
            self.app.set_status("Statistics updated.")

        def failure(error):
            self.summary.configure(text=f"Could not compute: {error}")
            self.app.show_error(error)

        self.app.run_task("statistics", "Computing statistics…", work, success, failure)

    def export(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export statistics",
            defaultextension=".csv",
            initialfile="statistics.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            export_statistics(self.result, path, overwrite=True)
            self.app.set_status(f"Statistics exported to {path}")
        except Exception as exc:
            self.app.show_error(exc)
