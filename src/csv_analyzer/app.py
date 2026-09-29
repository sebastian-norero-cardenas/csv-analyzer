"""Application coordinator and entry point. Only this layer orchestrates views."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from tkinter import TclError, filedialog, messagebox

import customtkinter as ctk

from .analysis import build_filter_mask
from .data import CancelledError, DataError, ImportOptions, load_dataset
from .jobs import TaskRunner
from .state import AppState
from .ui.dialogs import FilterDialog, Modal
from .ui.main_window import MainWindow
from .ui.widgets import button

logger = logging.getLogger(__name__)


class Application(MainWindow):
    def __init__(self):
        super().__init__()
        self.app_state = AppState()
        self.import_options = ImportOptions()
        self._busy = False
        self._closed = False
        self.build_interface()
        self.jobs = TaskRunner(self, self.activity_changed)
        self.app_state.subscribe(self.on_state_changed)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-o>", lambda event: self.open_file())
        self.bind("<Control-r>", lambda event: self.reload_file())

    def report_callback_exception(self, exc, value, traceback):
        logger.error("GUI callback failed", exc_info=(exc, value, traceback))
        self.show_error(value)

    def set_status(self, text):
        self.status.configure(text=text if len(text) <= 160 else text[:157] + "…")

    def show_error(self, error, parent=None):
        if isinstance(error, CancelledError):
            return
        if not isinstance(error, (DataError, ValueError, OSError)):
            logger.error("Operation failed", exc_info=(type(error), error, error.__traceback__))
        self.set_status(str(error))
        messagebox.showerror("CSV Analyzer", str(error), parent=parent or self)

    def activity_changed(self, busy):
        if self._closed:
            return
        if busy != self._busy:
            self._busy = busy
            if busy:
                self.progress.grid()
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.grid_remove()
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def run_task(self, key, description, work, success, failure=None):
        self.set_status(description)
        self.jobs.submit(key, work, success, failure or self.show_error)

    def open_file(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Open dataset",
            filetypes=[("Data files", "*.csv *.dat *.tsv *.txt"), ("All files", "*.*")],
        )
        if path:
            self.load_path(path)

    def reload_file(self):
        if self.app_state.dataset:
            self.load_path(self.app_state.dataset.path)

    def load_path(self, path: str | Path):
        ticket = self.app_state.begin_load()
        options = self.import_options

        def success(dataset):
            if self.app_state.commit_dataset(ticket, dataset):
                self.tabs.set("Data")
                self.set_status(f"Loaded {dataset.path.name} · {len(dataset.frame):,} rows")

        def failure(error):
            self.show_error(error)
            if self.app_state.dataset:
                self.set_status(
                    f"Load failed. {self.app_state.dataset.path.name} is still the active dataset."
                )

        self.run_task(
            "load",
            f"Checking and loading {Path(path).name}…",
            lambda cancel: load_dataset(path, options, cancel=cancel),
            success,
            failure,
        )

    def cancel_tasks(self):
        self.app_state.cancel_load()
        self.jobs.cancel_all()
        self.set_status("Task cancelled. The active dataset is unchanged.")
        if (
            self.statistics_tab.result is None
            and self.statistics_tab.summary.cget("text") == "Computing…"
        ):
            self.statistics_tab.summary.configure(
                text="Computation cancelled. Compute to try again."
            )

    def apply_filters(self, rules):
        dataset, revision = self.app_state.dataset, self.app_state.revision
        if dataset is None:
            return

        def success(mask):
            if self.app_state.commit_filters(revision, rules, mask if rules else None):
                self.set_status(
                    f"Filters applied · {self.app_state.selected_count:,} selected rows"
                )

        self.run_task(
            "filters",
            "Applying filters…",
            lambda cancel: build_filter_mask(dataset.frame, rules, cancel),
            success,
        )

    def edit_filters(self):
        if self.app_state.dataset and self.app_state.dataset.numeric_columns:
            FilterDialog(self)

    def on_state_changed(self, reason):
        for key in ("scatter", "histogram", "statistics"):
            self.jobs.cancel(key)
        if reason == "dataset":
            self.jobs.cancel("filters")
            # Editors refer to the previous dataset. Close them before refreshing.
            for child in self.winfo_children():
                if isinstance(child, ctk.CTkToplevel):
                    child.destroy()
        dataset = self.app_state.dataset
        if dataset is None:
            return
        name = dataset.path.name
        self.filename.configure(text=name if len(name) < 90 else name[:87] + "…")
        self.metrics.configure(
            text=f"{len(dataset.frame):,} rows  ·  {len(dataset.columns)} columns  ·  "
            f"{dataset.missing_count:,} missing  ·  {dataset.infinite_count:,} infinite  ·  "
            f"{dataset.file_size / 1024**2:.1f} MiB  ·  {self.app_state.selected_count:,} selected"
        )
        self.filter_button.configure(
            text=f"Filters · {len(self.app_state.filters) or 'none'}",
            state="normal" if dataset.numeric_columns else "disabled",
        )
        for view in self.views:
            view.on_state(reason)

    def show_guide(self):
        dialog = Modal(self, "A quick guide", "650x650")
        dialog.note(
            "1. OPEN & INSPECT\nOpen CSV or DAT files. Import options control delimiter, encoding, headers, "
            "decimal separator, and explicit ISO date parsing. Data preview is read-only; missing values stay in the dataset."
        )
        dialog.note(
            "2. SELECT ROWS\nFilters apply inclusive numeric bounds to the original values. All conditions must match. "
            "Every tab uses the same selection. Gray points in Relationships show rows excluded by filters."
        )
        dialog.note(
            "3. COMPARE & PLOT\nChoose numeric columns. Absolute values affect the figure only. Log axes require "
            "positive values; the caption reports omitted rows. Bounds use displayed coordinates. "
            "Use Appearance, Annotations and Inset zoom to refine a figure, then plot again."
        )
        dialog.note(
            "4. DISTRIBUTIONS & STATISTICS\nHistogram density integrates to 1 within the displayed range. "
            "Statistics use full selected data, even if the scatter is sampled. Pearson correlations use paired finite rows."
        )
        dialog.note(
            "5. EXPORT\nExport figure saves the displayed view as PNG, PDF or SVG. Dense scatter layers are "
            "rasterized in vector exports for manageable file sizes. Statistics export preserves computed precision.\n\n"
            "Shortcuts: Ctrl+O open · Ctrl+R reload · Ctrl+C copy a selected table row."
        )
        button(dialog.footer, "Close", dialog.destroy, primary=True).pack(side="right")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.app_state.cancel_load()
        self.jobs.close()
        for view in (self.relationships_tab, self.distributions_tab):
            view.panel.clear()
        self.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explore CSV and scientific DAT datasets.")
    parser.add_argument("file", nargs="?", type=Path, help="Dataset to open at startup")
    parser.add_argument("--debug", action="store_true", help="Log diagnostic details to stderr")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        application = Application()
    except TclError as exc:
        print(
            f"Cannot start the desktop interface: {exc}\nRun from a graphical desktop with Tk installed.",
            file=sys.stderr,
        )
        return 1
    if args.file:
        application.after(100, lambda: application.load_path(args.file))
    application.mainloop()
    return 0
