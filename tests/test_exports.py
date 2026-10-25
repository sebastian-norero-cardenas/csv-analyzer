import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_no_overwrite_race_preserves_concurrent_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.csv"
            with self.assertRaisesRegex(DataError, "created while|already exists"):
                with atomic_destination(path, overwrite=False) as temporary:
                    temporary.write_text("export")
                    path.write_text("created by another writer")
            self.assertEqual(path.read_text(), "created by another writer")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_statistics_no_overwrite_race_preserves_other_writer(self):
        frame = pd.DataFrame({"x": [1.25, 2.5]})
        original_to_csv = pd.DataFrame.to_csv
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"

            def racing_to_csv(self, temporary, *args, **kwargs):
                original_to_csv(self, temporary, *args, **kwargs)
                path.write_text("created by another writer")

            with patch.object(pd.DataFrame, "to_csv", new=racing_to_csv):
                with self.assertRaisesRegex(DataError, "created while|already exists"):
                    export_statistics(frame, path)
            self.assertEqual(path.read_text(), "created by another writer")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_figure_no_overwrite_race_preserves_other_writer(self):
        for suffix in ("png", "pdf", "svg"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"plot.{suffix}"

                class RacingFigure:
                    def savefig(self, temporary, **kwargs):
                        Path(temporary).write_bytes(b"complete export")
                        path.write_bytes(b"created by another writer")

                with self.assertRaisesRegex(DataError, "created while|already exists"):
                    export_figure(RacingFigure(), path)
                self.assertEqual(path.read_bytes(), b"created by another writer")
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_no_overwrite_rejects_existing_and_dangling_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            existing = root / "existing.csv"
            dangling = root / "dangling.csv"
            target.write_text("original")
            try:
                existing.symlink_to(target.name)
                dangling.symlink_to("missing.csv")
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(DataError, "already exists"):
                export_statistics(pd.DataFrame({"x": [1]}), existing)
            with self.assertRaisesRegex(DataError, "already exists"):
                export_statistics(pd.DataFrame({"x": [1]}), dangling)

            self.assertEqual(target.read_text(), "original")
            self.assertTrue(existing.is_symlink())
            self.assertTrue(dangling.is_symlink())
            self.assertFalse((root / "missing.csv").exists())
            self.assertEqual(set(root.iterdir()), {target, existing, dangling})

    def test_no_overwrite_unsupported_publication_is_safe_and_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "summary.csv"
            with patch(
                "csv_analyzer.exports.os.link",
                side_effect=OSError(errno.EOPNOTSUPP, "hard links unsupported"),
            ):
                with self.assertRaisesRegex(DataError, "safely publish"):
                    with atomic_destination(path, overwrite=False) as temporary:
                        temporary.write_text("complete export")
            self.assertFalse(path.exists())
            self.assertEqual(list(root.iterdir()), [])

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

    def test_overwrite_true_preserves_symlink_target_semantics(self):
        frame = pd.DataFrame({"x": [3.5]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            path = root / "alias.csv"
            target.write_text("original")
            try:
                path.symlink_to(target.name)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")
            export_statistics(frame, path, overwrite=True)
            self.assertTrue(path.is_symlink())
            actual = pd.read_csv(target)
            pd.testing.assert_frame_equal(actual, frame)

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
