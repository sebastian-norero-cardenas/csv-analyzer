from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .. import config as C
from ..plots import ScatterSpec, prepare_scatter, render_scatter
from .dialogs import InsetDialog, OverlayDialog, StyleDialog
from .plot_panel import PlotPanel
from .widgets import AxisControls, ColumnSelector, button, label


class RelationshipsTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        sidebar = ctk.CTkFrame(self, width=310, fg_color=C.PANEL)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12), pady=8)
        sidebar.grid_rowconfigure(0, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        self.controls = ctk.CTkScrollableFrame(sidebar, width=278, fg_color="transparent")
        self.controls.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.controls.grid_columnconfigure(0, weight=1)
        label(self.controls, "Compare variables", size=19, bold=True).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.x = AxisControls(self.controls, "X AXIS")
        self.y = AxisControls(self.controls, "Y AXIS")
        self.x.grid(row=1, column=0, sticky="ew")
        self.y.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        label(self.controls, "COLOR MAPPING", bold=True).grid(
            row=3, column=0, sticky="w", pady=(18, 5)
        )
        self.color = ColumnSelector(self.controls, command=lambda _: self.color_changed())
        self.color.grid(row=4, column=0, sticky="ew")
        self.color.bind("<FocusOut>", lambda e: self.color_changed(), add=True)
        self.color_abs = tk.BooleanVar(self, False)
        self.color_log = tk.BooleanVar(self, False)
        self.color_sci = tk.BooleanVar(self, False)
        self.color_checks = []
        for i, (title, variable) in enumerate(
            [
                ("Absolute color values", self.color_abs),
                ("Logarithmic color scale", self.color_log),
                ("Scientific color labels", self.color_sci),
            ]
        ):
            check = ctk.CTkCheckBox(
                self.controls, text=title, variable=variable, checkbox_width=18, checkbox_height=18
            )
            check.grid(row=5 + i, column=0, sticky="w", pady=5)
            self.color_checks.append(check)
        self.excluded = tk.BooleanVar(self, True)
        ctk.CTkCheckBox(
            self.controls,
            text="Show excluded points in gray",
            variable=self.excluded,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=8, column=0, sticky="w", pady=(14, 8))
        label(self.controls, "REFINE THE FIGURE", bold=True, color=C.MUTED, size=11).grid(
            row=9, column=0, sticky="w", pady=(12, 6)
        )
        self.style_button = button(self.controls, "Appearance & sampling…", self.edit_style)
        self.style_button.grid(row=10, column=0, sticky="ew", pady=4)
        self.overlay_button = button(self.controls, "Annotations · 0", self.edit_overlays)
        self.overlay_button.grid(row=11, column=0, sticky="ew", pady=4)
        self.inset_button = button(self.controls, "Inset zoom · off", self.edit_inset)
        self.inset_button.grid(row=12, column=0, sticky="ew", pady=4)
        action = ctk.CTkFrame(sidebar, fg_color="transparent")
        action.grid(row=1, column=0, sticky="ew", padx=14, pady=12)
        action.grid_columnconfigure(0, weight=1)
        self.plot_button = button(action, "Plot relationships", self.plot, primary=True)
        self.plot_button.grid(row=0, column=0, sticky="ew")
        self.reset_button = button(action, "Reset settings", self.reset, width=120)
        self.reset_button.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.panel = PlotPanel(self, app, "Explore relationships\n\nChoose X and Y, then plot.")
        self.panel.grid(row=0, column=1, sticky="nsew", pady=8)
        self.reset()

    def reset(self):
        if hasattr(self.app, "jobs"):
            self.app.jobs.cancel("scatter")
        dataset = self.app.app_state.dataset
        columns = dataset.numeric_columns if dataset else ()
        self.x.reset(columns)
        self.y.reset(columns, columns[1] if len(columns) > 1 else None)
        self.no_color = "— No color mapping —"
        while self.no_color in columns:
            self.no_color += " "
        self.color.set_choices([self.no_color, *columns], self.no_color)
        for variable in (self.color_abs, self.color_log, self.color_sci):
            variable.set(False)
        self.excluded.set(True)
        self.style = dict(
            title="",
            x_label="",
            y_label="",
            color_label="",
            marker="o",
            size=12.0,
            opacity=0.8,
            excluded_opacity=0.12,
            cmap="viridis",
            grid=True,
            sample_limit=None,
        )
        self.overlays = ()
        self.inset = None
        self.overlay_button.configure(text="Annotations · 0")
        self.inset_button.configure(text="Inset zoom · off")
        available = bool(columns) and bool(len(dataset.frame)) if dataset else False
        for widget in (self.plot_button, self.style_button, self.overlay_button, self.inset_button):
            widget.configure(state="normal" if available else "disabled")
        self.color_changed()
        self.panel.clear()

    def color_changed(self):
        enabled = self.color.get() != self.no_color and bool(self.app.app_state.dataset)
        for checkbox in self.color_checks:
            checkbox.configure(state="normal" if enabled else "disabled")

    def on_state(self, reason):
        if reason == "dataset":
            self.reset()
        else:
            self.panel.clear("Selection changed\n\nPlot again to use the current filters.")

    def edit_style(self):
        def accept(values):
            self.style = values
            self.app.set_status("Appearance updated. Plot again to apply it.")

        StyleDialog(self.app, self.style, accept)

    def edit_overlays(self):
        def accept(items):
            self.overlays = items
            self.overlay_button.configure(text=f"Annotations · {len(items)}")

        OverlayDialog(self.app, self.overlays, accept)

    def edit_inset(self):
        def accept(value):
            self.inset = value
            self.inset_button.configure(text=f"Inset zoom · {'on' if value else 'off'}")

        InsetDialog(
            self.app,
            self.inset,
            accept,
            "log" if self.x.scale.get() == "Logarithmic" else "linear",
            "log" if self.y.scale.get() == "Logarithmic" else "linear",
        )

    def spec(self):
        style = self.style
        return ScatterSpec(
            self.x.column.get(),
            self.y.column.get(),
            x_axis=self.x.spec(style["x_label"]),
            y_axis=self.y.spec(style["y_label"]),
            color=None if self.color.get() == self.no_color else self.color.get(),
            color_absolute=self.color_abs.get(),
            color_scale="log" if self.color_log.get() else "linear",
            color_scientific=self.color_sci.get(),
            color_label=style["color_label"],
            cmap=style["cmap"],
            marker=style["marker"],
            size=style["size"],
            opacity=style["opacity"],
            show_excluded=self.excluded.get(),
            excluded_opacity=style["excluded_opacity"],
            title=style["title"],
            grid=style["grid"],
            sample_limit=style["sample_limit"],
            overlays=self.overlays,
            inset=self.inset,
        )

    def plot(self):
        if self.app.app_state.dataset is None:
            return
        try:
            spec = self.spec()
            state = self.app.app_state
            dataset, mask, revision = state.dataset, state.mask, state.revision

            def success(data):
                if revision == state.revision:
                    self.panel.show(render_scatter(data, spec), data.description)
                    self.app.set_status("Relationship figure updated.")

            def failure(error):
                self.panel.error(str(error))
                self.app.show_error(error)

            self.app.run_task(
                "scatter",
                "Preparing relationship plot…",
                lambda cancel: prepare_scatter(dataset.frame, spec, mask, cancel=cancel),
                success,
                failure,
            )
        except Exception as exc:
            self.panel.error(str(exc))
            self.app.show_error(exc)
