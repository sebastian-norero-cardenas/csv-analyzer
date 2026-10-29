"""Qt application coordinator, publication gates and lifecycle ownership."""

from __future__ import annotations

import logging
import threading
import weakref
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

import shiboken6
from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog

from ..data import CancelledError, Dataset, ImportOptions, load_dataset
from ..state import AppState
from .errors import user_error_text
from .shell import QtShell
from .tasks import (
    QtTaskRunner,
    RunnerActivity,
    TaskOutcome,
    TaskQueueFull,
    TaskRunnerClosed,
    TaskStatus,
    TaskToken,
)

logger = logging.getLogger(__name__)


class Lifecycle(Enum):
    STARTING = auto()
    RUNNING = auto()
    CLOSING = auto()
    CLOSED = auto()


@dataclass
class _Publication:
    token: TaskToken
    success: Callable[[Any], None] | None
    failure: Callable[[BaseException], None] | None
    receiver: weakref.ReferenceType[QObject] | None


class QtCoordinator(QObject):
    """Own state, runner and all GUI-thread publication decisions."""

    def __init__(
        self,
        shell: QtShell,
        *,
        loader: Callable[..., Dataset] = load_dataset,
        max_workers: int = 2,
        max_pending: int = 8,
    ) -> None:
        super().__init__()
        self.state = AppState()
        self.import_options = ImportOptions()
        self.lifecycle = Lifecycle.STARTING
        self.application_epoch = 0
        self.gui_thread_id = threading.get_ident()
        self._shell_ref = weakref.ref(shell)
        self._loader = loader
        self._publications: dict[str, _Publication] = {}

        self.runner = QtTaskRunner(
            self,
            max_workers=max_workers,
            max_pending=max_pending,
        )
        self.runner.outcome_ready.connect(self._on_outcome)
        self.runner.activity_changed.connect(self._on_activity)
        self.runner.physical_idle.connect(self._on_physical_idle)

        shell.open_requested.connect(self.open_file)
        shell.cancel_requested.connect(self.cancel_all)
        shell.close_requested.connect(self.begin_close)
        self.state.subscribe(self._on_state_changed)

    def start(self) -> None:
        if self.lifecycle is not Lifecycle.STARTING:
            return
        self.lifecycle = Lifecycle.RUNNING
        shell = self._shell()
        if shell is not None:
            shell.set_status("Ready")

    def snapshot(self):
        return self.state.snapshot()

    def submit_task(
        self,
        key: str,
        work: Callable,
        *,
        success: Callable[[Any], None] | None = None,
        failure: Callable[[BaseException], None] | None = None,
        receiver: QObject | None = None,
        load_ticket: int | None = None,
        exclusive_group: str | None = None,
    ) -> TaskToken | None:
        if self.lifecycle is not Lifecycle.RUNNING:
            return None
        snapshot = self.state.snapshot()
        try:
            token = self.runner.submit(
                key,
                work,
                application_epoch=self.application_epoch,
                dataset_revision=snapshot.dataset_revision,
                selection_revision=snapshot.selection_revision,
                load_ticket=load_ticket,
                exclusive_group=exclusive_group,
            )
        except (TaskRunnerClosed, TaskQueueFull) as error:
            self._present_error(error)
            return None

        target = weakref.ref(receiver) if receiver is not None else None
        self._publications[key] = _Publication(token, success, failure, target)
        return token

    @Slot()
    def open_file(self) -> None:
        if self.lifecycle is not Lifecycle.RUNNING:
            return
        shell = self._shell()
        if shell is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            shell,
            "Open dataset",
            "",
            "Data files (*.csv *.dat *.tsv *.txt);;All files (*)",
        )
        if path:
            self.load_path(path)

    def load_path(self, path: str | Path) -> TaskToken | None:
        if self.lifecycle is not Lifecycle.RUNNING:
            return None
        path = Path(path).expanduser()
        ticket = self.state.begin_load()
        options = self.import_options
        loader = self._loader
        shell = self._shell()
        if shell is not None:
            shell.set_status(f"Checking and loading {path.name}…")

        def success(dataset: Dataset) -> None:
            if self.state.commit_dataset(ticket, dataset):
                current_shell = self._shell()
                if current_shell is not None:
                    current_shell.set_status(
                        f"Loaded {dataset.path.name} · {len(dataset.frame):,} rows"
                    )

        def failure(error: BaseException) -> None:
            self._present_error(error)
            current_shell = self._shell()
            if current_shell is not None and self.state.dataset is not None:
                current_shell.set_status(
                    f"Load failed. {self.state.dataset.path.name} is still active."
                )

        return self.submit_task(
            "load",
            lambda cancel: loader(path, options, cancel=cancel),
            success=success,
            failure=failure,
            receiver=shell,
            load_ticket=ticket,
            exclusive_group="load",
        )

    @Slot()
    def cancel_all(self) -> None:
        if self.lifecycle is not Lifecycle.RUNNING:
            return
        if self.runner.current_generation("load") is not None:
            self.state.cancel_load()
        self._publications.clear()
        self.runner.cancel_all()
        shell = self._shell()
        if shell is not None:
            shell.set_status("Task cancelled. The active dataset is unchanged.")

    @Slot()
    def begin_close(self) -> None:
        if self.lifecycle in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            return
        self.lifecycle = Lifecycle.CLOSING
        self.application_epoch += 1
        self.state.cancel_load()
        self._publications.clear()
        shell = self._shell()
        if shell is not None:
            shell.begin_closing(self.runner.activity.physical)
        self.runner.begin_closing()
        if self.lifecycle is Lifecycle.CLOSING and self.runner.activity.physical == 0:
            self._finalize_close()

    def _accept(self, publication: _Publication, outcome: TaskOutcome) -> bool:
        token = outcome.token
        if self.lifecycle is not Lifecycle.RUNNING:
            return False
        if publication.token != token:
            return False
        if token.application_epoch != self.application_epoch:
            return False
        if not self.runner.is_current(token):
            return False
        snapshot = self.state.snapshot()
        if token.dataset_revision != snapshot.dataset_revision:
            return False
        if token.selection_revision != snapshot.selection_revision:
            return False
        if token.load_ticket is not None and token.load_ticket != self.state.load_ticket:
            return False
        if publication.receiver is not None:
            receiver = publication.receiver()
            if receiver is None or not shiboken6.isValid(receiver):
                return False
        return True

    @Slot(object)
    def _on_outcome(self, outcome: TaskOutcome) -> None:
        if threading.get_ident() != self.gui_thread_id:
            raise RuntimeError("Task publication left the Qt GUI thread.")

        publication = self._publications.get(outcome.token.key)
        if publication is None:
            return
        accepted = self._accept(publication, outcome)
        if publication.token == outcome.token:
            self._publications.pop(outcome.token.key, None)
        if not accepted:
            return

        if outcome.status is TaskStatus.CANCELLED:
            return
        callback = (
            publication.failure if outcome.status is TaskStatus.ERROR else publication.success
        )
        if callback is None:
            if outcome.status is TaskStatus.ERROR and outcome.error is not None:
                self._present_error(outcome.error)
            return
        try:
            if outcome.status is TaskStatus.ERROR:
                callback(outcome.error or RuntimeError("Worker failed without an error."))
            else:
                callback(outcome.value)
        except Exception as error:
            logger.exception("Qt publication callback failed")
            self._present_error(error)

    @Slot(object)
    def _on_activity(self, activity: RunnerActivity) -> None:
        shell = self._shell()
        if shell is not None and self.lifecycle is not Lifecycle.CLOSED:
            shell.set_activity(activity.logical, activity.physical)
            if self.lifecycle is Lifecycle.CLOSING and activity.physical:
                shell.begin_closing(activity.physical)

    @Slot()
    def _on_physical_idle(self) -> None:
        if self.lifecycle is Lifecycle.CLOSING:
            self._finalize_close()

    def _on_state_changed(self, reason: str) -> None:
        shell = self._shell()
        dataset = self.state.dataset
        if shell is None:
            return
        if dataset is None:
            shell.set_dataset(None)
            return
        shell.set_dataset(dataset.path.name, len(dataset.frame), len(dataset.columns))

    def _present_error(self, error: BaseException) -> None:
        if isinstance(error, CancelledError):
            return
        text = user_error_text(error)
        shell = self._shell()
        if shell is not None:
            shell.set_status(text)
            shell.show_error(text)

    def _finalize_close(self) -> None:
        if self.lifecycle is Lifecycle.CLOSED:
            return
        if self.runner.activity.physical:
            return
        self.runner.finalize()
        self.lifecycle = Lifecycle.CLOSED
        shell = self._shell()
        if shell is not None and shiboken6.isValid(shell):
            shell.allow_close()

    def _shell(self) -> QtShell | None:
        shell = self._shell_ref()
        if shell is None or not shiboken6.isValid(shell):
            return None
        return shell
