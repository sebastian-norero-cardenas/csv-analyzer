"""Export callback safety tests that do not require constructing Tk widgets."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from matplotlib.figure import Figure

from csv_analyzer.data import DataError
from csv_analyzer.ui.plot_panel import PlotPanel
from csv_analyzer.ui.statistics_tab import StatisticsTab


class ExportCallbackAdapterTests(unittest.TestCase):
    def make_app(self):
        errors = []
        statuses = []
        app = SimpleNamespace(show_error=errors.append, set_status=statuses.append)
        return app, errors, statuses

    def make_statistics_tab(self):
        app, errors, statuses = self.make_app()
        tab = SimpleNamespace(result=pd.DataFrame({"x": [1]}), app=app)
        return tab, errors, statuses

    def make_plot_panel(self):
        app, errors, statuses = self.make_app()
        figure = Figure()
        figure.subplots().plot([0, 1], [1, 0])
        panel = SimpleNamespace(
            figure=figure,
            dpi=SimpleNamespace(get=lambda: "150 DPI"),
            app=app,
        )
        return panel, errors, statuses

    def test_new_destinations_export_without_replacement_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tab, errors, statuses = self.make_statistics_tab()
            csv_path = root / "new.csv"
            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(csv_path),
                ),
                patch("tkinter.messagebox.askyesno") as confirm,
            ):
                StatisticsTab.export(tab)
            self.assertTrue(csv_path.exists())
            confirm.assert_not_called()
            self.assertEqual(errors, [])
            self.assertEqual(statuses, [f"Statistics exported to {csv_path.resolve()}"])

            for suffix in ("png", "pdf", "svg"):
                with self.subTest(suffix=suffix):
                    panel, errors, statuses = self.make_plot_panel()
                    path = root / f"new.{suffix}"
                    with (
                        patch(
                            "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                            return_value=str(path),
                        ),
                        patch("tkinter.messagebox.askyesno") as confirm,
                    ):
                        PlotPanel.export(panel)
                    self.assertTrue(path.exists())
                    confirm.assert_not_called()
                    self.assertEqual(errors, [])
                    self.assertEqual(statuses, [f"Figure exported to {path.resolve()}"])

    def test_preexisting_destinations_require_confirmation_and_decline_preserves_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tab, errors, statuses = self.make_statistics_tab()
            csv_path = root / "existing.csv"
            csv_path.write_bytes(b"OTHER WRITER")
            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(csv_path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
            ):
                StatisticsTab.export(tab)
            self.assertEqual(csv_path.read_bytes(), b"OTHER WRITER")
            confirm.assert_called_once()
            self.assertEqual(errors, [])
            self.assertEqual(statuses, [])
            self.assertEqual(list(root.iterdir()), [csv_path])

            for suffix in ("png", "pdf", "svg"):
                with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as subdir:
                    path = Path(subdir) / f"existing.{suffix}"
                    path.write_bytes(b"OTHER WRITER")
                    panel, errors, statuses = self.make_plot_panel()
                    with (
                        patch(
                            "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                            return_value=str(path),
                        ),
                        patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
                    ):
                        PlotPanel.export(panel)
                    self.assertEqual(path.read_bytes(), b"OTHER WRITER")
                    confirm.assert_called_once()
                    self.assertEqual(errors, [])
                    self.assertEqual(statuses, [])
                    self.assertEqual(list(Path(subdir).iterdir()), [path])

    def test_concurrent_destinations_require_confirmation_and_decline_preserves_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.csv"
            tab, errors, statuses = self.make_statistics_tab()
            original = pd.DataFrame.to_csv

            def racing_to_csv(frame, temporary, *args, **kwargs):
                original(frame, temporary, *args, **kwargs)
                path.write_bytes(b"OTHER WRITER")

            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(path),
                ),
                patch.object(pd.DataFrame, "to_csv", new=racing_to_csv),
                patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
            ):
                StatisticsTab.export(tab)
            self.assertEqual(path.read_bytes(), b"OTHER WRITER")
            confirm.assert_called_once()
            self.assertEqual(errors, [])
            self.assertEqual(statuses, [])
            self.assertEqual(list(Path(directory).iterdir()), [path])

        for suffix in ("png", "pdf", "svg"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"race.{suffix}"
                panel, errors, statuses = self.make_plot_panel()
                original = panel.figure.savefig

                def racing_savefig(temporary, *args, **kwargs):
                    original(temporary, *args, **kwargs)
                    path.write_bytes(b"OTHER WRITER")

                with (
                    patch(
                        "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                        return_value=str(path),
                    ),
                    patch.object(panel.figure, "savefig", side_effect=racing_savefig),
                    patch("tkinter.messagebox.askyesno", return_value=False) as confirm,
                ):
                    PlotPanel.export(panel)
                self.assertEqual(path.read_bytes(), b"OTHER WRITER")
                confirm.assert_called_once()
                self.assertEqual(errors, [])
                self.assertEqual(statuses, [])
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_save_dialog_cancelled_does_not_export_or_report_success(self):
        tab, errors, statuses = self.make_statistics_tab()
        with (
            patch(
                "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                return_value="",
            ),
            patch("csv_analyzer.ui.statistics_tab.export_statistics") as exporter,
            patch("tkinter.messagebox.askyesno") as confirm,
        ):
            StatisticsTab.export(tab)
        exporter.assert_not_called()
        confirm.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [])

        panel, errors, statuses = self.make_plot_panel()
        with (
            patch(
                "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                return_value="",
            ),
            patch("csv_analyzer.ui.plot_panel.export_figure") as exporter,
            patch("tkinter.messagebox.askyesno") as confirm,
        ):
            PlotPanel.export(panel)
        exporter.assert_not_called()
        confirm.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [])

    def test_confirmed_replacement_retries_with_overwrite_and_reports_published_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "existing.csv"
            path.write_bytes(b"OLD")
            tab, errors, statuses = self.make_statistics_tab()
            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(path),
                ),
                patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
            ):
                StatisticsTab.export(tab)
            confirm.assert_called_once()
            self.assertNotEqual(path.read_bytes(), b"OLD")
            self.assertEqual(errors, [])
            self.assertEqual(statuses, [f"Statistics exported to {path.resolve()}"])
            self.assertEqual(list(root.iterdir()), [path])

        for suffix in ("png", "pdf", "svg"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"existing.{suffix}"
                path.write_bytes(b"OLD")
                panel, errors, statuses = self.make_plot_panel()
                with (
                    patch(
                        "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                        return_value=str(path),
                    ),
                    patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
                ):
                    PlotPanel.export(panel)
                confirm.assert_called_once()
                self.assertNotEqual(path.read_bytes(), b"OLD")
                self.assertEqual(errors, [])
                self.assertEqual(statuses, [f"Figure exported to {path.resolve()}"])
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_concurrent_conflict_can_be_explicitly_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.csv"
            tab, errors, statuses = self.make_statistics_tab()
            original = pd.DataFrame.to_csv
            calls = 0

            def racing_once(frame, temporary, *args, **kwargs):
                nonlocal calls
                calls += 1
                original(frame, temporary, *args, **kwargs)
                if calls == 1:
                    path.write_bytes(b"OTHER WRITER")

            with (
                patch(
                    "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                    return_value=str(path),
                ),
                patch.object(pd.DataFrame, "to_csv", new=racing_once),
                patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
            ):
                StatisticsTab.export(tab)
            self.assertEqual(calls, 2)
            confirm.assert_called_once()
            self.assertNotEqual(path.read_bytes(), b"OTHER WRITER")
            self.assertEqual(errors, [])
            self.assertEqual(statuses, [f"Statistics exported to {path.resolve()}"])
            self.assertEqual(list(Path(directory).iterdir()), [path])

        for suffix in ("png", "pdf", "svg"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"race.{suffix}"
                panel, errors, statuses = self.make_plot_panel()
                original = panel.figure.savefig
                calls = 0

                def racing_once(temporary, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    original(temporary, *args, **kwargs)
                    if calls == 1:
                        path.write_bytes(b"OTHER WRITER")

                with (
                    patch(
                        "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                        return_value=str(path),
                    ),
                    patch.object(panel.figure, "savefig", side_effect=racing_once),
                    patch("tkinter.messagebox.askyesno", return_value=True) as confirm,
                ):
                    PlotPanel.export(panel)
                self.assertEqual(calls, 2)
                confirm.assert_called_once()
                self.assertNotEqual(path.read_bytes(), b"OTHER WRITER")
                self.assertEqual(errors, [])
                self.assertEqual(statuses, [f"Figure exported to {path.resolve()}"])
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_non_conflict_error_is_reported_without_replacement_prompt(self):
        tab, errors, statuses = self.make_statistics_tab()
        with (
            patch(
                "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                return_value="/tmp/failure.csv",
            ),
            patch(
                "csv_analyzer.ui.statistics_tab.export_statistics",
                side_effect=DataError("simulated export failure"),
            ),
            patch("tkinter.messagebox.askyesno") as confirm,
        ):
            StatisticsTab.export(tab)
        confirm.assert_not_called()
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "simulated export failure")
        self.assertEqual(statuses, [])

    def test_status_uses_destination_returned_by_exporter(self):
        chosen = "/tmp/chosen.csv"
        published = Path("/tmp/resolved.csv")
        tab, errors, statuses = self.make_statistics_tab()
        with (
            patch(
                "csv_analyzer.ui.statistics_tab.filedialog.asksaveasfilename",
                return_value=chosen,
            ),
            patch(
                "csv_analyzer.ui.statistics_tab.export_statistics",
                return_value=published,
            ),
            patch("tkinter.messagebox.askyesno") as confirm,
        ):
            StatisticsTab.export(tab)
        confirm.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [f"Statistics exported to {published}"])

        panel, errors, statuses = self.make_plot_panel()
        chosen = "/tmp/chosen.png"
        published = Path("/tmp/resolved.png")
        with (
            patch(
                "csv_analyzer.ui.plot_panel.filedialog.asksaveasfilename",
                return_value=chosen,
            ),
            patch(
                "csv_analyzer.ui.plot_panel.export_figure",
                return_value=published,
            ),
            patch("tkinter.messagebox.askyesno") as confirm,
        ):
            PlotPanel.export(panel)
        confirm.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [f"Figure exported to {published}"])


if __name__ == "__main__":
    unittest.main()
