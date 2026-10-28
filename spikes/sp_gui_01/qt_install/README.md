# SP-GUI-01-QT-PKG-01

Packaging-only correction derived directly from the frozen Qt candidate
`9d54772c295e794747e60ebd50b582b6a393a0d4`.

It does **not** modify production metadata and does not define the official
Varia Studio distribution. It builds a disposable wheel named
`varia-studio-sp-gui-01-qt` containing only the production core modules needed
by the Qt spike plus the frozen common/Qt spike packages.

PySide6 is pinned exactly to **6.12.0** in wheel metadata. Ubuntu runtime
libraries used by the verification environment are frozen separately in
`ubuntu-runtime-packages.txt` and embedded in the wheel metadata directory.

The builder rejects a branch whose direct parent is not the frozen Qt candidate,
and rejects any diff outside this directory plus the focal CI workflow.

## Build

```bash
python spikes/sp_gui_01/qt_install/build_wheel.py --output dist
```

## Clean install

```bash
python -m venv /tmp/varia-qt-clean
/tmp/varia-qt-clean/bin/pip install \
  dist/varia_studio_sp_gui_01_qt-0.1.0-py3-none-any.whl
```

Outside the repository and with no `PYTHONPATH`:

```bash
cd /tmp
env -u PYTHONPATH \
  /tmp/varia-qt-clean/bin/python -m spikes.sp_gui_01.qt.app --help
```

The focal GitHub Actions job verifies the wheel on Ubuntu 22.04 / Python 3.10,
including clean installation, metadata, an actual Qt top-level window under
Xvfb and a 500x61 packaging-only load probe. That probe is **not** F-500 and
must not be reported as such.

The same job also publishes a Python 3.13 wheelhouse. It exists only to allow an
offline verification environment to install the exact candidate wheel and its
dependencies without access to the source checkout. `smoke_installed.py` can
then be run with the frozen F-500; its default expected SHA is:

`947eb8dc651a83df4c1edabb7f1c6a21a01680939fd7135613dba264b35df1ed`.
