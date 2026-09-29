from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .. import config as C
from ..data import DataError
from ..plots import HistogramSpec, prepare_histogram, render_histogram
from .plot_panel import PlotPanel
from .widgets import AxisControls, button, label, set_entry


class DistributionsTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        sidebar = ctk.CTkFrame(self, fg_color=C.PANEL)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12), pady=8)
        sidebar.grid_rowconfigure(0, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        controls = ctk.CTkScrollableFrame(sidebar, width=278, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        controls.grid_columnconfigure(0, weight=1)
        label(controls, "Explore a distribution", size=19, bold=True).grid(
            row=0, column=0, sticky="w"
        )
        self.axis = AxisControls(controls, "VARIABLE")
        self.axis.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        label(controls, "BINNING", bold=True).grid(row=2, column=0, sticky="w", pady=(8, 5))
        self.bins = ctk.CTkComboBox(
            controls, values=["auto", "fd", "sturges", "sqrt", "20", "50", "100"]
        )
        self.bins.grid(row=3, column=0, sticky="ew")
        label(controls, "Choose a rule or enter 1–4096 bins.", color=C.MUTED, size=11).grid(
            row=4, column=0, sticky="w", pady=(3, 8)
        )
        self.log_bins = tk.BooleanVar(self, False)
        ctk.CTkCheckBox(
            controls,
            text="Logarithmically spaced bins",
            variable=self.log_bins,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=5, column=0, sticky="w", pady=6)
        label(controls, "Y AXIS", bold=True).grid(row=6, column=0, sticky="w", pady=(18, 6))
        self.normalization = ctk.CTkSegmentedButton(controls, values=["Count", "Density"])
        self.normalization.grid(row=7, column=0, sticky="ew")
        self.log_y = tk.BooleanVar(self, False)
        self.sci_y = tk.BooleanVar(self, False)
        ctk.CTkCheckBox(
            controls,
            text="Logarithmic Y axis",
            variable=self.log_y,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=8, column=0, sticky="w", pady=(12, 5))
        ctk.CTkCheckBox(
            controls,
            text="Scientific Y labels",
            variable=self.sci_y,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=9, column=0, sticky="w", pady=5)
        label(controls, "Title (optional)", color=C.MUTED).grid(
            row=10, column=0, sticky="w", pady=(18, 4)
        )
        self.title = ctk.CTkEntry(controls)
        self.title.grid(row=11, column=0, sticky="ew")
        label(
            controls,
            "Density integrates to 1 over the chosen range.\nBin spacing and axis scale are independent.\nEvery selected usable row contributes.",
            color=C.MUTED,
            size=11,
            justify="left",
            wraplength=265,
        ).grid(row=12, column=0, sticky="w", pady=16)
        action = ctk.CTkFrame(sidebar, fg_color="transparent")
        action.grid(row=1, column=0, sticky="ew", padx=14, pady=12)
        action.grid_columnconfigure(0, weight=1)
        self.plot_button = button(action, "Plot distribution", self.plot, primary=True)
        self.plot_button.grid(row=0, column=0, sticky="ew")
        button(action, "Reset settings", self.reset).grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.panel = PlotPanel(
            self, app, "Explore a distribution\n\nChoose a numeric column, then plot."
        )
        self.panel.grid(row=0, column=1, sticky="nsew", pady=8)
        self.reset()

    def reset(self):
        if hasattr(self.app, "jobs"):
            self.app.jobs.cancel("histogram")
        dataset = self.app.app_state.dataset
        columns = dataset.numeric_columns if dataset else ()
        self.axis.reset(columns)
        self.bins.set("auto")
        self.normalization.set("Count")
        for variable in (self.log_bins, self.log_y, self.sci_y):
            variable.set(False)
        set_entry(self.title, "")
        self.plot_button.configure(state="normal" if columns and len(dataset.frame) else "disabled")
        self.panel.clear()

    def on_state(self, reason):
        if reason == "dataset":
            self.reset()
        else:
            self.panel.clear("Selection changed\n\nPlot again to use the current filters.")

    def spec(self):
        bins = self.bins.get().strip().lower()
        if bins not in ("auto", "fd", "sqrt", "sturges"):
            try:
                bins = int(bins)
            except ValueError as exc:
                raise DataError("Enter a bin count or choose Auto, FD, Sturges, or Sqrt.") from exc
        return HistogramSpec(
            self.axis.column.get(),
            axis=self.axis.spec(),
            bins=bins,
            log_bins=self.log_bins.get(),
            density=self.normalization.get() == "Density",
            log_y=self.log_y.get(),
            scientific_y=self.sci_y.get(),
            title=self.title.get(),
        )

    def plot(self):
        state = self.app.app_state
        if state.dataset is None:
            return
        try:
            spec = self.spec()
            dataset, mask, revision = state.dataset, state.mask, state.revision

            def success(data):
                if revision == state.revision:
                    self.panel.show(render_histogram(data, spec), data.description)
                    self.app.set_status("Distribution figure updated.")

            def failure(error):
                self.panel.error(str(error))
                self.app.show_error(error)

            self.app.run_task(
                "histogram",
                "Preparing distribution…",
                lambda cancel: prepare_histogram(dataset.frame, spec, mask, cancel=cancel),
                success,
                failure,
            )
        except Exception as exc:
            self.panel.error(str(exc))
            self.app.show_error(exc)
