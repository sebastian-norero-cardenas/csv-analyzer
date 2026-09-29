"""Scientific plot preparation and rendering without GUI dependencies or pyplot."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib as mpl
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm, Normalize, is_color_like
from matplotlib.figure import Figure
from matplotlib.markers import MarkerStyle
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, ScalarFormatter

from .analysis import numeric_array, selected_mask
from .data import DataError, check_cancelled
from .exports import atomic_destination


@dataclass(frozen=True)
class AxisSpec:
    scale: str = "linear"
    scientific: bool = False
    absolute: bool = False
    lower: float | None = None
    upper: float | None = None
    label: str = ""


@dataclass(frozen=True)
class Overlay:
    kind: str
    x: float = 0.0
    y: float = 0.0
    x2: float = 1.0
    y2: float = 1.0
    slope: float = 1.0
    text: str = ""
    color: str = "#C44536"
    opacity: float = 1.0
    size: float = 30.0
    linewidth: float = 1.5
    linestyle: str = "--"
    marker: str = "o"
    fontsize: float = 11.0
    rotation: float = 0.0


@dataclass(frozen=True)
class InsetSpec:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    width: float = 0.35
    height: float = 0.35
    location: str = "upper right"
    rectangle: bool = True
    edgecolor: str = "#C44536"
    linewidth: float = 1.2
    x_scale: str | None = None
    y_scale: str | None = None


@dataclass(frozen=True)
class ScatterSpec:
    x: str
    y: str
    x_axis: AxisSpec = field(default_factory=AxisSpec)
    y_axis: AxisSpec = field(default_factory=AxisSpec)
    color: str | None = None
    color_absolute: bool = False
    color_scale: str = "linear"
    color_scientific: bool = False
    color_label: str = ""
    cmap: str = "viridis"
    marker: str = "o"
    size: float = 12.0
    opacity: float = 0.8
    show_excluded: bool = True
    excluded_opacity: float = 0.12
    title: str = ""
    grid: bool = True
    sample_limit: int | None = None
    overlays: tuple[Overlay, ...] = ()
    inset: InsetSpec | None = None


@dataclass(frozen=True)
class ScatterData:
    x: np.ndarray
    y: np.ndarray
    color: np.ndarray | None
    excluded_x: np.ndarray
    excluded_y: np.ndarray
    total: int
    selected: int
    valid: int
    excluded_valid: int
    color_range: tuple[float, float] | None

    @property
    def description(self) -> str:
        return (
            f"Selected {self.selected:,} / {self.total:,} · Usable {self.valid:,} · "
            f"Invalid for this plot {self.selected - self.valid:,} · "
            f"Drawn {len(self.x):,} + {len(self.excluded_x):,} excluded"
        )


@dataclass(frozen=True)
class HistogramSpec:
    column: str
    axis: AxisSpec = field(default_factory=AxisSpec)
    bins: int | str = "auto"
    log_bins: bool = False
    density: bool = False
    log_y: bool = False
    scientific_y: bool = False
    title: str = ""
    color: str = "#178F80"
    opacity: float = 0.85


@dataclass(frozen=True)
class HistogramData:
    counts: np.ndarray
    edges: np.ndarray
    total: int
    selected: int
    valid: int
    in_range: int

    @property
    def description(self) -> str:
        return (
            f"Selected {self.selected:,} / {self.total:,} · Usable {self.valid:,} · "
            f"In range {self.in_range:,} · Invalid {self.selected - self.valid:,} · "
            f"{len(self.counts):,} bins"
        )


def _finite(value: float, name: str) -> None:
    if not np.isfinite(value):
        raise DataError(f"{name} must be a finite number.")


def _validate_axis(spec: AxisSpec, name: str) -> None:
    if spec.scale not in ("linear", "log"):
        raise DataError(f"{name}: choose Linear or Logarithmic scale.")
    for limit in (spec.lower, spec.upper):
        if limit is not None:
            _finite(limit, f"{name} bound")
            if spec.scale == "log" and limit <= 0:
                raise DataError(f"{name} bounds must be positive for a logarithmic axis.")
    if spec.lower is not None and spec.upper is not None and spec.lower >= spec.upper:
        raise DataError(f"{name}: minimum must be smaller than maximum.")


def _opacity(value: float) -> None:
    if not np.isfinite(value) or not 0 <= value <= 1:
        raise DataError("Opacity must be between 0 and 1.")


def _sample(indices: np.ndarray, limit: int | None, seed: int) -> np.ndarray:
    if limit is not None and len(indices) > limit:
        return np.sort(np.random.default_rng(seed).choice(indices, size=limit, replace=False))
    return indices


def validate_scatter(spec: ScatterSpec) -> None:
    _validate_axis(spec.x_axis, "X axis")
    _validate_axis(spec.y_axis, "Y axis")
    _opacity(spec.opacity)
    _opacity(spec.excluded_opacity)
    if not np.isfinite(spec.size) or not 0 < spec.size <= 1000:
        raise DataError("Marker size must be greater than 0 and at most 1000 points².")
    try:
        MarkerStyle(spec.marker)
    except (ValueError, TypeError) as exc:
        raise DataError("Choose a valid marker.") from exc
    if spec.color_scale not in ("linear", "log"):
        raise DataError("Color scale must be linear or logarithmic.")
    if spec.cmap not in mpl.colormaps:
        raise DataError("Choose a valid colormap.")
    if spec.sample_limit is not None and (
        not isinstance(spec.sample_limit, int) or spec.sample_limit < 1
    ):
        raise DataError("The point limit must be a positive integer.")
    for overlay in spec.overlays:
        validate_overlay(overlay)
    if spec.inset is not None:
        inset = spec.inset
        for fraction in (inset.width, inset.height):
            if not np.isfinite(fraction) or not 0.1 <= fraction <= 0.8:
                raise DataError("Inset width and height must be between 10% and 80%.")
        _validate_axis(
            AxisSpec(inset.x_scale or spec.x_axis.scale, lower=inset.x_min, upper=inset.x_max),
            "Inset X",
        )
        _validate_axis(
            AxisSpec(inset.y_scale or spec.y_axis.scale, lower=inset.y_min, upper=inset.y_max),
            "Inset Y",
        )
        if inset.location not in ("upper right", "upper left", "lower right", "lower left"):
            raise DataError("Choose one of the four inset corners.")
        if not is_color_like(inset.edgecolor) or not 0 < inset.linewidth <= 20:
            raise DataError("Choose a valid inset outline color and width (0–20).")


def validate_overlay(item: Overlay) -> None:
    if item.kind not in (
        "Horizontal line",
        "Vertical line",
        "Slope line",
        "Segment",
        "Point",
        "Text",
    ):
        raise DataError("Choose an annotation type.")
    for name in ("x", "y", "x2", "y2", "slope", "size", "linewidth", "fontsize", "rotation"):
        _finite(getattr(item, name), name)
    _opacity(item.opacity)
    if item.size <= 0 or item.linewidth <= 0 or item.fontsize <= 0:
        raise DataError("Marker size, line width and font size must be positive.")
    if not is_color_like(item.color):
        raise DataError("Enter a Matplotlib color name or a hexadecimal color such as #C44536.")
    if item.linestyle not in ("-", "--", "-.", ":"):
        raise DataError("Choose a valid line style.")
    try:
        MarkerStyle(item.marker)
    except (ValueError, TypeError) as exc:
        raise DataError("Choose a valid annotation marker.") from exc
    if item.kind == "Text" and not item.text.strip():
        raise DataError("Enter the annotation text.")


def prepare_scatter(
    frame: pd.DataFrame,
    spec: ScatterSpec,
    mask: np.ndarray | None = None,
    *,
    cancel: threading.Event | None = None,
) -> ScatterData:
    validate_scatter(spec)
    selection = selected_mask(frame, mask)
    check_cancelled(cancel)
    x, y = numeric_array(frame, spec.x), numeric_array(frame, spec.y)
    x = np.abs(x) if spec.x_axis.absolute else x
    y = np.abs(y) if spec.y_axis.absolute else y
    xy_valid = np.isfinite(x) & np.isfinite(y)
    if spec.x_axis.scale == "log":
        xy_valid &= x > 0
    if spec.y_axis.scale == "log":
        xy_valid &= y > 0
    valid = selection & xy_valid
    color, color_range = None, None
    if spec.color:
        color = numeric_array(frame, spec.color)
        color = np.abs(color) if spec.color_absolute else color
        valid &= np.isfinite(color)
        if spec.color_scale == "log":
            valid &= color > 0
        if valid.any():
            color_range = (float(color[valid].min()), float(color[valid].max()))
    indices = np.flatnonzero(valid)
    if not len(indices):
        raise DataError(
            "No usable selected rows. Check filters, missing values, and logarithmic scales."
        )
    excluded_indices = (
        np.flatnonzero(~selection & xy_valid) if spec.show_excluded else np.array([], dtype=int)
    )
    valid_count, excluded_count = len(indices), len(excluded_indices)
    indices = _sample(indices, spec.sample_limit, 0)
    excluded_indices = _sample(excluded_indices, spec.sample_limit, 1)
    check_cancelled(cancel)
    return ScatterData(
        x[indices],
        y[indices],
        color[indices] if color is not None else None,
        x[excluded_indices],
        y[excluded_indices],
        len(frame),
        int(selection.sum()),
        valid_count,
        excluded_count,
        color_range,
    )


def _format_axis(ax, spec: AxisSpec, name: str, label: str) -> None:
    getattr(ax, f"set_{name}scale")(spec.scale)
    formatter = ScalarFormatter(useMathText=True)
    if spec.scientific and spec.scale == "linear":
        formatter.set_scientific(True)
        formatter.set_powerlimits((0, 0))
        getattr(ax, f"{name}axis").set_major_formatter(formatter)
    elif spec.scientific:
        getattr(ax, f"{name}axis").set_major_formatter(FuncFormatter(_scientific_tick))
    if spec.lower is not None or spec.upper is not None:
        lower, upper = getattr(ax, f"get_{name}lim")()
        lower = spec.lower if spec.lower is not None else lower
        upper = spec.upper if spec.upper is not None else upper
        if lower >= upper:
            raise DataError(
                f"{name.upper()} bound lies beyond the automatic range. "
                "Enter both bounds or choose a bound within the data range."
            )
        getattr(ax, f"set_{name}lim")(lower, upper)
    label = spec.label or (f"|{label}|" if spec.absolute else label)
    getattr(ax, f"set_{name}label")(label)


def _scientific_tick(value, position=None):
    if not np.isfinite(value) or value == 0:
        return "0" if value == 0 else ""
    mantissa, exponent = f"{value:.1e}".split("e")
    return rf"${mantissa}\times10^{{{int(exponent)}}}$"


def _style_axes(ax, grid: bool = True) -> None:
    ax.set_facecolor("#FFFFFF")
    ax.grid(grid, color="#DFE6EC", linewidth=0.7, alpha=0.7) if grid else ax.grid(False)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#B7C4CE")
    ax.tick_params(colors="#3A4B58", labelsize=9)


def _color_norm(data: ScatterData, spec: ScatterSpec):
    if data.color_range is None:
        return None
    low, high = data.color_range
    if low == high:
        if spec.color_scale == "log":
            low, high = low / 1.1, high * 1.1
        else:
            delta = abs(low) * 0.01 if low else 0.5
            low, high = low - delta, high + delta
    return LogNorm(low, high) if spec.color_scale == "log" else Normalize(low, high)


def _draw_points(ax, data: ScatterData, spec: ScatterSpec, norm, *, legend: bool = False):
    linewidth = 0 if MarkerStyle(spec.marker).is_filled() else 0.8
    if len(data.excluded_x):
        ax.scatter(
            data.excluded_x,
            data.excluded_y,
            c="#8D9BA5",
            s=spec.size,
            alpha=spec.excluded_opacity,
            marker=spec.marker,
            linewidths=linewidth,
            label="Excluded by filters",
            rasterized=True,
        )
    points = ax.scatter(
        data.x,
        data.y,
        c=data.color if data.color is not None else "#178F80",
        cmap=spec.cmap if data.color is not None else None,
        norm=norm,
        s=spec.size,
        alpha=spec.opacity,
        marker=spec.marker,
        linewidths=linewidth,
        label="Selected",
        rasterized=len(data.x) > 10000,
    )
    if legend and len(data.excluded_x):
        ax.legend(loc="best", frameon=False, fontsize=9)
    return points


def _draw_overlay(ax, item: Overlay) -> None:
    line = dict(
        color=item.color, alpha=item.opacity, linewidth=item.linewidth, linestyle=item.linestyle
    )
    if item.kind == "Horizontal line":
        ax.axhline(item.y, **line)
    elif item.kind == "Vertical line":
        ax.axvline(item.x, **line)
    elif item.kind == "Slope line":
        limits = np.asarray(ax.get_xlim())
        ax.plot(limits, item.slope * (limits - item.x) + item.y, **line)
    elif item.kind == "Segment":
        ax.plot([item.x, item.x2], [item.y, item.y2], **line)
    elif item.kind == "Point":
        ax.scatter(
            [item.x],
            [item.y],
            s=item.size,
            marker=item.marker,
            color=item.color,
            alpha=item.opacity,
            zorder=5,
        )
    elif item.kind == "Text":
        ax.text(
            item.x,
            item.y,
            item.text,
            fontsize=item.fontsize,
            color=item.color,
            alpha=item.opacity,
            rotation=item.rotation,
            zorder=6,
        )


def render_scatter(data: ScatterData, spec: ScatterSpec) -> Figure:
    """Render on the GUI thread (or with an Agg canvas in tests)."""
    validate_scatter(spec)
    fig = Figure(figsize=(8, 6), dpi=100, layout="constrained", facecolor="white")
    ax = fig.add_subplot(111)
    _style_axes(ax, spec.grid)
    norm = _color_norm(data, spec)
    points = _draw_points(ax, data, spec, norm, legend=True)
    _format_axis(ax, spec.x_axis, "x", spec.x)
    _format_axis(ax, spec.y_axis, "y", spec.y)
    ax.set_title(spec.title or f"{spec.y} vs {spec.x}", loc="left", fontsize=13, pad=16)
    limits = ax.get_xlim(), ax.get_ylim()
    for item in spec.overlays:
        _draw_overlay(ax, item)
    # Reference elements must not silently change the data's viewing range.
    ax.set_xlim(limits[0])
    ax.set_ylim(limits[1])
    if data.color is not None:
        colorbar = fig.colorbar(points, ax=ax, pad=0.025)
        label = spec.color_label or (f"|{spec.color}|" if spec.color_absolute else spec.color)
        colorbar.set_label(label)
        if spec.color_scientific and spec.color_scale == "linear":
            formatter = ScalarFormatter(useMathText=True)
            formatter.set_powerlimits((0, 0))
            colorbar.formatter = formatter
            colorbar.update_ticks()
        elif spec.color_scientific:
            colorbar.formatter = FuncFormatter(_scientific_tick)
            colorbar.update_ticks()
    if spec.inset:
        inset = spec.inset
        left = 0.07 if "left" in inset.location else 0.93 - inset.width
        bottom = 0.93 - inset.height if "upper" in inset.location else 0.08
        axins = ax.inset_axes([left, bottom, inset.width, inset.height])
        _style_axes(axins, spec.grid)
        _draw_points(axins, data, spec, norm)
        axins.set_xscale(inset.x_scale or spec.x_axis.scale)
        axins.set_yscale(inset.y_scale or spec.y_axis.scale)
        axins.set_xlim(inset.x_min, inset.x_max)
        axins.set_ylim(inset.y_min, inset.y_max)
        axins.tick_params(labelsize=7)
        if inset.rectangle:
            ax.add_patch(
                Rectangle(
                    (inset.x_min, inset.y_min),
                    inset.x_max - inset.x_min,
                    inset.y_max - inset.y_min,
                    fill=False,
                    edgecolor=inset.edgecolor,
                    linewidth=inset.linewidth,
                )
            )
    return fig


def _bin_edges(values: np.ndarray, spec: HistogramSpec) -> np.ndarray:
    if isinstance(spec.bins, int):
        count = spec.bins
    else:
        if spec.bins not in ("auto", "fd", "sqrt", "sturges"):
            raise DataError("Bins must be Auto, FD, Sqrt, Sturges, or an integer from 1 to 4096.")
        work = np.log10(values) if spec.log_bins else values
        scale = float(np.max(np.abs(work))) or 1.0
        work = work / scale
        span = float(work.max() - work.min())
        iqr = float(np.subtract(*np.quantile(work, [0.75, 0.25])))
        sturges = int(np.ceil(np.log2(len(values)) + 1))
        width = 2 * iqr / np.cbrt(len(values))
        fd = min(4096, int(np.ceil(min(span / width, 4096)))) if width > 0 else sturges
        count = {
            "auto": max(sturges, fd),
            "fd": fd,
            "sqrt": int(np.ceil(np.sqrt(len(values)))),
            "sturges": sturges,
        }[spec.bins]
    if not 1 <= count <= 4096:
        raise DataError("Use between 1 and 4096 bins.")
    low = spec.axis.lower if spec.axis.lower is not None else float(values.min())
    high = spec.axis.upper if spec.axis.upper is not None else float(values.max())
    if low == high:
        if spec.log_bins or spec.axis.scale == "log":
            low, high = low / 1.1, high * 1.1
        else:
            delta = max(abs(low) * 0.01, 0.5)
            low, high = low - delta, high + delta
    edges = (
        np.geomspace(low, high, count + 1) if spec.log_bins else np.linspace(low, high, count + 1)
    )
    if not np.isfinite(edges).all() or not (np.diff(edges) > 0).all():
        raise DataError(
            "These bounds cannot form distinct finite bins. Reduce the bin count or change the range."
        )
    return edges


def prepare_histogram(
    frame: pd.DataFrame,
    spec: HistogramSpec,
    mask: np.ndarray | None = None,
    *,
    cancel: threading.Event | None = None,
) -> HistogramData:
    _validate_axis(spec.axis, "Histogram X")
    _opacity(spec.opacity)
    if not is_color_like(spec.color):
        raise DataError("Choose a valid histogram color.")
    if spec.log_bins and any(v is not None and v <= 0 for v in (spec.axis.lower, spec.axis.upper)):
        raise DataError("Logarithmic bin bounds must be positive.")
    selection = selected_mask(frame, mask)
    raw = numeric_array(frame, spec.column)[selection]
    raw = np.abs(raw) if spec.axis.absolute else raw
    valid = np.isfinite(raw)
    if spec.log_bins or spec.axis.scale == "log":
        valid &= raw > 0
    values = raw[valid]
    n_valid = len(values)
    if spec.axis.lower is not None:
        values = values[values >= spec.axis.lower]
    if spec.axis.upper is not None:
        values = values[values <= spec.axis.upper]
    if not len(values):
        raise DataError(
            "No usable values in this range. Check the column, filters, bounds, and log scale."
        )
    check_cancelled(cancel)
    edges = _bin_edges(values, spec)
    counts, edges = np.histogram(values, bins=edges)
    return HistogramData(
        counts, edges, len(frame), int(selection.sum()), n_valid, int(counts.sum())
    )


def render_histogram(data: HistogramData, spec: HistogramSpec) -> Figure:
    fig = Figure(figsize=(8, 6), dpi=100, layout="constrained", facecolor="white")
    ax = fig.add_subplot(111)
    _style_axes(ax)
    heights = data.counts.astype(float)
    if spec.density:
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            heights = heights / data.in_range / np.diff(data.edges)
        if not np.isfinite(heights).all():
            raise DataError("Density exceeds numeric precision. Use counts or a wider bin range.")
    ax.stairs(
        heights,
        data.edges,
        fill=True,
        color=spec.color,
        alpha=spec.opacity,
        linewidth=1.1,
        label=f"N = {data.in_range:,}",
    )
    _format_axis(ax, spec.axis, "x", spec.column)
    _format_axis(
        ax,
        AxisSpec("log" if spec.log_y else "linear", spec.scientific_y),
        "y",
        "Probability density" if spec.density else "Count",
    )
    ax.set_title(spec.title or f"Distribution of {spec.column}", loc="left", fontsize=13, pad=16)
    ax.legend(frameon=False)
    return fig


def export_figure(
    fig: Figure, path: str | Path, *, dpi: int = 300, overwrite: bool = False
) -> Path:
    """Write the displayed figure via a temporary file in the destination directory."""
    path = Path(path).expanduser().resolve()
    extension = path.suffix.lower().lstrip(".")
    if extension not in ("png", "pdf", "svg"):
        raise DataError("Choose a .png, .pdf, or .svg filename.")
    if not isinstance(dpi, int) or not 50 <= dpi <= 1200:
        raise DataError("Export resolution must be an integer from 50 to 1200 DPI.")
    try:
        with atomic_destination(path, overwrite=overwrite) as temporary:
            fig.savefig(temporary, format=extension, dpi=dpi, bbox_inches="tight")
    except (OSError, ValueError, RuntimeError) as exc:
        raise DataError(f"Could not export the figure: {exc}") from exc
    return path
