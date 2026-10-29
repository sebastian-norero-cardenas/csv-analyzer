# F3.A — Qt Foundation

F3.A introduces an independent PySide6 bootstrap without replacing the existing
CustomTkinter application. The canonical `varia-studio` and compatibility
`csv-analyzer` commands still start CTk; `varia-studio-qt` and
`python -m csv_analyzer.qt_app` start the minimal Qt foundation.

## Ownership

- `AppState` remains the single committed dataset store.
- `QtCoordinator` owns publication policy and lifecycle.
- `QtTaskRunner` owns one private `QThreadPool` with two worker threads.
- The runner queue is application-managed and bounded to eight pending requests.
- Workers receive only a callable, cancellation Event and immutable revision
  metadata. They never receive live `AppState`, widgets or view callbacks.
- The Qt shell is intentionally minimal; table, charts and statistics remain
  outside F3.A.

A load is exclusive with other physical loads. Replacing a logical LOAD sets the
old cancellation Event immediately but waits for that physical worker to finish
before starting another LOAD. Other work may use the second worker.

Closing follows STARTING -> RUNNING -> CLOSING -> CLOSED. CLOSING invalidates
publication, stops accepting work and requests cancellation, while keeping the
pool, bridge and runnable objects alive until physical activity reaches zero. No
indefinite wait is performed on the GUI thread.

## Revision domains

`AppState.revision` is preserved for CTk compatibility.

- Dataset commit: revision +1, dataset_revision +1, selection_revision +1.
- Filter commit: revision +1, selection_revision +1.
- Failed, cancelled or stale load: no revision changes.

Toolkit-neutral `StateSnapshot` instances carry stable committed references and
revision tokens. Qt publication checks application epoch, key/generation,
dataset revision, selection revision, load ticket and lifecycle before invoking
a GUI-thread callback.

## Optional PySide6 policy

PySide6 is a transitional **optional dependency** while CTk and Qt coexist.

A base installation:

```bash
python -m pip install .
```

installs CTk/core dependencies but does not require PySide6.

The Qt route is enabled explicitly:

```bash
python -m pip install '.[qt]'
```

Development of the Qt route uses both extras:

```bash
python -m pip install '.[dev,qt]'
```

The `qt` extra declares `PySide6>=6.11.3,<6.13`. This avoids making the
experimental spike's exact 6.12.0 pin the project dependency policy while
keeping F3.A inside the current Qt 6.11/6.12 compatibility band.

The CPython 3.12 constraints file pins PySide6 6.12.0 and its split wheels only
for reproducible environments that actually install the `qt` extra. Those
constraint entries do not turn PySide6 into a base dependency.

Importing `csv_analyzer`, core modules, `csv_analyzer.qt_app`, or the Qt
bootstrap does not import PySide6 eagerly. Without the `qt` extra:

- `varia-studio-qt --help` and `python -m csv_analyzer.qt_app --help` work,
  because argument parsing does not require Qt;
- attempting to start the Qt UI exits non-zero with an actionable stderr
  message telling the user to install the `qt` extra, without a normal raw
  traceback.

Matplotlib's QtAgg backend is compatible with PySide6, although QtAgg
integration itself is not part of F3.A.

This is transitional packaging, not the final release packaging decision.
