# PySide6 / Qt Widgets candidate — SP-GUI-01

This branch is disposable. PySide6 is confined to this spike and is **not** added
to the production distribution.

```bash
python -m pip install -r spikes/sp_gui_01/qt/requirements.txt
python -m spikes.sp_gui_01.common.corpus /tmp/SP-GUI-01/S-200K-mixed.csv
python -m spikes.sp_gui_01.qt.app /tmp/SP-GUI-01/S-200K-mixed.csv \
  --metrics /tmp/SP-GUI-01/qt-metrics.json
```

## Table strategy

The table is a native `QTableView` backed by `QAbstractTableModel`. The model
exposes the complete active-scope row count and translates requested visible
indexes to stable Source Row IDs. Qt requests only cells needed for painting; no
per-row or per-cell widgets are created.

## Common contract

The candidate uses the same C0 `SpikeModel`, sort/search oracles, production
`build_filter_mask`, `AppState`, `load_dataset`, `prepare_scatter` and
`render_scatter` as CTk. A small QThreadPool adapter preserves the same
generation/cancel/stale-result publication rule without moving domain logic into
Qt.

Matplotlib uses `FigureCanvasQTAgg` and `NavigationToolbar2QT`.

## Measurements

The metrics file records the same event names as CTk: startup, load, first paint,
scroll, direct navigation, search, sort, filter, scatter prepare/redraw, cancel
feedback, resize and area switch, plus RSS at each sample. QA owns the frozen
10-sample and AB/BA execution series.

## Experimental dependency

`PySide6==6.12.0` is pinned only here to make the spike environment reproducible.
It must not be propagated to `pyproject.toml` unless a later architecture
decision explicitly authorizes migration.

## Known experimental constraints

- Statistics is intentionally `Not implemented in spike`.
- The QTableView path uses Qt's native model/view virtualization rather than a
  custom viewport window.
- Styling is intentionally minimal and aligned to the CTk functional shell; this
  is not a proposed final visual design.
