"""Bounded PySide6 task execution with GUI-thread publication."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from ..data import CancelledError

logger = logging.getLogger(__name__)

TaskWork = Callable[[threading.Event], Any]


class TaskStatus(Enum):
    SUCCESS = auto()
    ERROR = auto()
    CANCELLED = auto()


class TaskRunnerClosed(RuntimeError):
    """New work was requested after the runner stopped accepting tasks."""


class TaskQueueFull(RuntimeError):
    """The bounded application queue cannot accept another distinct request."""


@dataclass(frozen=True)
class TaskToken:
    key: str
    generation: int
    application_epoch: int
    dataset_revision: int
    selection_revision: int
    load_ticket: int | None = None
    exclusive_group: str | None = None


@dataclass(frozen=True)
class TaskOutcome:
    token: TaskToken
    status: TaskStatus
    value: Any = None
    error: BaseException | None = None
    worker_thread_id: int | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class RunnerActivity:
    logical: int
    physical: int
    queued: int

    @property
    def busy(self) -> bool:
        return self.logical > 0 or self.physical > 0


@dataclass
class _Request:
    token: TaskToken
    work: TaskWork
    cancel: threading.Event


def _token_fields(token: TaskToken) -> str:
    return (
        f"key={token.key} generation={token.generation} "
        f"application_epoch={token.application_epoch} "
        f"dataset_revision={token.dataset_revision} "
        f"selection_revision={token.selection_revision}"
    )


class _Bridge(QObject):
    outcome = Signal(object)


class _Runnable(QRunnable):
    def __init__(self, request: _Request, bridge: _Bridge) -> None:
        super().__init__()
        self.request = request
        self.bridge = bridge
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        status = TaskStatus.SUCCESS
        value = None
        error: BaseException | None = None
        worker_thread_id = threading.get_ident()
        started_ns = time.perf_counter_ns()
        try:
            if self.request.cancel.is_set():
                raise CancelledError()
            value = self.request.work(self.request.cancel)
            if self.request.cancel.is_set():
                status = TaskStatus.CANCELLED
        except CancelledError as exc:
            status = TaskStatus.CANCELLED
            error = exc
        except Exception as exc:
            status = TaskStatus.ERROR
            error = exc

        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        exception_class = type(error).__name__ if error is not None else "None"
        if status is TaskStatus.ERROR and error is not None:
            logger.error(
                "event=task_worker_error %s exception_class=%s elapsed_ms=%.3f",
                _token_fields(self.request.token),
                exception_class,
                elapsed_ms,
                exc_info=(type(error), error, error.__traceback__),
            )
        logger.debug(
            "event=task_physical_finished %s status=%s elapsed_ms=%.3f exception_class=%s",
            _token_fields(self.request.token),
            status.name,
            elapsed_ms,
            exception_class,
        )
        self.bridge.outcome.emit(
            TaskOutcome(
                token=self.request.token,
                status=status,
                value=value,
                error=error,
                worker_thread_id=worker_thread_id,
                elapsed_ms=elapsed_ms,
            )
        )


class QtTaskRunner(QObject):
    """Application-owned scheduler in front of a private QThreadPool.

    Runner bookkeeping is GUI-thread-only. Worker threads receive only their
    work callable and a cancellation Event, then emit a typed TaskOutcome.
    """

    outcome_ready = Signal(object)
    activity_changed = Signal(object)
    physical_idle = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
        max_pending: int = 8,
    ) -> None:
        super().__init__(parent)
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_pending < 1:
            raise ValueError("max_pending must be positive")

        self.max_workers = max_workers
        self.max_pending = max_pending
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_workers)
        self._bridge = _Bridge(self)
        self._bridge.outcome.connect(self._on_worker_outcome)

        self._generation: dict[str, int] = {}
        self._logical: dict[str, int] = {}
        self._queued: OrderedDict[str, _Request] = OrderedDict()
        self._active: dict[tuple[str, int], _Request] = {}
        self._accepting = True
        self._closing = False

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def activity(self) -> RunnerActivity:
        return RunnerActivity(
            logical=len(self._logical),
            physical=max(len(self._active), self.pool.activeThreadCount()),
            queued=len(self._queued),
        )

    def current_generation(self, key: str) -> int | None:
        return self._logical.get(key)

    def is_current(self, token: TaskToken) -> bool:
        return self._logical.get(token.key) == token.generation

    def submit(
        self,
        key: str,
        work: TaskWork,
        *,
        application_epoch: int,
        dataset_revision: int,
        selection_revision: int,
        load_ticket: int | None = None,
        exclusive_group: str | None = None,
    ) -> TaskToken:
        if not self._accepting:
            generation = self._generation.get(key, 0) + 1
            logger.debug(
                "event=task_rejected reason=runner_closed key=%s generation=%d "
                "application_epoch=%d dataset_revision=%d selection_revision=%d",
                key,
                generation,
                application_epoch,
                dataset_revision,
                selection_revision,
            )
            raise TaskRunnerClosed("Task runner is closing.")

        # Replacement invalidates publication immediately. A physically running
        # predecessor may continue until its cooperative cancellation point.
        self.cancel(key)
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        token = TaskToken(
            key=key,
            generation=generation,
            application_epoch=application_epoch,
            dataset_revision=dataset_revision,
            selection_revision=selection_revision,
            load_ticket=load_ticket,
            exclusive_group=exclusive_group,
        )
        request = _Request(token=token, work=work, cancel=threading.Event())
        self._logical[key] = generation

        if self._can_start(request):
            logger.debug(
                "event=task_admitted admission=started %s",
                _token_fields(token),
            )
            self._start(request)
        else:
            if len(self._queued) >= self.max_pending:
                self._logical.pop(key, None)
                logger.debug(
                    "event=task_rejected reason=queue_full %s pending=%d",
                    _token_fields(token),
                    len(self._queued),
                )
                raise TaskQueueFull(f"Task queue is full ({self.max_pending} pending requests).")
            self._queued[key] = request
            logger.debug(
                "event=task_admitted admission=queued %s pending=%d",
                _token_fields(token),
                len(self._queued),
            )

        self._emit_activity()
        self._schedule()
        return token

    def cancel(self, key: str) -> None:
        generation = self._logical.pop(key, None)
        queued = self._queued.pop(key, None)
        active = self._active.get((key, generation)) if generation is not None else None

        request = active or queued
        if request is not None:
            logger.debug(
                "event=task_cancel_requested %s physical_active=%s queued=%s",
                _token_fields(request.token),
                active is not None,
                queued is not None,
            )

        if queued is not None:
            queued.cancel.set()
        if active is not None:
            active.cancel.set()

        self._emit_activity()

    def cancel_all(self) -> None:
        for key in tuple(self._logical):
            self.cancel(key)

    def begin_closing(self) -> None:
        if self._closing:
            return
        self._accepting = False
        self._closing = True
        self.cancel_all()
        for request in self._queued.values():
            request.cancel.set()
        self._queued.clear()
        self._emit_activity()
        if not self._active:
            self._check_physical_idle()

    def finalize(self) -> None:
        """Release pool resources after physical activity has reached zero."""
        if self.activity.physical:
            raise RuntimeError("Cannot finalize while physical tasks are active.")
        self._accepting = False
        self._closing = True
        self.pool.clear()
        if not self.pool.waitForDone(0):
            raise RuntimeError("QThreadPool still owns unfinished work.")

    def _can_start(self, request: _Request) -> bool:
        if len(self._active) >= self.max_workers:
            return False
        group = request.token.exclusive_group
        if group is None:
            return True
        return not any(active.token.exclusive_group == group for active in self._active.values())

    def _start(self, request: _Request) -> None:
        key = (request.token.key, request.token.generation)
        self._active[key] = request
        logger.debug(
            "event=task_started %s physical=%d queued=%d",
            _token_fields(request.token),
            len(self._active),
            len(self._queued),
        )
        self.pool.start(_Runnable(request, self._bridge))

    def _schedule(self) -> None:
        if self._closing:
            return
        made_progress = True
        while made_progress and len(self._active) < self.max_workers:
            made_progress = False
            for key, request in tuple(self._queued.items()):
                if self._can_start(request):
                    del self._queued[key]
                    self._start(request)
                    made_progress = True
                    break
        self._emit_activity()

    @Slot(object)
    def _on_worker_outcome(self, outcome: TaskOutcome) -> None:
        token = outcome.token
        exception_class = type(outcome.error).__name__ if outcome.error is not None else "None"
        logger.debug(
            "event=task_outcome_received %s status=%s elapsed_ms=%.3f exception_class=%s",
            _token_fields(token),
            outcome.status.name,
            outcome.elapsed_ms,
            exception_class,
        )
        self._active.pop((token.key, token.generation), None)
        # Keep the generation current while the GUI-thread coordinator validates
        # and publishes this outcome. A callback may submit a replacement.
        self.outcome_ready.emit(outcome)
        if self._logical.get(token.key) == token.generation:
            self._logical.pop(token.key, None)
        self._schedule()
        self._emit_activity()
        if not self._active:
            self._check_physical_idle()

    def _check_physical_idle(self) -> None:
        """Wait cooperatively for the QThreadPool to observe runnable return."""
        if self._active:
            return
        if self.pool.activeThreadCount() == 0:
            logger.debug(
                "event=task_pool_physical_idle logical=%d physical=0 queued=%d",
                len(self._logical),
                len(self._queued),
            )
            self._emit_activity()
            if self._closing:
                self.physical_idle.emit()
            return
        QTimer.singleShot(0, self._check_physical_idle)

    def _emit_activity(self) -> None:
        self.activity_changed.emit(self.activity)
