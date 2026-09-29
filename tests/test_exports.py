import tempfile
import unittest
from pathlib import Path

import pandas as pd

from csv_analyzer.analysis import numeric_summary
from csv_analyzer.data import DataError
from csv_analyzer.exports import atomic_destination, export_statistics
from csv_analyzer.plots import export_figure


class ExportTests(unittest.TestCase):
    def test_statistics_round_trip_includes_convention_and_precision(self):
        result = numeric_summary(pd.DataFrame({'x, quoted "label"': [1.23456789012345, 2.0]}))
        with tempfile.TemporaryDirectory() as directory:
            path = export_statistics(result, Path(directory) / "summary.csv")
            actual = pd.read_csv(path, float_precision="round_trip")
            pd.testing.assert_frame_equal(actual, result, check_dtype=False)
            self.assertEqual(actual["ddof"].tolist(), [1])
            with self.assertRaises(DataError):
                export_statistics(result, path)
            export_statistics(result, path, overwrite=True)

    def test_failed_write_preserves_destination_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_text("original")
            with self.assertRaisesRegex(OSError, "simulated"):
                with atomic_destination(path, overwrite=True) as temporary:
                    temporary.write_text("incomplete")
                    raise OSError("simulated disk failure")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_figure_export_does_not_replace_previous_file(self):
        class BrokenFigure:
            def savefig(self, path, **kwargs):
                path.write_text("incomplete")
                raise ValueError("invalid plot text")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.png"
            path.write_bytes(b"original image")
            with self.assertRaisesRegex(DataError, "invalid plot text"):
                export_figure(BrokenFigure(), path, overwrite=True)
            self.assertEqual(path.read_bytes(), b"original image")
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
