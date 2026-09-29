# Refactor and resume record

## Starting problems

The prototype coupled parsing, widget mutation and plot configuration across large
view classes. Data ownership was local to the uploader; successful replacement,
failure and cancellation could leave other tabs inconsistent. Scatter controls
existed, but plotting routines were inside a large commented string. Histogram
and statistics views were placeholders. DAT header handling could lose the first
data row. Several callbacks/widget APIs were inconsistent, and the repository
lacked packaging, dependency declarations and automated regression tests.

## Work recovered from the interrupted session

The resumed workspace already contained the `src` package, initial project
configuration, strict loader, centralized state, pure analysis functions, plot
engine, worker queue, four GUI views and example datasets. All 30 then-existing
tests passed. No incomplete file write was found. GUI files were implemented but
not runtime-verified; documentation, release packaging and large-file integration
checks had not been finished. These architectural decisions were retained.

## Completed during resumption

- Fixed `Application.state` shadowing Tk's window-state method by using
  `Application.app_state` throughout the coordinator and views.
- Fixed DAT handling of apostrophes, literal backslashes, quoted comment symbols,
  escaped quotes in headers and empty quoted fields.
- Distinguished physical blank CSV lines from quoted empty/whitespace records.
- Added delayed-dialog activation cleanup and cancellation of pending TkAgg
  draws/event loops when a figure is replaced or invalidated.
- Removed the redundant Matplotlib toolbar save action in favor of one explicit
  format/DPI export workflow.
- Isolated worker callback failures and made shutdown idempotent.
- Removed unnecessary preview index allocation and repeated categorical row-index
  preparation.
- Centralized atomic figure/table export and recorded `ddof` in numeric output.
- Prevented partial axis bounds from silently reversing an axis, preserved scale
  for tiny constant color values, and rejected unrepresentable histogram density.
- Added coordinator, export, worker and scientific regression tests, plus desktop
  integration tests that skip explicitly when no display is available.
- Ran the supplied 200,000 × 61 dataset through import, filters, statistics,
  correlations, full scatter rendering, histogram rendering and image export.
- Added installation/development documentation, exact tested dependency
  constraints, a benchmark tool and buildable source/wheel distributions.

## File replacement map

| Prototype file | Replacement |
| --- | --- |
| `main.py`, `gui.py` | `src/csv_analyzer/app.py`, `__main__.py`, `ui/main_window.py` |
| `settings.py` | `config.py` and typed immutable plot/import/filter specifications |
| `csv_upload_frame.py` | `data.py`, `state.py`, `jobs.py`, `ui/data_tab.py`, import/filter dialogs |
| `scatter_plots_frame.py` | `plots.py`, `ui/relationships_tab.py`, shared controls and focused editors |
| `histograms_frame.py` | Real histogram preparation/rendering plus `ui/distributions_tab.py` |
| `statistics_frame.py` | `analysis.py`, `exports.py`, `ui/statistics_tab.py` |

Additional new files include `pyproject.toml`, dependency constraints, `MANIFEST.in`,
README, architecture/validation/change notes, tests, examples and the benchmark.
The original brief is preserved as `PROJECT_BRIEF.md`. The deliverable contains
the new package only; obsolete prototype modules and their commented routines are
not part of its source tree or installed distributions. Original uploaded source
attachments and the large test input were left intact outside this project.

## Scope

All four required functional areas now have implementations. Scientific scatter
capabilities were rebuilt: independent transforms/scales/formatting, color mapping,
bounds, structured annotations, insets, filtered/excluded points and export.
Histograms and statistics are new working numerical implementations. Optional
chart families, regression fitting and session persistence were not added; they
are not needed to complete the requested CSV-analysis workflow.

Desktop runtime and visual verification remain explicitly open, as described in
`VALIDATION.md`; implementation and headless verification are not presented as a
substitute for that check.
