"""Toolkit-neutral hard-gate semantics for SP-GUI-01."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from csv_analyzer.data import Dataset
from csv_analyzer.plots import ScatterSpec
from spikes.sp_gui_01.common.model import (
    EXACT,
    SEARCH_WHOLE,
    SOURCE_ROW,
    TABLE_ALL,
    TABLE_SELECTED,
    SearchRequest,
    SpikeModel,
)


def candidate(frame: pd.DataFrame, name: str = "candidate.csv") -> Dataset:
    return Dataset(frame=frame, path=Path(name), columns=())


def make_model() -> SpikeModel:
    frame = pd.DataFrame(
        {
            "int_exact": pd.Series(
                [9007199254740992, 9007199254740993, 7, 8, 9, pd.NA],
                dtype="Int64",
            ),
            "sort_key": pd.Series([2, 1, 1, pd.NA, 2, 1], dtype="Int64"),
            "filter_value": pd.Series([10, 20, 30, 40, 50, 60], dtype="Int64"),
            "text": pd.Series(["alpha", "needle", "Needle", "delta", None, "omega"]),
            "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "y": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
        }
    )
    model = SpikeModel(
        (SOURCE_ROW, "int_exact", "sort_key", "filter_value", "text", "x", "y")
    )
    ticket = model.begin_load()
    assert model.commit_candidate(ticket, candidate(frame))
    return model


class CommonContractTests(unittest.TestCase):
    def test_scope_source_identity_and_filter_are_independent(self):
        model = make_model()
        self.assertEqual(model.view.table_scope, TABLE_SELECTED)
        self.assertEqual(model.selected_source_ids().tolist(), [1, 2, 3, 4, 5, 6])
        self.assertEqual(model.apply_lower_filter("filter_value", 30), 4)
        self.assertEqual(model.selected_source_ids().tolist(), [3, 4, 5, 6])
        model.set_scope(TABLE_ALL)
        self.assertEqual(model.scope_source_ids().tolist(), [1, 2, 3, 4, 5, 6])
        self.assertEqual(model.app_state.selected_count, 4)

    def test_sort_is_global_null_last_and_ties_use_source_row(self):
        model = make_model()
        model.sort("sort_key")
        self.assertEqual(model.ordered_source_ids().tolist(), [2, 3, 6, 1, 5, 4])
        model.sort("sort_key", descending=True)
        self.assertEqual(model.ordered_source_ids().tolist(), [1, 5, 2, 3, 6, 4])
        model.reset_order()
        self.assertEqual(model.ordered_source_ids().tolist(), [1, 2, 3, 4, 5, 6])

    def test_exact_large_integer_search_and_copy_preserve_precision(self):
        model = make_model()
        matches = model.search(
            SearchRequest("9007199254740993", SEARCH_WHOLE, "int_exact", EXACT)
        )
        self.assertEqual(matches, (2,))
        copied = model.copy_tsv((1, 2), (SOURCE_ROW, "int_exact"))
        self.assertEqual(
            copied,
            "1\t9007199254740992\n2\t9007199254740993",
        )

    def test_whole_dataset_match_and_go_to_excluded_only_change_view_scope(self):
        model = make_model()
        model.apply_lower_filter("filter_value", 30)
        original_mask = model.app_state.mask.copy()
        original_count = model.app_state.selected_count
        matches = model.search(
            SearchRequest("needle", SEARCH_WHOLE, "text", "Contains")
        )
        self.assertEqual(matches, (2, 3))
        model.set_scope(TABLE_SELECTED)
        self.assertEqual(model.navigate_match(1), 2)
        self.assertEqual(model.view.table_scope, TABLE_ALL)
        self.assertEqual(model.navigate_match(1), 3)
        self.assertEqual(model.app_state.selected_count, original_count)
        np.testing.assert_array_equal(model.app_state.mask, original_mask)

    def test_visual_state_sort_search_and_scope_do_not_change_scatter_population(self):
        model = make_model()
        model.apply_lower_filter("filter_value", 30)
        spec = ScatterSpec("x", "y", sample_limit=None)
        before = model.scatter(spec)
        model.set_scope(TABLE_ALL)
        model.sort("sort_key", descending=True)
        model.search(SearchRequest("needle", SEARCH_WHOLE, "text", "Contains"))
        model.set_visual_selection((3, 5))
        after = model.scatter(spec)
        self.assertEqual(before.selected, 4)
        self.assertEqual(after.selected, 4)
        np.testing.assert_array_equal(before.x, after.x)
        np.testing.assert_array_equal(before.y, after.y)

    def test_cancelled_candidate_cannot_replace_active_dataset(self):
        model = make_model()
        active = model.dataset
        ticket = model.begin_load()
        model.cancel_load()
        other = candidate(pd.DataFrame({"x": [99]}), "B.csv")
        self.assertFalse(model.commit_candidate(ticket, other))
        self.assertIs(model.dataset, active)


if __name__ == "__main__":
    unittest.main()
