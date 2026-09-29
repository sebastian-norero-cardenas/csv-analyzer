import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg

from csv_analyzer.analysis import (
    build_filter_mask,
    categorical_summary,
    correlation_summary,
    numeric_summary,
)
from csv_analyzer.data import DataError
from csv_analyzer.plots import (
    AxisSpec,
    HistogramSpec,
    InsetSpec,
    Overlay,
    ScatterSpec,
    export_figure,
    prepare_histogram,
    prepare_scatter,
    render_histogram,
    render_scatter,
)
from csv_analyzer.state import FilterRule


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "x": [1.0, 2.0, 3.0, 4.0, np.nan, np.inf],
                "y": [2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
                "constant": [5.0] * 6,
                "group": ["a", "b", "a", None, "b", "b"],
                "flag": [True, False, True, False, True, False],
            }
        )

    def test_known_sample_and_population_statistics(self):
        row = numeric_summary(self.frame).set_index("Column").loc["x"]
        self.assertEqual(row["Rows"], 6)
        self.assertEqual(row["Valid"], 4)
        self.assertEqual(row["Missing"], 1)
        self.assertEqual(row["Infinite"], 1)
        self.assertAlmostEqual(row["Mean"], 2.5)
        self.assertAlmostEqual(row["Variance"], 5 / 3)
        self.assertAlmostEqual(row["Q1"], 1.75)
        self.assertAlmostEqual(row["Median"], 2.5)
        row = numeric_summary(self.frame, ddof=0).set_index("Column").loc["x"]
        self.assertAlmostEqual(row["Variance"], 1.25)

    def test_empty_single_and_extreme_statistics(self):
        result = numeric_summary(pd.DataFrame({"empty": [np.nan], "single": [2.0]})).set_index(
            "Column"
        )
        self.assertTrue(np.isnan(result.loc["single", "Std"]))
        self.assertTrue(np.isnan(result.loc["empty", "Mean"]))
        result = numeric_summary(pd.DataFrame({"x": [1e308, 1e308]})).iloc[0]
        self.assertEqual(result["Mean"], 1e308)
        self.assertEqual(result["Std"], 0)

    def test_filters_raw_inclusive_and_finite(self):
        mask = build_filter_mask(self.frame, (FilterRule("x", 2, 4), FilterRule("y", None, 6)))
        self.assertEqual(mask.tolist(), [False, True, True, False, False, False])
        result = numeric_summary(self.frame, mask).set_index("Column")
        self.assertAlmostEqual(result.loc["x", "Mean"], 2.5)
        self.assertEqual(result.loc["y", "Valid"], 2)

    def test_bad_filters(self):
        for rule in (
            FilterRule("group", 1),
            FilterRule("absent", 1),
            FilterRule("x"),
            FilterRule("x", 4, 2),
            FilterRule("x", np.nan),
        ):
            with self.subTest(rule=rule), self.assertRaises(DataError):
                build_filter_mask(self.frame, (rule,))

    def test_categorical_missing_and_boolean(self):
        result = categorical_summary(self.frame).set_index("Column")
        self.assertEqual(result.loc["group", "Unique"], 2)
        self.assertEqual(result.loc["group", "Most frequent"], "b")
        self.assertEqual(result.loc["group", "Frequency"], 3)
        self.assertEqual(result.loc["flag", "Tied modes"], 2)

    def test_pairwise_correlations_and_constants(self):
        result = correlation_summary(self.frame, ("x", "y", "constant"))
        self.assertEqual(result.iloc[0]["Paired N"], 4)
        self.assertAlmostEqual(result.iloc[0]["Pearson r"], 1)
        self.assertTrue(np.isnan(result.iloc[1]["Pearson r"]))
        self.assertEqual(result.iloc[1]["Note"], "Constant column")


class PlotTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "x": [-2.0, -1.0, 0.0, 1.0, 2.0, np.nan],
                "y": [4.0, 1.0, 0.0, 1.0, 4.0, 9.0],
                "c": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            },
            index=[1] * 6,
        )

    def draw(self, figure):
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        self.assertGreater(canvas.buffer_rgba().nbytes, 10000)

    def test_aligned_finite_points_and_duplicate_indices(self):
        frame = pd.DataFrame({"x": [1.0, np.nan, 3.0], "y": [10.0, 20.0, np.nan]})
        data = prepare_scatter(frame, ScatterSpec("x", "y"))
        self.assertEqual(data.x.tolist(), [1.0])
        self.assertEqual(data.y.tolist(), [10.0])
        mask = build_filter_mask(self.frame, (FilterRule("x", 0, 2),))
        data = prepare_scatter(self.frame, ScatterSpec("x", "y"), mask)
        self.assertEqual(data.x.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(data.excluded_x.tolist(), [-2.0, -1.0])

    def test_abs_log_and_excluded_transform(self):
        spec = ScatterSpec("x", "y", x_axis=AxisSpec("log", absolute=True))
        mask = np.array([False, False, True, True, True, False])
        data = prepare_scatter(self.frame, spec, mask)
        self.assertEqual(data.x.tolist(), [1.0, 2.0])
        self.assertEqual(data.excluded_x.tolist(), [2.0, 1.0])
        self.assertEqual(data.selected - data.valid, 1)

    def test_color_sampling_uses_full_normalization_and_is_deterministic(self):
        frame = pd.DataFrame(
            {"x": np.arange(1, 101.0), "y": np.arange(100.0), "z": np.arange(100.0)}
        )
        # Use consistent lengths independently of the data preparation pipeline.
        spec = ScatterSpec("x", "y", color="z", sample_limit=5)
        a, b = prepare_scatter(frame, spec), prepare_scatter(frame, spec)
        np.testing.assert_array_equal(a.x, b.x)
        self.assertEqual(len(a.x), 5)
        self.assertEqual(a.color_range, (0.0, 99.0))

    def test_color_log_drops_invalid_selected_values(self):
        frame = self.frame.copy()
        frame.loc[:, "c"] = [-1.0, 0.0, 1.0, 2.0, np.nan, 3.0]
        data = prepare_scatter(frame, ScatterSpec("x", "y", color="c", color_scale="log"))
        self.assertEqual(data.x.tolist(), [0.0, 1.0])

    def test_render_scientific_inset_and_structured_annotations(self):
        spec = ScatterSpec(
            "x",
            "y",
            color="c",
            x_axis=AxisSpec(absolute=True, scientific=True),
            overlays=(
                Overlay("Text", x=1, y=3, text="a,b = c", opacity=0.3),
                Overlay("Point", x=1, y=2, size=90, opacity=0.2),
                Overlay("Horizontal line", y=2),
                Overlay("Slope line", slope=2),
            ),
            inset=InsetSpec(0.5, 2, 0.5, 4),
        )
        data = prepare_scatter(self.frame, spec)
        fig = render_scatter(data, spec)
        self.draw(fig)
        ax = fig.axes[0]
        self.assertEqual(ax.texts[0].get_text(), "a,b = c")
        self.assertEqual(ax.texts[0].get_alpha(), 0.3)
        self.assertEqual(ax.child_axes[0].collections[0].get_sizes()[0], spec.size)
        self.assertIs(ax.collections[0].norm, ax.child_axes[0].collections[0].norm)

    def test_excluded_drawn_once_and_unfilled_markers_visible(self):
        spec = ScatterSpec("x", "y", marker="x")
        data = prepare_scatter(self.frame, spec, np.array([True, False, True, False, True, False]))
        fig = render_scatter(data, spec)
        self.draw(fig)
        self.assertEqual(len(fig.axes[0].collections), 2)
        self.assertGreater(fig.axes[0].collections[0].get_linewidths()[0], 0)

    def test_invalid_plot_parameters_and_no_usable_data(self):
        base = ScatterSpec("x", "y")
        for spec in (
            replace(base, opacity=2),
            replace(base, sample_limit=0),
            replace(base, x_axis=AxisSpec(lower=3, upper=1)),
            replace(base, x_axis=AxisSpec("log", lower=0)),
            replace(base, color="missing"),
        ):
            with self.subTest(spec=spec), self.assertRaises(DataError):
                prepare_scatter(self.frame, spec)
        with self.assertRaises(DataError):
            prepare_scatter(self.frame, base, np.zeros(6, dtype=bool))

    def test_histogram_known_counts_and_density(self):
        frame = pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0, 4.0, np.nan, np.inf]})
        spec = HistogramSpec("x", bins=2, density=True)
        data = prepare_histogram(frame, spec)
        self.assertEqual(data.counts.tolist(), [2, 3])
        self.assertEqual(data.valid, 5)
        fig = render_histogram(data, spec)
        self.draw(fig)
        heights = fig.axes[0].patches[0].get_data().values
        self.assertAlmostEqual(np.sum(heights * np.diff(data.edges)), 1)

    def test_histogram_log_bins_and_constant(self):
        spec = HistogramSpec("x", axis=AxisSpec("log"), log_bins=True, bins=2)
        data = prepare_histogram(pd.DataFrame({"x": [1.0, 10.0, 100.0, 0.0, -1.0]}), spec)
        np.testing.assert_allclose(data.edges, [1.0, 10.0, 100.0])
        self.assertEqual(data.valid, 3)
        spec = replace(spec, log_bins=False)
        data = prepare_histogram(pd.DataFrame({"x": [1e-10] * 4}), spec)
        self.assertTrue((data.edges > 0).all())
        self.draw(render_histogram(data, spec))

    def test_histogram_range_and_bin_validation(self):
        spec = HistogramSpec("x", axis=AxisSpec(lower=0, upper=1), bins=2)
        data = prepare_histogram(self.frame, spec)
        self.assertEqual(data.valid, 5)
        self.assertEqual(data.in_range, 2)
        for bins in (0, 5000, "bad"):
            with self.assertRaises(DataError):
                prepare_histogram(self.frame, replace(spec, bins=bins))

    def test_one_sided_scatter_bounds_cannot_reverse_axes(self):
        spec = ScatterSpec("x", "y", x_axis=AxisSpec(lower=100))
        with self.assertRaisesRegex(DataError, "automatic range"):
            render_scatter(prepare_scatter(self.frame, spec), spec)

    def test_unrepresentable_density_reports_actionable_error(self):
        frame = pd.DataFrame({"x": [1e-310, 2e-310]})
        spec = HistogramSpec("x", bins=2, density=True)
        with self.assertRaisesRegex(DataError, "Use counts"):
            render_histogram(prepare_histogram(frame, spec), spec)

    def test_tiny_constant_color_range_retains_scientific_scale(self):
        frame = self.frame.assign(tiny=1e-100)
        spec = ScatterSpec("x", "y", color="tiny", color_scientific=True)
        fig = render_scatter(prepare_scatter(frame, spec), spec)
        self.draw(fig)
        norm = fig.axes[0].collections[0].norm
        self.assertGreater(norm.vmin, 0)
        self.assertLess(norm.vmax, 2e-100)

    def test_export_all_formats_and_overwrite_protection(self):
        spec = ScatterSpec("x", "y")
        fig = render_scatter(prepare_scatter(self.frame, spec), spec)
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ("png", "pdf", "svg"):
                path = Path(directory) / f"plot.{suffix}"
                export_figure(fig, path, dpi=100)
                self.assertGreater(path.stat().st_size, 1000)
                with self.assertRaises(DataError):
                    export_figure(fig, path)
                export_figure(fig, path, overwrite=True)
            self.assertEqual(len(list(Path(directory).iterdir())), 3)


if __name__ == "__main__":
    unittest.main()
