"""Strict, cancellable text import with explicit parsing options.

Input is structurally scanned before pandas parses it. The extra sequential
read prevents silent header/index shifts and incomplete records without holding
a second copy of the file in memory.
"""

from __future__ import annotations

import csv
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype


class DataError(ValueError):
    """An import or analysis request cannot be satisfied with these data."""


class CancelledError(Exception):
    """A background operation was cancelled."""


@dataclass(frozen=True)
class ImportOptions:
    delimiter: str = "auto"  # auto, whitespace, or a single character
    encoding: str = "utf-8-sig"
    header: str = "auto"  # auto, yes, no; CSV auto means yes
    decimal: str = "."
    comment: str | None = None
    date_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    dtype: str
    kind: str
    missing: int
    infinite: int


@dataclass(frozen=True)
class Dataset:
    """A committed frame is treated as read-only by every consumer."""

    frame: pd.DataFrame
    path: Path
    columns: tuple[ColumnInfo, ...]
    notices: tuple[str, ...] = ()
    delimiter: str = ","
    encoding: str = "utf-8-sig"
    file_size: int = 0
    memory_bytes: int = 0

    @property
    def numeric_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.kind == "numeric")

    @property
    def missing_count(self) -> int:
        return sum(c.missing for c in self.columns)

    @property
    def infinite_count(self) -> int:
        return sum(c.infinite for c in self.columns)


def check_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def describe_columns(frame: pd.DataFrame) -> tuple[ColumnInfo, ...]:
    columns = []
    for name, series in frame.items():
        kind = (
            "boolean"
            if is_bool_dtype(series.dtype)
            else "numeric"
            if is_numeric_dtype(series.dtype)
            else "datetime"
            if is_datetime64_any_dtype(series.dtype)
            else "text"
        )
        infinite = 0
        if kind == "numeric":
            infinite = int(np.isinf(series.to_numpy(dtype=float, na_value=np.nan)).sum())
        columns.append(
            ColumnInfo(str(name), str(series.dtype), kind, int(series.isna().sum()), infinite)
        )
    return tuple(columns)


def _unique_headers(raw: list[str]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    notices = []
    reserved = {s for s in raw if s.strip()}
    used: set[str] = set()
    for i, original in enumerate(raw):
        base = original if original.strip() else f"Column_{i + 1}"
        name = base
        suffix = 2
        while (
            name in used
            or (not original.strip() and name in reserved)
            or (name != base and name in reserved)
        ):
            name = f"{base} [{suffix}]"
            suffix += 1
        if name != original:
            notices.append(f"Column {i + 1}: {original!r} was named {name!r}.")
        used.add(name)
        names.append(name)
    return names, notices


def _choose_delimiter(path: Path, sample: str, options: ImportOptions) -> str:
    if options.delimiter != "auto":
        delimiter = options.delimiter
    elif path.suffix.lower() == ".dat":
        delimiter = "whitespace"
    else:
        # Use several complete physical lines; never infer from a truncated last line.
        lines = sample.splitlines()
        candidates = [line for line in lines[:30] if line.strip()]
        try:
            delimiter = csv.Sniffer().sniff("\n".join(candidates), delimiters=",;\t|").delimiter
        except csv.Error:
            first = candidates[0] if candidates else ""
            counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
            delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    if delimiter != "whitespace" and (len(delimiter) != 1 or delimiter in '\r\n"'):
        raise DataError("Choose a single delimiter character or Whitespace.")
    if options.decimal not in (".", ","):
        raise DataError("The decimal separator must be a period or comma.")
    if options.decimal == delimiter:
        raise DataError("The field delimiter and decimal separator must be different.")
    return delimiter


def _dat_fields(line: str, comment: str | None) -> list[str]:
    """Tokenize a single DAT record using CSV double-quote semantics.

    Apostrophes and backslashes are literal. Double quotes inside a quoted
    field are doubled. Multiline fields belong in delimited CSV imports.
    """
    fields, field = [], []
    started = quoted = False
    i = 0
    while i < len(line):
        char = line[i]
        if quoted:
            if char == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    field.append('"')
                    i += 1
                else:
                    quoted = False
            else:
                field.append(char)
        elif char == comment:
            break
        elif char.isspace():
            if started:
                fields.append("".join(field))
                field = []
                started = False
        elif char == '"' and not started:
            quoted = started = True
        else:
            field.append(char)
            started = True
        i += 1
    if quoted:
        raise DataError("Unclosed quote in DAT record. Use CSV for quoted multiline fields.")
    if started:
        fields.append("".join(field))
    return fields


class _RecordLines:
    """Track physical blank lines without dropping quoted empty CSV records."""

    def __init__(self, stream):
        self.stream = stream
        self.has_content = False

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.stream)
        self.has_content |= bool(line.strip())
        return line


def load_dataset(
    path: str | Path,
    options: ImportOptions | None = None,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> Dataset:
    """Return a fully validated dataset; never modify application state.

    Whitespace DAT supports a first commented header (``# x y``), an ordinary
    header with ``header='yes'``, or generated names with ``header='no'``.
    Arbitrary comments in delimited CSV are deliberately not inferred.
    """
    options = options or ImportOptions()
    path = Path(path).expanduser().resolve()
    if options.header not in ("auto", "yes", "no"):
        raise DataError("Header must be Auto, Yes, or No.")
    if options.comment is not None and len(options.comment) != 1:
        raise DataError("Use a single comment character.")
    try:
        before = path.stat()
        check_cancelled(cancel)
        with path.open(encoding=options.encoding, newline="") as stream:
            sample = stream.read(65536)
        if not sample.strip():
            raise DataError("This file is empty. Choose a file containing a header or data rows.")
        delimiter = _choose_delimiter(path, sample, options)
        whitespace = delimiter == "whitespace"
        comment = options.comment or ("#" if whitespace else None)
        if comment and not whitespace:
            raise DataError("Comment markers are supported for whitespace DAT imports only.")
        if progress:
            progress("Checking record widths…")
        names_raw: list[str] | None = None
        expected = None
        rows = 0
        skiprows: list[int] = []
        ordinary_header = options.header == "yes" or (options.header == "auto" and not whitespace)
        commented_header = False
        first_nonblank = True
        with path.open(encoding=options.encoding, newline="") as stream:
            if whitespace:
                for line_num, line in enumerate(stream, 1):
                    if line_num % 2048 == 0:
                        check_cancelled(cancel)
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if comment and stripped.startswith(comment):
                        if first_nonblank and options.header == "auto":
                            names_raw = _dat_fields(stripped[1:].strip(), None)
                            expected = len(names_raw)
                            commented_header = True
                            ordinary_header = False
                        first_nonblank = False
                        continue
                    first_nonblank = False
                    # Match pandas' whitespace parser; comments end an unquoted record.
                    fields = _dat_fields(line, comment)
                    if not fields:
                        continue
                    if expected is None:
                        expected = len(fields)
                    if len(fields) != expected:
                        raise DataError(
                            f"Line {line_num} has {len(fields)} fields; expected {expected}."
                        )
                    if names_raw is None and ordinary_header:
                        names_raw = fields
                        skiprows.append(line_num - 1)
                    else:
                        rows += 1
            else:
                lines = _RecordLines(stream)
                reader = csv.reader(lines, delimiter=delimiter, strict=True)
                header_end = 0
                record_num = 0
                while True:
                    lines.has_content = False
                    fields = next(reader, None)
                    if fields is None:
                        break
                    record_num += 1
                    if record_num % 2048 == 0:
                        check_cancelled(cancel)
                    if not lines.has_content:
                        continue
                    if expected is None:
                        expected = len(fields)
                        if ordinary_header:
                            names_raw = fields
                            header_end = reader.line_num
                            continue
                    if len(fields) != expected:
                        raise DataError(
                            f"Record ending on line {reader.line_num} has {len(fields)} fields; "
                            f"expected {expected}. Check the delimiter or repair the record."
                        )
                    rows += 1
                if ordinary_header:
                    skiprows = list(range(header_end))
        if not expected:
            raise DataError("No columns were found in this file.")
        if names_raw is None:
            names_raw = [f"Column_{i + 1}" for i in range(expected)]
        names, notices = _unique_headers(names_raw)
        check_cancelled(cancel)
        if progress:
            progress("Reading values…")
        if rows == 0:
            frame = pd.DataFrame(columns=names)
            notices.append("The file contains column names but no data rows.")
        else:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", pd.errors.DtypeWarning)
                frame = pd.read_csv(
                    path,
                    sep=r"\s+" if whitespace else delimiter,
                    encoding=options.encoding,
                    header=None,
                    names=names,
                    skiprows=skiprows or None,
                    comment=comment,
                    decimal=options.decimal,
                    index_col=False,
                    on_bad_lines="error",
                    float_precision="round_trip",
                    low_memory=False,
                )
            notices.extend(str(item.message) for item in captured)
        if len(frame) != rows:
            raise DataError(
                "The parser and record scan disagree. Check quoting or comment settings."
            )
        for column in options.date_columns:
            if column not in frame:
                raise DataError(f"Date column {column!r} was not found.")
            try:
                frame[column] = pd.to_datetime(frame[column], errors="raise", format="ISO8601")
            except (ValueError, TypeError) as exc:
                raise DataError(f"Column {column!r} contains invalid ISO dates.") from exc
        check_cancelled(cancel)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise DataError("The file changed while it was being read. Please load it again.")
        if whitespace and commented_header:
            notices.append(
                "Used the first commented line as column names; preserved every data row."
            )
        columns = describe_columns(frame)
        if any(c.missing for c in columns):
            notices.append("Missing values are retained. Each analysis reports its usable rows.")
        if any(c.infinite for c in columns):
            notices.append("Infinite values are retained but excluded from numeric analyses.")
        check_cancelled(cancel)
        return Dataset(
            frame,
            path,
            columns,
            tuple(notices),
            delimiter,
            options.encoding,
            before.st_size,
            int(frame.memory_usage(index=True, deep=True).sum()),
        )
    except (CancelledError, DataError):
        raise
    except UnicodeError as exc:
        raise DataError(
            f"Cannot decode this file as {options.encoding}. Select its encoding and try again."
        ) from exc
    except (OSError, csv.Error, pd.errors.ParserError, ValueError, LookupError) as exc:
        raise DataError(f"Could not read {path.name}: {exc}") from exc
