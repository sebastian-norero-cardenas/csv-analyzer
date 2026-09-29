from __future__ import annotations

import customtkinter as ctk
import numpy as np

from .. import config as C
from .dialogs import ImportDialog
from .widgets import DataTable, button, label


class DataTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.page = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(8, 12))
        label(top, "Your dataset, at a glance", size=21, bold=True).pack(side="left")
        button(top, "Import options…", lambda: ImportDialog(app), width=140).pack(side="right")
        self.reload_button = button(top, "Reload", app.reload_file, width=85, state="disabled")
        self.reload_button.pack(side="right", padx=10)
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.mode = ctk.CTkSegmentedButton(
            controls, values=["Data preview", "Columns & quality"], command=lambda _: self.refresh()
        )
        self.mode.set("Data preview")
        self.mode.pack(side="left")
        self.previous = button(controls, "Previous", lambda: self.move(-1), width=85)
        self.next = button(controls, "Next", lambda: self.move(1), width=85)
        self.next.pack(side="right")
        self.previous.pack(side="right", padx=8)
        self.caption = label(controls, "No dataset", color=C.MUTED)
        self.caption.pack(side="right", padx=12)
        self.table = DataTable(self)
        self.table.grid(row=2, column=0, sticky="nsew")
        self.empty = ctk.CTkFrame(self, fg_color=C.PANEL, corner_radius=12)
        self.empty.grid(row=2, column=0, sticky="nsew")
        self.empty.grid_columnconfigure(0, weight=1)
        self.empty.grid_rowconfigure((0, 4), weight=1)
        label(self.empty, "Start with a question. Open your data.", size=26, bold=True).grid(
            row=1, column=0, padx=30, pady=8
        )
        label(
            self.empty,
            "Inspect columns, compare variables, explore distributions,\nand calculate statistics in one workspace.",
            color=C.MUTED,
            size=15,
            justify="center",
        ).grid(row=2, column=0, padx=30, pady=12)
        button(self.empty, "Open CSV or DAT…", app.open_file, primary=True, width=200).grid(
            row=3, column=0, pady=20
        )
        self.notices = ctk.CTkTextbox(
            self,
            height=94,
            fg_color=C.PANEL,
            text_color=C.MUTED,
            font=ctk.CTkFont(size=12),
            wrap="word",
        )
        self.notices.grid(row=3, column=0, sticky="ew", pady=(12, 4))
        self.refresh()

    def on_state(self, reason):
        self.page = 0
        self.refresh()

    def move(self, offset):
        self.page = max(0, self.page + offset)
        self.refresh()

    def refresh(self):
        state = self.app.app_state
        dataset = state.dataset
        self.notices.configure(state="normal")
        self.notices.delete("1.0", "end")
        if dataset is None:
            self.empty.grid()
            self.notices.insert(
                "1.0",
                "CSV: comma, semicolon, tab, or pipe separators. DAT: whitespace tables.\n"
                "Import options let you specify headers, encodings, decimal separators, and ISO date columns.",
            )
            self.previous.configure(state="disabled")
            self.next.configure(state="disabled")
        else:
            self.empty.grid_remove()
            self.reload_button.configure(state="normal")
            if self.mode.get() == "Columns & quality":
                self.table.show(
                    ["Column", "Kind", "Data type", "Missing", "Infinite"],
                    [(c.name, c.kind, c.dtype, c.missing, c.infinite) for c in dataset.columns],
                )
                self.caption.configure(text="Quality counts use the full file")
                self.previous.configure(state="disabled")
                self.next.configure(state="disabled")
            else:
                indices = (
                    range(len(dataset.frame)) if state.mask is None else np.flatnonzero(state.mask)
                )
                count = len(indices)
                self.page = min(self.page, max(0, (count - 1) // C.PREVIEW_ROWS))
                start = self.page * C.PREVIEW_ROWS
                chosen = indices[start : start + C.PREVIEW_ROWS]
                preview = dataset.frame.iloc[chosen]
                self.table.show(
                    ["Source row", *dataset.frame.columns],
                    (
                        (int(index) + 1, *row)
                        for index, row in zip(chosen, preview.itertuples(index=False, name=None))
                    ),
                )
                self.caption.configure(
                    text=f"{start + 1 if count else 0:,}–{start + len(chosen):,} of {count:,} rows"
                )
                self.previous.configure(state="normal" if self.page else "disabled")
                self.next.configure(
                    state="normal" if start + C.PREVIEW_ROWS < count else "disabled"
                )
            delimiter = "tab" if dataset.delimiter == "\t" else repr(dataset.delimiter)
            lines = [
                f"{dataset.path}\nDelimiter: {delimiter} · Encoding: {dataset.encoding} · "
                "Preview is read-only; numbers display 6 significant digits."
            ]
            lines.extend(dataset.notices)
            self.notices.insert("1.0", "\n".join(lines))
        self.notices.configure(state="disabled")
