"""Desktop integration checks. Run normally on a desktop or under xvfb-run."""

import os
import sys
import time
import unittest
from pathlib import Path

from csv_analyzer.app import Application
from csv_analyzer.plots import InsetSpec, Overlay
from csv_analyzer.state import FilterRule
from csv_analyzer.ui.dialogs import (
    ColumnsDialog,
    FilterDialog,
    ImportDialog,
    InsetDialog,
    OverlayDialog,
    StyleDialog,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@unittest.skipIf(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    "A graphical display is required; headless logic tests still run",
)
class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.app = Application()
        self.errors = []
        self.app.show_error = lambda error, **kwargs: self.errors.append(error)
        self.app.report_callback_exception = lambda kind, error, tb: self.errors.append(error)
        self.addCleanup(self.app.close)
        self.pump()

    def pump(self, duration=0.15):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.005)

    def finish_jobs(self):
        deadline = time.monotonic() + 15
        while self.app.jobs.jobs and time.monotonic() < deadline:
            self.pump(0.02)
        self.assertFalse(self.app.jobs.jobs)
        self.pump()
        self.assertEqual(self.errors, [])

    def load(self, name):
        self.app.load_path(EXAMPLES / name)
        self.finish_jobs()

    def test_startup_dialogs_and_resize(self):
        self.assertTrue(callable(self.app.state), "Preserve Tk's window-state method")
        self.load("scientific_demo.csv")
        r = self.app.relationships_tab
        constructors = [
            lambda: ImportDialog(self.app),
            lambda: FilterDialog(self.app),
            lambda: StyleDialog(self.app, r.style, lambda _: None),
            lambda: OverlayDialog(self.app, (), lambda _: None),
            lambda: InsetDialog(self.app, None, lambda _: None),
            lambda: ColumnsDialog(
                self.app, self.app.app_state.dataset.numeric_columns, (), lambda _: None
            ),
        ]
        for make in constructors:
            dialog = make()
            self.pump()
            dialog.destroy()
            self.pump()
            make().destroy()  # Also close before delayed activation runs.
            self.pump()
        for geometry in ("1000x680", "1600x1000"):
            self.app.geometry(geometry)
            for tab in ("Data", "Relationships", "Distributions", "Statistics"):
                self.app.tabs.set(tab)
                self.pump()
        self.assertEqual(self.errors, [])

    def test_four_tabs_filters_figures_and_replacement(self):
        self.load("scientific_demo.csv")
        r, d, s = self.app.relationships_tab, self.app.distributions_tab, self.app.statistics_tab
        columns = self.app.app_state.dataset.numeric_columns
        self.assertEqual(tuple(r.x.column.choices), columns)
        r.color.set(columns[2])
        r.color_changed()
        r.overlays = (Overlay("Text", x=100, y=1, text="a,b = c"),)
        r.inset = InsetSpec(1, 100, 0.1, 10)
        self.app.tabs.set("Relationships")
        r.plot()
        self.finish_jobs()
        self.assertIsNotNone(r.panel.figure)
        self.app.tabs.set("Distributions")
        d.plot()
        self.finish_jobs()
        self.assertIsNotNone(d.panel.figure)
        self.app.tabs.set("Statistics")
        for mode in ("Numeric", "Categorical", "Correlation"):
            s.mode.set(mode)
            s.mode_changed()
            s.compute()
            self.finish_jobs()
            self.assertIsNotNone(s.result)
        self.app.apply_filters((FilterRule(columns[0], lower=100),))
        self.finish_jobs()
        self.assertIsNone(r.panel.figure)
        self.assertIsNone(d.panel.figure)
        self.assertIsNone(s.result)
        self.load("whitespace_demo.dat")
        self.assertEqual(tuple(r.x.column.choices), self.app.app_state.dataset.numeric_columns)
        self.assertEqual(r.overlays, ())
        self.assertIsNone(r.inset)
        self.assertIsNone(self.app.app_state.mask)


if __name__ == "__main__":
    unittest.main()
