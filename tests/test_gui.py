"""Desktop integration checks. Run normally on a desktop or under xvfb-run."""

import os
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from csv_analyzer.app import Application
from csv_analyzer.data import DataError
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
from csv_analyzer.ui.widgets import entry_value

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class EntryValueTests(unittest.TestCase):
    class Entry:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    def test_exact_integer_filter_input_preserves_large_values(self):
        huge = 10**400
        values = (
            ("9007199254740993", 9007199254740993),
            ("9.007199254740993e15", 9007199254740993),
            ("9007199254740992.5", Decimal("9007199254740992.5")),
            (str(huge), huge),
            (str(-huge), -huge),
            ("1e400", huge),
            ("-1e400", -huge),
            ("1e-400", Decimal("1e-400")),
        )
        for text, expected in values:
            with self.subTest(text=text):
                self.assertEqual(entry_value(self.Entry(text), "Bound", exact=True), expected)

        for text in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(text=text), self.assertRaises(DataError):
                entry_value(self.Entry(text), "Bound", exact=True)


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

    def test_plot_panel_clear_replot_and_toolbar_lifecycle(self):
        self.load("scientific_demo.csv")
        r = self.app.relationships_tab
        self.app.tabs.set("Relationships")
        r.plot()
        self.finish_jobs()

        first_canvas = r.panel.canvas
        first_toolbar = r.panel.toolbar
        self.assertIsNotNone(r.panel.figure)
        self.assertIs(first_canvas.toolbar, first_toolbar)
        self.assertTrue({"Home", "Back", "Forward", "Pan", "Zoom"} <= set(first_toolbar._buttons))

        # Replot through PlotPanel.show(); the old canvas/toolbar must be fully
        # detached while the replacement keeps normal navigation available.
        r.plot()
        self.finish_jobs()
        second_canvas = r.panel.canvas
        second_toolbar = r.panel.toolbar
        self.assertIsNot(second_canvas, first_canvas)
        self.assertIsNone(first_canvas.toolbar)
        self.assertIsNone(first_toolbar.canvas)
        self.assertIsNone(first_canvas.figure)
        self.assertIs(second_canvas.toolbar, second_toolbar)
        self.assertTrue(
            {"Home", "Back", "Forward", "Pan", "Zoom"} <= set(second_toolbar._buttons)
        )

        second_canvas.draw_idle()
        second_canvas._event_loop_id = second_canvas.get_tk_widget().after(
            10_000, lambda: None
        )
        r.panel.clear()

        self.assertIsNone(r.panel.figure)
        self.assertIsNone(r.panel.canvas)
        self.assertIsNone(r.panel.toolbar)
        self.assertIsNone(second_canvas.toolbar)
        self.assertIsNone(second_toolbar.canvas)
        self.assertIsNone(second_canvas.figure)
        self.assertIsNone(second_canvas._idle_draw_id)
        self.assertIsNone(second_canvas._event_loop_id)
        self.assertEqual(r.panel.export_button.cget("state"), "disabled")

        r.panel.clear()  # Idempotent after a complete teardown.

    def test_real_widgets_do_not_replace_without_explicit_confirmation(self):
        self.load("scientific_demo.csv")
        relationships = self.app.relationships_tab
        statistics = self.app.statistics_tab

        relationships.plot()
        self.finish_jobs()
        self.app.tabs.set("Statistics")
        statistics.compute()
        self.finish_jobs()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "existing.csv"
            png_path = root / "existing.png"
            csv_path.write_bytes(b"CSV OTHER")
            png_path.write_bytes(b"PNG OTHER")

            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(csv_path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
            ):
                statistics.export()
            self.assertEqual(csv_path.read_bytes(), b"CSV OTHER")
            confirm.assert_called_once()

            with (
                patch(
                    "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                    return_value=str(png_path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
            ):
                relationships.panel.export()
            self.assertEqual(png_path.read_bytes(), b"PNG OTHER")
            confirm.assert_called_once()

            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(csv_path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
            ):
                statistics.export()
            self.assertNotEqual(csv_path.read_bytes(), b"CSV OTHER")
            confirm.assert_called_once()

            with (
                patch(
                    "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                    return_value=str(png_path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
            ):
                relationships.panel.export()
            self.assertNotEqual(png_path.read_bytes(), b"PNG OTHER")
            confirm.assert_called_once()

        self.assertEqual(self.errors, [])

    def test_close_after_plot(self):
        self.load("scientific_demo.csv")
        r = self.app.relationships_tab
        r.plot()
        self.finish_jobs()
        canvas = r.panel.canvas

        self.app.close()

        self.assertTrue(self.app._closed)
        self.assertIsNone(r.panel.figure)
        self.assertIsNone(r.panel.canvas)
        self.assertIsNone(r.panel.toolbar)
        self.assertIsNone(canvas.toolbar)
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

        # Recreate derived results so dataset replacement exercises teardown
        # while plots/statistics are active, rather than only after filtering
        # has already cleared them.
        r.plot()
        self.finish_jobs()
        d.plot()
        self.finish_jobs()
        s.compute()
        self.finish_jobs()
        self.assertIsNotNone(r.panel.figure)
        self.assertIsNotNone(d.panel.figure)
        self.assertIsNotNone(s.result)

        self.load("whitespace_demo.dat")
        self.assertIsNone(r.panel.figure)
        self.assertIsNone(d.panel.figure)
        self.assertIsNone(s.result)
        self.assertEqual(tuple(r.x.column.choices), self.app.app_state.dataset.numeric_columns)
        self.assertEqual(r.overlays, ())
        self.assertIsNone(r.inset)
        self.assertIsNone(self.app.app_state.mask)


if __name__ == "__main__":
    unittest.main()
