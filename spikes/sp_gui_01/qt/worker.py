"""Generation-checked QThreadPool runner for the Qt spike."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from csv_analyzer.data import CancelledError


class _Signals(QObject):
    done = Signal(str, int, object, object)


class _Runnable(QRunnable):
    def __init__(self, key: str, generation: int, cancel: threading.Event, work):
        super().__init__()
        self.key = key
        self.generation = generation
        self.cancel = cancel
        self.work = work
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            value, error = self.work(self.cancel), None
        except Exception as exc:
            value, error = None, exc
        self.signals.done.emit(self.key, self.generation, value, error)


@dataclass
class _Job:
    generation: int
    cancel: threading.Event
    success: Callable[[Any], None]
    error: Callable[[Exception], None]
    runnable: _Runnable


class QtTaskRunner(QObject):
    """Drop stale results exactly like the production TaskRunner contract."""

    activity_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.jobs: dict[str, _Job] = {}
        self.generation = 0
        self.closed = False

    def submit(self, key: str, work, success, error) -> None:
        if self.closed:
            return
        self.cancel(key)
        self.generation += 1
        cancel = threading.Event()
        runnable = _Runnable(key, self.generation, cancel, work)
        job = _Job(self.generation, cancel, success, error, runnable)
        self.jobs[key] = job
        runnable.signals.done.connect(self._result)
        self.activity_changed.emit(True)
        self.pool.start(runnable)

    def cancel(self, key: str) -> None:
        job = self.jobs.pop(key, None)
        if job is not None:
            job.cancel.set()
        self.activity_changed.emit(bool(self.jobs))

    def cancel_all(self) -> None:
        for key in tuple(self.jobs):
            self.cancel(key)

    @Slot(str, int, object, object)
    def _result(self, key: str, generation: int, value, error) -> None:
        if self.closed:
            return
        job = self.jobs.get(key)
        if job is None or job.generation != generation:
            return
        del self.jobs[key]
        if job.cancel.is_set() or isinstance(error, CancelledError):
            self.activity_changed.emit(bool(self.jobs))
            return
        try:
            if error is None:
                job.success(value)
            else:
                job.error(error)
        finally:
            self.activity_changed.emit(bool(self.jobs))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel_all()
