"""Focused editors for import settings, filters, and scientific plot additions."""

from __future__ import annotations

import tkinter as tk
from dataclasses import asdict

import customtkinter as ctk

from .. import config as C
from ..data import DataError, ImportOptions
from ..plots import InsetSpec, Overlay, ScatterSpec, validate_overlay, validate_scatter
from ..state import FilterRule
from .widgets import ColumnSelector, DataTable, button, entry_value, label, set_entry


class Modal(ctk.CTkToplevel):
    def __init__(self, app, title, size="580x690"):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.geometry(size)
        self.minsize(480, 460)
        self.configure(fg_color=C.BG)
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        label(self, title, size=22, bold=True).grid(
            row=0, column=0, padx=24, pady=(20, 12), sticky="w"
        )
        self.body = ctk.CTkScrollableFrame(self, fg_color=C.PANEL)
        self.body.grid(row=1, column=0, padx=20, pady=4, sticky="nsew")
        self.body.grid_columnconfigure(1, weight=1)
        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.grid(row=2, column=0, sticky="ew", padx=20, pady=16)
        self.row = 0
        self.bind("<Escape>", lambda e: self.destroy())
        self._activate_id = self.after(80, self._activate)

    def _activate(self):
        self._activate_id = None
        if self.winfo_exists():
            self.lift()
            self.grab_set()

    def destroy(self):
        if getattr(self, "_activate_id", None) is not None:
            self.after_cancel(self._activate_id)
            self._activate_id = None
        super().destroy()

    def field(self, title, value="", choices=None):
        label(self.body, title, anchor="w").grid(
            row=self.row, column=0, sticky="w", padx=10, pady=7
        )
        if choices:
            widget = ctk.CTkComboBox(self.body, values=list(choices), state="readonly")
            widget.set(str(value))
        else:
            widget = ctk.CTkEntry(self.body)
            set_entry(widget, value)
        widget.grid(row=self.row, column=1, sticky="ew", padx=10, pady=7)
        self.row += 1
        return widget

    def note(self, text):
        label(self.body, text, color=C.MUTED, justify="left", wraplength=450).grid(
            row=self.row, column=0, columnspan=2, padx=10, pady=10, sticky="w"
        )
        self.row += 1

    def actions(self, text, accept):
        button(self.footer, "Cancel", self.destroy, width=100).pack(side="left")

        def checked():
            try:
                accept()
            except Exception as exc:
                self.app.show_error(exc, parent=self)

        button(self.footer, text, checked, primary=True, width=160).pack(side="right")


class ImportDialog(Modal):
    def __init__(self, app):
        super().__init__(app, "Import options", "590x680")
        options = app.import_options
        self.delimiters = {
            "Auto": "auto",
            "Comma": ",",
            "Semicolon": ";",
            "Tab": "\t",
            "Pipe": "|",
            "Whitespace (DAT)": "whitespace",
        }
        selected = next((k for k, v in self.delimiters.items() if v == options.delimiter), "Auto")
        self.delimiter = self.field("Delimiter", selected, self.delimiters)
        self.encoding = self.field(
            "Encoding", options.encoding, ["utf-8-sig", "utf-8", "cp1252", "latin-1", "utf-16"]
        )
        self.header = self.field("Header row", options.header.title(), ["Auto", "Yes", "No"])
        self.decimal = self.field("Decimal separator", options.decimal, [".", ","])
        self.note(
            "Auto: CSV uses a header; DAT uses whitespace and accepts a first # header.\n"
            "Choose No for headerless data. Incorrect record widths are reported, never skipped."
        )
        label(self.body, "ISO date columns (optional)", bold=True).grid(
            row=self.row, column=0, columnspan=2, padx=10, pady=(12, 4), sticky="w"
        )
        self.row += 1
        self.dates = ctk.CTkTextbox(self.body, height=90)
        self.dates.grid(row=self.row, column=0, columnspan=2, padx=10, sticky="ew")
        self.dates.insert("1.0", "\n".join(options.date_columns))
        self.row += 1
        self.note(
            "One exact column name per line. Leave empty to keep date-like text unchanged.\n"
            "These settings apply to the next Open or Reload."
        )
        self.actions("Save options", self.accept)

    def accept(self):
        self.app.import_options = ImportOptions(
            delimiter=self.delimiters[self.delimiter.get()],
            encoding=self.encoding.get(),
            header=self.header.get().lower(),
            decimal=self.decimal.get(),
            date_columns=tuple(
                line for line in self.dates.get("1.0", "end").splitlines() if line.strip()
            ),
        )
        self.app.set_status("Import options saved. Open or reload a file to apply them.")
        self.destroy()


class FilterDialog(Modal):
    def __init__(self, app):
        super().__init__(app, "Filter the dataset", "660x660")
        self.revision = app.app_state.revision
        self.rules = list(app.app_state.filters)
        self.edit_index = None
        self.note(
            "All conditions must match. Bounds include their endpoints and apply to raw values.\n"
            "Missing and infinite values do not match a numeric condition. Filters affect every tab."
        )
        self.column = ColumnSelector(self.body)
        self.column.set_choices(app.app_state.dataset.numeric_columns)
        label(self.body, "Column").grid(row=self.row, column=0, padx=10, pady=7, sticky="w")
        self.column.grid(row=self.row, column=1, padx=10, pady=7, sticky="ew")
        self.row += 1
        self.lower = self.field("Minimum (blank = none)")
        self.upper = self.field("Maximum (blank = none)")
        controls = ctk.CTkFrame(self.body, fg_color="transparent")
        controls.grid(row=self.row, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        self.row += 1
        self.add_button = button(controls, "Add condition", self.add, width=130)
        self.add_button.pack(side="left")
        button(controls, "New", self.new, width=70).pack(side="left", padx=8)
        button(controls, "Remove", self.remove, width=85).pack(side="right")
        self.table = DataTable(self.body, height=7)
        self.table.grid(row=self.row, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)
        self.table.tree.bind("<<TreeviewSelect>>", self.select)
        self.row += 1
        button(self.body, "Clear all conditions", self.clear, width=180).grid(
            row=self.row, column=0, columnspan=2, padx=10, pady=10, sticky="w"
        )
        self.refresh()
        self.actions("Apply filters", self.accept)

    def refresh(self):
        self.table.show(
            ["Column", "Minimum", "Maximum"], [(r.column, r.lower, r.upper) for r in self.rules]
        )

    def new(self):
        self.edit_index = None
        self.table.tree.selection_remove(self.table.tree.selection())
        set_entry(self.lower, None)
        set_entry(self.upper, None)
        self.add_button.configure(text="Add condition")

    def select(self, event=None):
        index = self.table.selected_index()
        if index is None or index >= len(self.rules):
            return
        self.edit_index = index
        rule = self.rules[index]
        self.column.set(rule.column)
        set_entry(self.lower, rule.lower)
        set_entry(self.upper, rule.upper)
        self.add_button.configure(text="Update condition")

    def add(self):
        try:
            rule = FilterRule(
                self.column.get(),
                entry_value(self.lower, "Minimum", optional=True),
                entry_value(self.upper, "Maximum", optional=True),
            )
            if rule.column not in self.app.app_state.dataset.numeric_columns:
                raise DataError("Choose a numeric column from the list.")
            if rule.lower is None and rule.upper is None:
                raise DataError("Enter at least one bound.")
            if rule.lower is not None and rule.upper is not None and rule.lower > rule.upper:
                raise DataError("Minimum must not exceed maximum.")
            if self.edit_index is None:
                self.rules.append(rule)
            else:
                self.rules[self.edit_index] = rule
            self.refresh()
            self.new()
        except Exception as exc:
            self.app.show_error(exc, parent=self)

    def remove(self):
        index = self.table.selected_index()
        if index is not None:
            del self.rules[index]
            self.refresh()
            self.new()

    def clear(self):
        self.rules.clear()
        self.refresh()
        self.new()

    def accept(self):
        if self.revision != self.app.app_state.revision:
            raise DataError("The dataset or selection changed. Reopen the filter editor.")
        self.app.apply_filters(tuple(self.rules))
        self.destroy()


class StyleDialog(Modal):
    def __init__(self, app, values, accept):
        super().__init__(app, "Figure appearance", "600x740")
        self.callback = accept
        self.fields = {}
        choices = {
            "marker": ["o", ".", "s", "^", "v", "D", "*", "x", "+"],
            "cmap": [
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "coolwarm",
                "RdBu_r",
                "turbo",
                "gray",
            ],
        }
        names = {
            "title": "Title",
            "x_label": "X label",
            "y_label": "Y label",
            "color_label": "Color label",
            "marker": "Marker",
            "size": "Marker area (points²)",
            "opacity": "Selected opacity",
            "excluded_opacity": "Excluded opacity",
            "cmap": "Colormap",
        }
        for key, title in names.items():
            self.fields[key] = self.field(title, values[key], choices.get(key))
        self.grid_var = tk.BooleanVar(self, value=values["grid"])
        ctk.CTkCheckBox(self.body, text="Grid lines", variable=self.grid_var).grid(
            row=self.row, column=0, columnspan=2, padx=10, pady=10, sticky="w"
        )
        self.row += 1
        self.sampling = self.field(
            "Display sampling",
            "Limit each group" if values["sample_limit"] else "All points",
            ["All points", "Limit each group"],
        )
        self.limit = self.field("Maximum points per group", values["sample_limit"] or 50000)
        self.note(
            "Sampling uses a fixed seed and affects the displayed/exported figure only.\n"
            "Statistics use every selected row. Blank labels use column names; $…$ supports math notation."
        )
        self.actions("Apply appearance", self.accept)

    def accept(self):
        result = {key: widget.get() for key, widget in self.fields.items()}
        for key in ("size", "opacity", "excluded_opacity"):
            result[key] = entry_value(self.fields[key], key.replace("_", " "))
        result["grid"] = self.grid_var.get()
        result["sample_limit"] = (
            entry_value(self.limit, "Point limit", integer=True)
            if self.sampling.get() == "Limit each group"
            else None
        )
        validate_scatter(
            ScatterSpec(
                "x",
                "y",
                size=result["size"],
                opacity=result["opacity"],
                excluded_opacity=result["excluded_opacity"],
                marker=result["marker"],
                cmap=result["cmap"],
                sample_limit=result["sample_limit"],
            )
        )
        self.callback(result)
        self.destroy()


class OverlayDialog(Modal):
    KINDS = {
        "Horizontal line": {"y", "linewidth", "linestyle"},
        "Vertical line": {"x", "linewidth", "linestyle"},
        "Slope line": {"x", "y", "slope", "linewidth", "linestyle"},
        "Segment": {"x", "y", "x2", "y2", "linewidth", "linestyle"},
        "Point": {"x", "y", "size", "marker"},
        "Text": {"x", "y", "text", "fontsize", "rotation"},
    }

    def __init__(self, app, items, accept):
        super().__init__(app, "Annotations & reference elements", "680x820")
        self.items = list(items)
        self.callback = accept
        self.edit_index = None
        self.note(
            "Coordinates use the displayed axes, after any absolute-value transformation.\n"
            "Annotations preserve your text exactly, including commas and equations."
        )
        self.kind = self.field("Element", "Horizontal line", self.KINDS)
        self.kind.configure(command=lambda _: self.update_fields())
        defaults = asdict(Overlay("Horizontal line"))
        titles = {
            "x": "X / first X",
            "y": "Y / first Y",
            "x2": "Second X",
            "y2": "Second Y",
            "slope": "Slope",
            "text": "Text",
            "color": "Color",
            "opacity": "Opacity",
            "size": "Marker area (points²)",
            "linewidth": "Line width",
            "linestyle": "Line style",
            "marker": "Marker",
            "fontsize": "Font size",
            "rotation": "Rotation (degrees)",
        }
        self.fields = {}
        for key, title in titles.items():
            choices = (
                ["-", "--", "-.", ":"]
                if key == "linestyle"
                else (["o", "s", "^", "x", "+"] if key == "marker" else None)
            )
            self.fields[key] = self.field(title, defaults[key], choices)
        controls = ctk.CTkFrame(self.body, fg_color="transparent")
        controls.grid(row=self.row, column=0, columnspan=2, sticky="ew", padx=10, pady=12)
        self.row += 1
        self.add_button = button(controls, "Add element", self.add, width=130)
        self.add_button.pack(side="left")
        button(controls, "New", self.new, width=70).pack(side="left", padx=8)
        button(controls, "Remove", self.remove, width=85).pack(side="right")
        self.table = DataTable(self.body, height=5)
        self.table.grid(row=self.row, column=0, columnspan=2, padx=10, pady=8, sticky="ew")
        self.table.tree.bind("<<TreeviewSelect>>", self.select)
        self.refresh()
        self.update_fields()
        self.actions("Use these elements", self.accept)

    def update_fields(self):
        enabled = self.KINDS[self.kind.get()] | {"color", "opacity"}
        for name, control in self.fields.items():
            control.configure(
                state=("readonly" if name in ("marker", "linestyle") else "normal")
                if name in enabled
                else "disabled"
            )

    def refresh(self):
        self.table.show(
            ["Element", "Position / text", "Color"],
            [
                (
                    item.kind,
                    item.text if item.kind == "Text" else f"x={item.x:g}, y={item.y:g}",
                    item.color,
                )
                for item in self.items
            ],
        )

    def select(self, event=None):
        index = self.table.selected_index()
        if index is None or index >= len(self.items):
            return
        self.edit_index = index
        self.kind.set(self.items[index].kind)
        for key, value in asdict(self.items[index]).items():
            if key in self.fields:
                widget = self.fields[key]
                widget.configure(state="normal")
                widget.set(str(value)) if key in ("marker", "linestyle") else set_entry(
                    widget, value
                )
        self.update_fields()
        self.add_button.configure(text="Update element")

    def new(self):
        self.edit_index = None
        self.table.tree.selection_remove(self.table.tree.selection())
        self.add_button.configure(text="Add element")

    def add(self):
        try:
            values = {"kind": self.kind.get()}
            for key in self.KINDS[values["kind"]] | {"color", "opacity"}:
                widget = self.fields[key]
                values[key] = (
                    widget.get()
                    if key in ("text", "color", "marker", "linestyle")
                    else entry_value(widget, key)
                )
            item = Overlay(**values)
            validate_overlay(item)
            if self.edit_index is None:
                self.items.append(item)
            else:
                self.items[self.edit_index] = item
            self.refresh()
            self.new()
        except Exception as exc:
            self.app.show_error(exc, parent=self)

    def remove(self):
        index = self.table.selected_index()
        if index is not None:
            del self.items[index]
            self.refresh()
            self.new()

    def accept(self):
        self.callback(tuple(self.items))
        self.destroy()


class InsetDialog(Modal):
    def __init__(self, app, value, accept, x_scale="linear", y_scale="linear"):
        super().__init__(app, "Inset zoom", "590x740")
        self.callback = accept
        self.x_scale, self.y_scale = x_scale, y_scale
        self.enabled = self.field("Inset", "Enabled" if value else "Off", ["Off", "Enabled"])
        base = asdict(value or InsetSpec(0.1, 1, 0.1, 1))
        titles = {
            "x_min": "X minimum",
            "x_max": "X maximum",
            "y_min": "Y minimum",
            "y_max": "Y maximum",
            "width": "Width (%)",
            "height": "Height (%)",
            "location": "Position",
            "edgecolor": "Outline color",
            "linewidth": "Outline width",
        }
        self.fields = {}
        for key, title in titles.items():
            choices = (
                ["upper right", "upper left", "lower right", "lower left"]
                if key == "location"
                else None
            )
            self.fields[key] = self.field(
                title, base[key] * 100 if key in ("width", "height") else base[key], choices
            )
        self.xscale = self.field(
            "Inset X scale", base["x_scale"] or "inherit", ["inherit", "linear", "log"]
        )
        self.yscale = self.field(
            "Inset Y scale", base["y_scale"] or "inherit", ["inherit", "linear", "log"]
        )
        self.rectangle = tk.BooleanVar(self, value=base["rectangle"])
        ctk.CTkCheckBox(
            self.body, text="Mark zoom region on main plot", variable=self.rectangle
        ).grid(row=self.row, column=0, columnspan=2, padx=10, pady=12, sticky="w")
        self.row += 1
        self.note(
            "The inset shares the main plot's points and color normalization.\n"
            "Changing its scales only changes the view; excluded invalid rows are not restored."
        )
        self.actions("Apply inset", self.accept)

    def accept(self):
        result = None
        if self.enabled.get() == "Enabled":
            values = {
                key: widget.get() if key in ("location", "edgecolor") else entry_value(widget, key)
                for key, widget in self.fields.items()
            }
            values["width"] /= 100
            values["height"] /= 100
            values["rectangle"] = self.rectangle.get()
            values["x_scale"] = None if self.xscale.get() == "inherit" else self.xscale.get()
            values["y_scale"] = None if self.yscale.get() == "inherit" else self.yscale.get()
            result = InsetSpec(**values)
            from ..plots import AxisSpec

            validate_scatter(
                ScatterSpec(
                    "x",
                    "y",
                    x_axis=AxisSpec(self.x_scale),
                    y_axis=AxisSpec(self.y_scale),
                    inset=result,
                )
            )
        self.callback(result)
        self.destroy()


class ColumnsDialog(Modal):
    def __init__(self, app, columns, selected, accept):
        super().__init__(app, "Correlation columns", "540x620")
        self.callback = accept
        self.variables = {}
        self.note(
            "Pearson correlations use paired finite observations.\nEach pair reports its own sample size."
        )
        for column in columns:
            variable = tk.BooleanVar(self, value=column in selected)
            self.variables[column] = variable
            ctk.CTkCheckBox(self.body, text=column, variable=variable).grid(
                row=self.row, column=0, columnspan=2, padx=10, pady=5, sticky="w"
            )
            self.row += 1
        controls = ctk.CTkFrame(self.footer, fg_color="transparent")
        controls.pack(side="left")
        button(
            controls, "All", lambda: [v.set(True) for v in self.variables.values()], width=65
        ).pack(side="left", padx=4)
        button(
            controls, "None", lambda: [v.set(False) for v in self.variables.values()], width=65
        ).pack(side="left", padx=4)
        button(self.footer, "Use selection", self.accept, primary=True).pack(side="right")

    def accept(self):
        columns = tuple(name for name, variable in self.variables.items() if variable.get())
        if len(columns) < 2:
            self.app.show_error(DataError("Choose at least two columns."), parent=self)
            return
        self.callback(columns)
        self.destroy()
