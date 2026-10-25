"""Qt model/view tests for SP-GUI-01."""
from __future__ import annotations

import os
import unittest

import numpy as np
import pandas as pd

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:
    Qt = None
    QApplication = None

from spikes.sp_gui_01.common.model import SOURCE_ROW

if QApplication is not None:
    from spikes.sp_gui_01.qt.table_model import FrameTableModel


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class FrameTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_model_exposes_200k_rows_without_row_widgets_or_materialized_cells(self):
        frame = pd.DataFrame(
            {
                "int_exact": pd.Series(
                    np.arange(200_000, dtype=np.int64) + 9007199254000000,
                    dtype="Int64",
                )
            }
        )
        model = FrameTableModel()
        ids = np.arange(1, 200_001, dtype=np.int64)
        model.set_data(frame, (SOURCE_ROW, "int_exact"), ids)

        self.assertEqual(model.rowCount(), 200_000)
        self.assertEqual(model.columnCount(), 2)
        self.assertEqual(
            model.data(model.index(99_999, 0), Qt.ItemDataRole.DisplayRole),
            "100000",
        )
        self.assertEqual(model.row_for_source_id(200_000), 199_999)

    def test_display_keeps_adjacent_large_integers_distinct(self):
        frame = pd.DataFrame(
            {
                "int_exact": pd.Series(
                    [9007199254740992, 9007199254740993], dtype="Int64"
                )
            }
        )
        model = FrameTableModel()
        model.set_data(frame, (SOURCE_ROW, "int_exact"), np.array([1, 2]))
        first = model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole)
        second = model.data(model.index(1, 1), Qt.ItemDataRole.DisplayRole)
        self.assertEqual(first, "9007199254740992")
        self.assertEqual(second, "9007199254740993")
        self.assertNotEqual(first, second)


@unittest.skipUnless(
    QApplication is not None
    and (os.environ.get("DISPLAY") or os.environ.get("QT_QPA_PLATFORM") == "offscreen"),
    "Qt GUI environment required",
)
class QtShellTests(unittest.TestCase):
    def test_shell_has_same_three_areas_and_statistics_placeholder(self):
        from spikes.sp_gui_01.qt.app import QtSpikeWindow

        window = QtSpikeWindow()
        self.addCleanup(window.close)
        self.assertEqual(
            [window.tabs.tabText(i) for i in range(window.tabs.count())],
            ["Data", "Charts", "Statistics"],
        )
        self.assertEqual(window.scope_combo.currentText(), "Selected rows")
        labels = [
            child.text()
            for child in window.statistics_area.findChildren(type(window.status))
        ]
        self.assertTrue(any("Not implemented in spike" in text for text in labels))


if __name__ == "__main__":
    unittest.main()
