"""A small worker queue. Background threads never call Tk, including after()."""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .data import CancelledError

logger = logging.getLogger(__name__)


@dataclass
class _Job:
    ticket: int
    cancel: threading.Event
    success: Callable[[Any], None]
    error: Callable[[Exception], None]
    future: Any = None


class TaskRunner:
    def __init__(self, root, activity: Callable[[bool], None] | None = None):
        self.root = root
        self.activity = activity or (lambda busy: None)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="csv-analysis")
        self.results = queue.Queue()
        self.jobs: dict[str, _Job] = {}
        self.ticket = 0
        self.closed = False
        self.poll_id = root.after(50, self.poll)

    def submit(self, key, work, success, error) -> None:
        if self.closed:
            return
        self.cancel(key)
        self.ticket += 1
        job = _Job(self.ticket, threading.Event(), success, error)
        self.jobs[key] = job

        def run():
            try:
                value, failure = work(job.cancel), None
            except Exception as exc:
                value, failure = None, exc
            self.results.put((key, job.ticket, value, failure))

        job.future = self.executor.submit(run)
        self.activity(True)

    def cancel(self, key: str) -> None:
        job = self.jobs.pop(key, None)
        if job:
            job.cancel.set()
            if job.future:
                job.future.cancel()
        self.activity(bool(self.jobs))

    def cancel_all(self) -> None:
        for key in tuple(self.jobs):
            self.cancel(key)

    def poll(self) -> None:
        if self.closed:
            return
        while True:
            try:
                key, ticket, value, failure = self.results.get_nowait()
            except queue.Empty:
                break
            job = self.jobs.get(key)
            if job is None or ticket != job.ticket:
                continue
            del self.jobs[key]
            if job.cancel.is_set() or isinstance(failure, CancelledError):
                continue
            try:
                if failure is not None:
                    job.error(failure)
                else:
                    job.success(value)
            except Exception as exc:
                logger.exception("Result handling failed")
                # A failed error dialog must not terminate polling for every
                # other job. Only route a success-handler failure to it once.
                if failure is None:
                    try:
                        job.error(exc)
                    except Exception:
                        logger.exception("Error handling failed")
        if self.closed:
            return
        self.activity(bool(self.jobs))
        self.poll_id = self.root.after(50, self.poll)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel_all()
        self.root.after_cancel(self.poll_id)
        self.executor.shutdown(wait=False, cancel_futures=True)
