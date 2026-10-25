"""Frozen AC-01..AC-19 semantic gates shared byte-for-byte by both candidates."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from csv_analyzer.data import DataError, Dataset, load_dataset
from csv_analyzer.plots import ScatterSpec
from spikes.sp_gui_01.common.corpus import generate, sha256
from spikes.sp_gui_01.common.model import (
    EXACT,
    SEARCH_WHOLE,
    SOURCE_ROW,
    TABLE_ALL,
    TABLE_SELECTED,
    SearchRequest,
    SpikeModel,
    s200k_import_options,
    sort_source_ids,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
VISIBLE = tuple(MANIFEST["table"]["visible_columns"])


class FrozenHardGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get("SP_GUI_01_S200K")
        cls._tmp = None
        if supplied:
            cls.path = Path(supplied)
        else:
            cls._tmp = tempfile.TemporaryDirectory(prefix="sp-gui-01-")
            cls.path = generate(Path(cls._tmp.name) / "S-200K-mixed.csv")
        expected = MANIFEST["corpus"]["S-200K-mixed"]["sha256"]
        if sha256(cls.path) != expected:
            raise AssertionError("S-200K-mixed does not match frozen C0 SHA")
        cls.dataset = load_dataset(cls.path, s200k_import_options())

    @classmethod
    def tearDownClass(cls):
        if cls._tmp is not None:
            cls._tmp.cleanup()

    def model(self) -> SpikeModel:
        model = SpikeModel(VISIBLE)
        ticket = model.begin_load()
        self.assertTrue(model.commit_candidate(ticket, self.dataset))
        return model

    def filtered(self) -> SpikeModel:
        model = self.model()
        spec = MANIFEST["filter"]
        selected = model.apply_lower_filter(spec["column"], spec["value"])
        self.assertEqual(selected, spec["expected_selected"])
        return model

    def test_ac01_direct_source_row_ids_resolve_globally(self):
        model = self.model()
        for source_id in MANIFEST["table"]["go_to_source_row_ids"]:
            with self.subTest(source_id=source_id):
                self.assertEqual(model.go_to_source_row(source_id), source_id - 1)

    def test_ac02_source_row_identity_survives_view_operations(self):
        model = self.filtered()
        source_id = 199_999
        model.sort(MANIFEST["sort"]["column"])
        model.search(SearchRequest("needle", SEARCH_WHOLE, "text_00", "Contains"))
        model.set_scope(TABLE_ALL)
        model.reset_order()
        self.assertEqual(model.go_to_source_row(source_id), source_id - 1)
        self.assertEqual(model.source_ids[source_id - 1], source_id)

    def test_ac03_ac04_default_scope_and_filter_counts(self):
        model = self.model()
        self.assertEqual(model.view.table_scope, TABLE_SELECTED)
        self.assertEqual(model.app_state.selected_count, 200_000)
        model = self.filtered()
        k = MANIFEST["filter"]["expected_selected"]
        self.assertEqual(len(model.scope_source_ids()), k)
        model.set_scope(TABLE_ALL)
        self.assertEqual(len(model.scope_source_ids()), 200_000)
        self.assertEqual(model.app_state.selected_count, k)

    def test_ac05_ac06_ac07_global_sort_ties_and_nulls(self):
        frame = self.dataset.frame
        all_ids = np.arange(1, len(frame) + 1, dtype=np.int64)
        column = MANIFEST["sort"]["column"]
        asc = sort_source_ids(frame, all_ids, column, False)
        desc = sort_source_ids(frame, all_ids, column, True)
        self.assertEqual(
            asc[:10].tolist(), MANIFEST["sort"]["all_ascending_first10"]
        )
        self.assertEqual(
            asc[-10:].tolist(), MANIFEST["sort"]["all_ascending_last10"]
        )
        self.assertEqual(
            desc[:10].tolist(), MANIFEST["sort"]["all_descending_first10"]
        )
        self.assertEqual(
            desc[-10:].tolist(), MANIFEST["sort"]["all_ascending_last10"]
        )
        selected_ids = self.filtered().selected_source_ids()
        selected_asc = sort_source_ids(frame, selected_ids, column, False)
        self.assertEqual(
            selected_asc[:10].tolist(),
            MANIFEST["sort"]["selected_ascending_first10"],
        )

    def test_ac08_large_integer_exact_search_and_copy(self):
        model = self.model()
        a = int(model.dataset.frame.iloc[99_999]["int_exact"])
        b = int(model.dataset.frame.iloc[100_000]["int_exact"])
        self.assertEqual(a, 9007199254740992)
        self.assertEqual(b, 9007199254740993)
        self.assertNotEqual(a, b)
        request = SearchRequest(
            "9007199254740993", SEARCH_WHOLE, "int_exact", EXACT
        )
        self.assertEqual(model.search(request), (100_001,))
        copied = model.copy_tsv(
            MANIFEST["copy"]["source_row_ids"],
            MANIFEST["copy"]["columns"],
        )
        self.assertEqual(copied, MANIFEST["copy"]["expected_tsv"])

    def test_ac09_ac10_search_is_global_and_does_not_change_k(self):
        model = self.filtered()
        k = model.app_state.selected_count
        selected_query = MANIFEST["search"][1]
        request = SearchRequest(
            selected_query["query"],
            selected_query["scope"],
            selected_query["target"],
            selected_query["mode"],
        )
        self.assertEqual(
            list(model.search(request)),
            selected_query["expected_source_row_ids"],
        )
        self.assertEqual(model.app_state.selected_count, k)
        model.navigate_match(1)
        model.clear_search()
        self.assertEqual(model.app_state.selected_count, k)

    def test_ac11_sort_changes_navigation_order_not_membership(self):
        model = self.model()
        query = MANIFEST["search"][0]
        request = SearchRequest(
            query["query"],
            query["scope"],
            query["target"],
            query["mode"],
        )
        before = set(model.search(request))
        model.sort(MANIFEST["sort"]["column"], descending=True)
        after = set(model.view.match_ids)
        self.assertEqual(before, after)
        self.assertEqual(before, set(query["expected_source_row_ids"]))

    def test_ac12_whole_search_excluded_match_changes_view_only(self):
        model = self.filtered()
        k = model.app_state.selected_count
        original_mask = model.app_state.mask.copy()
        request = SearchRequest(
            "9007199254740993", SEARCH_WHOLE, "int_exact", EXACT
        )
        self.assertEqual(model.search(request), (100_001,))
        self.assertEqual(model.view.table_scope, TABLE_SELECTED)
        self.assertEqual(model.navigate_match(1), 100_001)
        self.assertEqual(model.view.table_scope, TABLE_ALL)
        self.assertEqual(model.app_state.selected_count, k)
        np.testing.assert_array_equal(model.app_state.mask, original_mask)

    def test_ac13_ac14_ac15_view_state_does_not_change_scatter_population(self):
        model = self.filtered()
        scatter = MANIFEST["scatter"]
        spec = ScatterSpec(
            scatter["x"],
            scatter["y"],
            sample_limit=scatter["sample_limit"],
            show_excluded=False,
        )
        before = model.scatter(spec)
        self.assertEqual(
            before.selected, scatter["expected_selected_after_filter"]
        )
        self.assertEqual(
            before.valid, scatter["expected_usable_after_filter"]
        )
        model.set_scope(TABLE_ALL)
        model.sort(MANIFEST["sort"]["column"], descending=True)
        model.search(
            SearchRequest("needle", SEARCH_WHOLE, "text_00", "Contains")
        )
        model.set_visual_selection((199_999, 100_001))
        after = model.scatter(spec)
        self.assertEqual(after.selected, before.selected)
        self.assertEqual(after.valid, before.valid)
        np.testing.assert_array_equal(after.x, before.x)
        np.testing.assert_array_equal(after.y, before.y)

    def test_ac16_copy_uses_underlying_values(self):
        model = self.model()
        self.assertEqual(
            model.copy_tsv(
                MANIFEST["copy"]["source_row_ids"],
                MANIFEST["copy"]["columns"],
            ),
            MANIFEST["copy"]["expected_tsv"],
        )

    def test_ac17_failed_or_cancelled_candidate_preserves_a(self):
        model = self.filtered()
        active = model.dataset
        selected = model.app_state.selected_count
        filters = model.app_state.filters
        mask = model.app_state.mask.copy()

        ticket = model.begin_load()
        model.cancel_load()
        stale = Dataset(pd.DataFrame({"x": [1]}), Path("B.csv"), ())
        self.assertFalse(model.commit_candidate(ticket, stale))
        self.assertIs(model.dataset, active)
        self.assertEqual(model.app_state.selected_count, selected)
        self.assertEqual(model.app_state.filters, filters)
        np.testing.assert_array_equal(model.app_state.mask, mask)

        invalid = ROOT / "fixtures" / "invalid_width.csv"
        with self.assertRaises(DataError):
            load_dataset(invalid)
        self.assertIs(model.dataset, active)
        self.assertEqual(model.app_state.selected_count, selected)

    def test_ac18_successful_b_commit_resets_selection_to_all(self):
        model = self.filtered()
        ticket = model.begin_load()
        frame = pd.DataFrame({"x": [10, 20, 30]})
        b = Dataset(frame, Path("B.csv"), ())
        self.assertTrue(model.commit_candidate(ticket, b))
        self.assertEqual(model.app_state.selected_count, 3)
        self.assertIsNone(model.app_state.mask)
        self.assertEqual(model.app_state.filters, ())
        self.assertEqual(model.source_ids.tolist(), [1, 2, 3])

    def test_ac19_temporal_edge_semantics_are_deferred(self):
        timestamp_columns = [
            f"timestamp_{index:02d}" for index in range(6)
        ]
        for column in timestamp_columns:
            self.assertTrue(
                pd.api.types.is_datetime64_any_dtype(
                    self.dataset.frame[column]
                )
            )
        self.assertEqual(MANIFEST["hard_gates"][-1], "AC-19")


if __name__ == "__main__":
    unittest.main()
