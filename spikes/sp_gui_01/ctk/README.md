# CTk/ttk candidate — SP-GUI-01

Run from a checkout of `spike/sp-gui-01-ctk` with the production dependencies installed.

```bash
python -m spikes.sp_gui_01.common.corpus /tmp/SP-GUI-01/S-200K-mixed.csv
python -m spikes.sp_gui_01.ctk.app /tmp/SP-GUI-01/S-200K-mixed.csv \
  --metrics /tmp/SP-GUI-01/ctk-metrics.json
```

## Verification commands

```bash
# Functional-only supplied fixture
python -m spikes.sp_gui_01.ctk.app /path/to/test_sample_500.csv

# Frozen hard gates; reuse the generated corpus rather than regenerating it
SP_GUI_01_S200K=/tmp/SP-GUI-01/S-200K-mixed.csv \
  python -m unittest spikes.sp_gui_01.ctk.test_hard_gates -v

# Real-display adapter checks
python -m unittest spikes.sp_gui_01.ctk.test_ctk -v

# Dependency/version/installed-size inventory
python -m spikes.sp_gui_01.ctk.environment
```

The hard-gate file is byte-identical to the Qt candidate. GUI scenarios S01–S16,
keyboard/focus and the DPI matrix still require a real display; a skipped display
test is not a PASS.

## Table strategy

The candidate uses one `ttk.Treeview` with a fixed window of 120 reusable items.
The scrollbar maps onto the complete global Source Row ordering. Go-to operations
jump directly by Source Row ID. Global sort/search/filter work happens outside the
Tk thread; the viewport is refreshed from the returned ordering. No widget is
created per corpus row or cell.

## Common contract

The GUI delegates analytical filters to production `build_filter_mask`, dataset
commit/revision ownership to production `AppState`, scatter preparation/rendering
to production Matplotlib functions, and stale/cancel behaviour to the existing
`TaskRunner`. Search and sort use the C0 common pure helpers.

The visible visual row selection is independent of `AppState.mask`.

## Measurements

The metrics file records startup, load, first paint, scroll, direct navigation,
search, sort, filter, scatter prepare/redraw, cancel feedback, resize and area
switch events plus RSS at each sample. QA should execute the frozen 10-sample
series and S01–S16; a single interactive run is not a performance verdict.

## Known experimental constraints

- Treeview owns only a fixed visible row window. Native scrollbar semantics are
  adapted to a logical 200k-row scope rather than letting Treeview own 200k items.
- Columns outside the frozen visible set remain in the committed DataFrame but
  are not rendered during the comparison.
- Statistics is intentionally `Not implemented in spike`.
- Styling is deliberately minimal; this branch is not a proposed final UI.
