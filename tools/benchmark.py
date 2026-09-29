"""Reproducible full-dataset check; run after installing the package.

Example: python tools/benchmark.py /path/to/test.csv --output /tmp/csv-check
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg

from csv_analyzer.analysis import build_filter_mask, correlation_summary, numeric_summary
from csv_analyzer.data import load_dataset
from csv_analyzer.plots import (
    HistogramSpec,
    ScatterSpec,
    export_figure,
    prepare_histogram,
    prepare_scatter,
    render_histogram,
    render_scatter,
)
from csv_analyzer.state import AppState, FilterRule


def peak_rss_mib():
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(value / (1024**2 if sys.platform == "darwin" else 1024), 2)
    except ImportError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "file": args.file.name,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "matplotlib", "customtkinter")
        },
        "seconds": {},
        "peak_rss_before_mib": peak_rss_mib(),
    }

    def timed(name, function):
        start = time.perf_counter()
        result = function()
        report["seconds"][name] = round(time.perf_counter() - start, 4)
        print(f"{name}: {report['seconds'][name]:.4f}s", flush=True)
        return result

    start = last_tick = time.perf_counter()
    intervals = []
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(load_dataset, args.file)
        while not future.done():
            time.sleep(0.01)
            now = time.perf_counter()
            intervals.append(now - last_tick)
            last_tick = now
        dataset = future.result()
    report["seconds"]["load_with_validation"] = round(time.perf_counter() - start, 4)
    report.update(
        rows=len(dataset.frame),
        columns=len(dataset.columns),
        numeric_columns=len(dataset.numeric_columns),
        missing=dataset.missing_count,
        infinite=dataset.infinite_count,
        file_mib=round(dataset.file_size / 1024**2, 2),
        dataframe_mib=round(dataset.memory_bytes / 1024**2, 2),
        peak_rss_after_load_mib=peak_rss_mib(),
        # A main-thread heartbeat measures scheduling availability, not GUI latency.
        load_heartbeat_max_ms=round(max(intervals, default=0) * 1000, 2),
        load_heartbeat_p95_ms=round(float(np.quantile(intervals, 0.95)) * 1000, 2)
        if intervals
        else 0,
    )
    print(
        f"Loaded {report['rows']:,} × {report['columns']} in "
        f"{report['seconds']['load_with_validation']:.2f}s",
        flush=True,
    )
    state = AppState()
    assert state.commit_dataset(state.begin_load(), dataset)
    assert state.dataset.frame is dataset.frame
    columns = dataset.numeric_columns
    if len(columns) < 2:
        raise ValueError("Benchmark needs at least two numeric columns")
    stats = timed("numeric_summary_all_rows", lambda: numeric_summary(dataset.frame))
    stats.to_csv(args.output / "numeric-summary.csv", index=False)
    correlations = timed(
        "correlations_first_four", lambda: correlation_summary(dataset.frame, columns[:4])
    )
    correlations.to_csv(args.output / "correlations.csv", index=False)
    pivot = float(dataset.frame[columns[0]].median())
    rules = (FilterRule(columns[0], lower=pivot),)
    mask = timed("filter", lambda: build_filter_mask(dataset.frame, rules))
    assert state.commit_filters(state.revision, rules, mask)
    report["selected_after_filter"] = state.selected_count
    report["filter_mask_mib"] = round(state.mask.nbytes / 1024**2, 3)
    spec = ScatterSpec(columns[0], columns[1], color=columns[2] if len(columns) > 2 else None)
    scatter = timed("scatter_prepare_all_rows", lambda: prepare_scatter(dataset.frame, spec))
    figure = timed("scatter_render_setup", lambda: render_scatter(scatter, spec))
    timed("scatter_draw_agg", lambda: FigureCanvasAgg(figure).draw())
    timed(
        "scatter_export_png",
        lambda: export_figure(figure, args.output / "scatter.png", dpi=150, overwrite=True),
    )
    report["scatter_caption"] = scatter.description
    figure.clear()
    hist_spec = HistogramSpec(columns[0], density=True)
    histogram = timed(
        "histogram_prepare_filtered",
        lambda: prepare_histogram(dataset.frame, hist_spec, state.mask),
    )
    hist_fig = render_histogram(histogram, hist_spec)
    timed("histogram_draw_agg", lambda: FigureCanvasAgg(hist_fig).draw())
    export_figure(hist_fig, args.output / "histogram.png", dpi=150, overwrite=True)
    report["histogram_caption"] = histogram.description
    report["peak_rss_total_mib"] = peak_rss_mib()
    hist_fig.clear()
    (args.output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
