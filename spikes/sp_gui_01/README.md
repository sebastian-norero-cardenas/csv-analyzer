# SP-GUI-01 common experimental base

This directory is disposable experimental infrastructure. It is **not** production and must never be merged to `main`.

`SP-GUI-01-C0` freezes the common inputs for the CTk/ttk and PySide6/Qt candidates. Both toolkit branches must be created directly from the exact C0 commit and must not edit `manifest.json`, common corpus generation, or common semantic oracles after the first candidate begins.

## Frozen authorities

- F2-01: retain pandas, NumPy, Matplotlib and the existing dataset/AppState/revision/cancellation contracts; toolkit comparison is a spike, not a domain rewrite.
- F2-02A FROZEN: AC-01…AC-19 are hard functional gates.
- F2-02B-R1 FROZEN: S01–S16 and the common shell/content/interaction contract govern both candidates.

## Corpus

### F-500
Use the supplied `test_sample_500.csv`. The experiment verifies its SHA-256 before use. It is functional data only and **must not** be used to claim performance.

### S-200K-mixed
Generate it outside Git:

```bash
python -m spikes.sp_gui_01.common.corpus /tmp/SP-GUI-01/S-200K-mixed.csv
```

The generator aborts if the resulting SHA differs from the value frozen in `manifest.json`. The file is synthetic and representative only; it is not the unavailable historical ~200k production dataset.

## Common tests

```bash
python -m unittest spikes.sp_gui_01.tests.test_common_contract -v
```

These tests verify toolkit-neutral semantics only. They do not substitute for GUI interaction tests required by S01–S16.

## Candidate rules

- CTk/ttk and Qt branches start from exactly C0.
- Same dataset, manifest, visible columns, filter, search queries, sort, Source Row IDs, X/Y, sampling policy and measurement events.
- No toolkit may modify analytical selection through table selection, sort, search, scope, copy or navigation.
- Statistics is present only as `Not implemented in spike`.
- PASS/FAIL/NOT TESTED must distinguish logic checks from actual GUI interaction.
