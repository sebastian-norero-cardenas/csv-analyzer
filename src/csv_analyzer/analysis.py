"""Numerical analysis independent of Tk and Matplotlib."""

from __future__ import annotations

import math
import threading
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pandas as pd
from pandas.api.types import (
    is_bool_dtype,
    is_datetime64_any_dtype,
    is_integer_dtype,
    is_numeric_dtype,
)

from .data import DataError, check_cancelled
from .state import FilterRule


def _finite_filter_bound(bound) -> bool:
    if isinstance(bound, Decimal):
        return bound.is_finite()
    if isinstance(bound, (bool, np.bool_)):
        return False
    if isinstance(bound, (int, np.integer)):
        return True
    if isinstance(bound, (float, np.floating)):
        return math.isfinite(float(bound))
    return False


def _float_filter_bound(bound) -> float:
    try:
        return float(bound)
    except OverflowError:
        return math.inf if bound > 0 else -math.inf


def numeric_array(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame:
        raise DataError(f"Column {column!r} is not in the current dataset.")
    series = frame[column]
    if not is_numeric_dtype(series.dtype) or is_bool_dtype(series.dtype):
        raise DataError(f"Column {column!r} is not numeric. Choose a numeric column.")
    return series.to_numpy(dtype=float, na_value=np.nan)


def selected_mask(frame: pd.DataFrame, mask: np.ndarray | None) -> np.ndarray:
    if mask is None:
        return np.ones(len(frame), dtype=bool)
    if mask.shape != (len(frame),) or mask.dtype != np.bool_:
        raise DataError("The row selection does not match this dataset.")
    return mask


def build_filter_mask(
    frame: pd.DataFrame,
    rules: tuple[FilterRule, ...],
    cancel: threading.Event | None = None,
) -> np.ndarray:
    """AND inclusive bounds on raw values; nonfinite operands fail their rule."""
    mask = np.ones(len(frame), dtype=bool)
    for rule in rules:
        check_cancelled(cancel)
        if rule.column not in frame:
            raise DataError(f"Column {rule.column!r} is not in the current dataset.")
        series = frame[rule.column]
        if not is_numeric_dtype(series.dtype) or is_bool_dtype(series.dtype):
            raise DataError(f"Column {rule.column!r} is not numeric. Choose a numeric column.")
        if rule.lower is None and rule.upper is None:
            raise DataError("A filter needs at least one bound.")
        if not all(
            bound is None or _finite_filter_bound(bound)
            for bound in (rule.lower, rule.upper)
        ):
            raise DataError("Filter bounds must be finite numbers.")
        try:
            reversed_bounds = (
                rule.lower is not None
                and rule.upper is not None
                and rule.lower > rule.upper
            )
        except (TypeError, ValueError):
            raise DataError("Filter bounds must be finite numbers.") from None
        if reversed_bounds:
            raise DataError("The lower filter bound must not exceed the upper bound.")

        if is_integer_dtype(series.dtype):
            rule_mask = series.notna().to_numpy(dtype=bool)
            if rule.lower is not None:
                lower = math.ceil(rule.lower)
                rule_mask &= series.ge(lower).fillna(False).to_numpy(dtype=bool)
            if rule.upper is not None:
                upper = math.floor(rule.upper)
                rule_mask &= series.le(upper).fillna(False).to_numpy(dtype=bool)
            mask &= rule_mask
            continue

        values = numeric_array(frame, rule.column)
        mask &= np.isfinite(values)
        if rule.lower is not None:
            mask &= values >= _float_filter_bound(rule.lower)
        if rule.upper is not None:
            mask &= values <= _float_filter_bound(rule.upper)
    return mask


def _linear_interpolate(a: float, b: float, numerator: int) -> float:
    if numerator == 0 or a == b:
        return float(a)
    left = Fraction.from_float(float(a))
    right = Fraction.from_float(float(b))
    return float(((4 - numerator) * left + numerator * right) / 4)


def _linear_quartiles(values: np.ndarray) -> tuple[float, float, float]:
    """Q1/median/Q3 using exact quartile positions and one final float rounding."""
    n = len(values)
    positions = []
    indices = []
    for numerator in (1, 2, 3):
        lower, remainder = divmod((n - 1) * numerator, 4)
        upper = lower if remainder == 0 else lower + 1
        positions.append((lower, upper, remainder))
        indices.extend((lower, upper))
    partitioned = np.partition(values, np.unique(indices))
    return tuple(
        _linear_interpolate(partitioned[lower], partitioned[upper], numerator)
        for lower, upper, numerator in positions
    )


def numeric_summary(
    frame: pd.DataFrame,
    mask: np.ndarray | None = None,
    *,
    ddof: int = 1,
    cancel: threading.Event | None = None,
) -> pd.DataFrame:
    """Finite values only; linear quantiles; ddof=1 for sample, 0 for population."""
    if ddof not in (0, 1):
        raise DataError("Choose sample (ddof=1) or population (ddof=0) statistics.")
    selection = selected_mask(frame, mask)
    rows = []
    for column in frame:
        check_cancelled(cancel)
        series = frame[column]
        if not is_numeric_dtype(series.dtype) or is_bool_dtype(series.dtype):
            continue
        raw = numeric_array(frame, column)[selection]
        values = raw[np.isfinite(raw)]
        n = len(values)
        result = {
            "Column": column,
            "Rows": len(raw),
            "Valid": n,
            "Missing": int(np.isnan(raw).sum()),
            "Infinite": int(np.isinf(raw).sum()),
            "Mean": np.nan,
            "Std": np.nan,
            "Variance": np.nan,
            "Min": np.nan,
            "Q1": np.nan,
            "Median": np.nan,
            "Q3": np.nan,
            "Max": np.nan,
            "ddof": ddof,
        }
        if n:
            q1, median, q3 = _linear_quartiles(values)
            # Scaling avoids spurious intermediate overflow in mean/std.
            scale = float(np.max(np.abs(values))) or 1.0
            scaled = values / scale
            with np.errstate(over="ignore", invalid="ignore"):
                std = float(np.std(scaled, ddof=ddof) * scale) if n > ddof else np.nan
                variance = float(np.square(std))
            result.update(
                Mean=float(np.mean(scaled) * scale),
                Std=std,
                Variance=variance,
                Min=float(values.min()),
                Q1=q1,
                Median=median,
                Q3=q3,
                Max=float(values.max()),
            )
        rows.append(result)
    return pd.DataFrame(
        rows,
        columns=[
            "Column",
            "Rows",
            "Valid",
            "Missing",
            "Infinite",
            "Mean",
            "Std",
            "Variance",
            "Min",
            "Q1",
            "Median",
            "Q3",
            "Max",
            "ddof",
        ],
    )


def categorical_summary(
    frame: pd.DataFrame,
    mask: np.ndarray | None = None,
    *,
    cancel: threading.Event | None = None,
) -> pd.DataFrame:
    """Non-numeric columns, including boolean/date columns; missing is not a category."""
    selection = selected_mask(frame, mask)
    indices = np.flatnonzero(selection)
    rows = []
    for column in frame:
        check_cancelled(cancel)
        series = frame[column]
        if is_numeric_dtype(series.dtype) and not is_bool_dtype(series.dtype):
            continue
        series = series.iloc[indices]
        valid = series.dropna()
        counts = valid.value_counts(sort=True)
        maximum = int(counts.iloc[0]) if len(counts) else 0
        modes = counts[counts == maximum]
        dates = is_datetime64_any_dtype(series.dtype)
        rows.append(
            {
                "Column": column,
                "Rows": len(series),
                "Valid": len(valid),
                "Missing": int(series.isna().sum()),
                "Unique": int(valid.nunique()),
                "Most frequent": str(counts.index[0]) if len(counts) else "",
                "Frequency": maximum,
                "Tied modes": len(modes),
                "Earliest": str(valid.min()) if dates and len(valid) else "",
                "Latest": str(valid.max()) if dates and len(valid) else "",
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Column",
            "Rows",
            "Valid",
            "Missing",
            "Unique",
            "Most frequent",
            "Frequency",
            "Tied modes",
            "Earliest",
            "Latest",
        ],
    )


def correlation_summary(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    mask: np.ndarray | None = None,
    *,
    cancel: threading.Event | None = None,
) -> pd.DataFrame:
    """Pearson r with pairwise finite observations and explicit sample counts."""
    if len(columns) < 2:
        raise DataError("Choose at least two numeric columns for correlations.")
    if len(set(columns)) != len(columns):
        raise DataError("Choose distinct correlation columns.")
    selection = selected_mask(frame, mask)
    arrays = {column: numeric_array(frame, column) for column in columns}
    rows = []
    for i, x in enumerate(columns):
        for y in columns[i + 1 :]:
            check_cancelled(cancel)
            a, b = arrays[x], arrays[y]
            valid = selection & np.isfinite(a) & np.isfinite(b)
            av, bv = a[valid], b[valid]
            n = len(av)
            r, note = np.nan, ""
            if n < 2:
                note = "Fewer than two paired observations"
            elif av.min() == av.max() or bv.min() == bv.max():
                note = "Constant column"
            else:
                a_scaled = av / max(float(np.max(np.abs(av))), 1e-300)
                b_scaled = bv / max(float(np.max(np.abs(bv))), 1e-300)
                r = float(np.clip(np.corrcoef(a_scaled, b_scaled)[0, 1], -1, 1))
            rows.append({"X": x, "Y": y, "Paired N": n, "Pearson r": r, "Note": note})
    return pd.DataFrame(rows)
