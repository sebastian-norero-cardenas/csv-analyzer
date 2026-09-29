"""Real coordinator/worker/state integration with a headless view adapter."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from csv_analyzer.app import Application
from csv_analyzer.data import ImportOptions
from csv_analyzer.jobs import TaskRunner
from csv_analyzer.state import AppState, FilterRule


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class Coordinator:
    load_path = Application.load_path
    run_task = Application.run_task
    apply_filters = Application.apply_filters
    on_state_changed = Application.on_state_changed

    def __init__(self):
        self.app_state = AppState()
        self.import_options = ImportOptions()
        self.errors = []
        self.events = [[] for _ in range(4)]
        self.views = [SimpleNamespace(on_state=events.append) for events in self.events]
        self.filename = Widget()
        self.metrics = Widget()
        self.filter_button = Widget()
        self.tabs = SimpleNamespace(set=lambda _: None)
        self.jobs = TaskRunner(self)
        self.app_state.subscribe(self.on_state_changed)

    def after(self, delay, callback):
        return "poll"

    def after_cancel(self, callback):
        pass

    def winfo_children(self):
        return []

    def set_status(self, text):
        self.status_text = text

    def show_error(self, error):
        self.errors.append(error)


class CoordinatorTests(unittest.TestCase):
    def test_import_filter_failed_load_and_replacement_across_views(self):
        app = Coordinator()
        self.addCleanup(app.jobs.close)

        def drain():
            deadline = time.monotonic() + 5
            while app.jobs.jobs and time.monotonic() < deadline:
                app.jobs.poll()
                time.sleep(0.005)
            self.assertFalse(app.jobs.jobs, "Worker did not complete")

        with tempfile.TemporaryDirectory() as directory:
            first, second, bad = [Path(directory) / name for name in ("a.csv", "b.dat", "bad.csv")]
            first.write_text("x,y\n1,2\n3,4\n5,6\n")
            second.write_text("# mass rate\n10 20\n")
            bad.write_text("x,y\n1,2,3\n")
            app.load_path(first)
            self.assertIsNone(app.app_state.dataset)
            drain()
            original = app.app_state.dataset
            self.assertEqual(original.frame.shape, (3, 2))
            app.apply_filters((FilterRule("x", lower=3),))
            drain()
            self.assertEqual(app.app_state.selected_count, 2)
            mask = app.app_state.mask
            app.load_path(bad)
            drain()
            self.assertEqual(len(app.errors), 1)
            self.assertIs(app.app_state.dataset, original)
            self.assertIs(app.app_state.mask, mask)
            app.load_path(second)
            drain()
            self.assertEqual(app.app_state.dataset.numeric_columns, ("mass", "rate"))
            self.assertIsNone(app.app_state.mask)
            self.assertEqual(app.app_state.filters, ())
            for events in app.events:
                self.assertEqual(events, ["dataset", "filters", "dataset"])
            self.assertEqual(app.filename.options["text"], "b.dat")


if __name__ == "__main__":
    unittest.main()
