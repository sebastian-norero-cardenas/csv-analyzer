"""CTk-specific SP-GUI-01 adapter tests.

These tests require a real Tk display. They intentionally do not convert a headless
logic check into a GUI PASS.
"""
from __future__ import annotations

import os
import unittest

import numpy as np
import pandas as pd

from spikes.sp_gui_01.common.model import SOURCE_ROW
from spikes.sp_gui_01.ctk.virtual_table import VirtualTreeTable

try:
    import customtkinter as ctk
except ImportError:
    ctk = None


@unittest.skipUnless(ctk is not None and os.environ.get("DISPLAY"), "real Tk display required")
class VirtualTreeTableTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_fixed_widget_count_and_direct_navigation_on_large_order(self):
        frame = pd.DataFrame({"value": np.arange(200_000, dtype=np.int64)})
        table = VirtualTreeTable(self.root, window_rows=32)
        table.pack()
        ids = np.arange(1, 200_001, dtype=np.int64)
        table.set_data(frame, (SOURCE_ROW, "value"), ids)
        self.root.update_idletasks()

        self.assertEqual(len(table.tree.get_children()), 32)
        self.assertTrue(table.focus_source_id(100_000))
        self.assertIn(100_000, table.visible_source_ids())
        self.assertEqual(len(table.tree.get_children()), 32)
        self.assertTrue(table.focus_source_id(200_000))
        self.assertIn(200_000, table.visible_source_ids())
        self.assertEqual(len(table.tree.get_children()), 32)

    def test_visual_selection_tracks_source_identity_across_window_refresh(self):
        frame = pd.DataFrame({"value": np.arange(1000, dtype=np.int64)})
        seen = []
        table = VirtualTreeTable(
            self.root,
            window_rows=20,
            on_selection=lambda source_ids: seen.append(source_ids),
        )
        table.pack()
        table.set_data(frame, (SOURCE_ROW, "value"), np.arange(1, 1001))
        self.assertTrue(table.focus_source_id(500))
        self.root.update()
        self.assertIn(500, table.all_selected_source_ids())
        table._scroll_units(30)
        table._scroll_units(-30)
        self.root.update()
        self.assertIn(500, table.all_selected_source_ids())


if __name__ == "__main__":
    unittest.main()
