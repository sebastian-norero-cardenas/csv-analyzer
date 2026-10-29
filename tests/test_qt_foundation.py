"""F3.A Qt foundation contracts and deterministic concurrency tests."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEventLoop, QObject, QTimer
from PySide6.QtWidgets import QApplication

from csv_analyzer.data import CancelledError, load_dataset
from csv_analyzer.qt_app.bootstrap import create_application
from csv_analyzer.qt_app.coordinator import Lifecycle, QtCoordinator
from csv_analyzer.qt_app.shell import QtShell
from csv_analyzer.qt_app.tasks import QtTaskRunner, TaskQueueFull, TaskStatus
from csv_analyzer.state import AppState, FilterRule

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication(["varia-f3a-tests"])


def pump_until(predicate, timeout_ms=5000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(2)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timed_out = {"value": False}

    def check():
        if predicate():
            loop.quit()

    def expire():
        timed_out["value"] = True
        loop.quit()

    poll.timeout.connect(check)
    timeout.timeout.connect(expire)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if timed_out["value"] and not predicate():
        raise AssertionError("Qt condition did not become true before timeout")


class RecordingShell(QtShell):
    def __init__(self):
        super().__init__()
        self.errors = []

    def show_error(self, text):
        self.errors.append(text)


class StateContractTests(unittest.TestCase):
    def test_revision_domains_and_snapshot(self):
        state = AppState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.csv"
            path.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")
            dataset = load_dataset(path)

            initial = state.snapshot()
            self.assertEqual(
                (initial.revision, initial.dataset_revision, initial.selection_revision),
                (0, 0, 0),
            )
            ticket = state.begin_load()
            self.assertTrue(state.commit_dataset(ticket, dataset))
            self.assertEqual(
                (state.revision, state.dataset_revision, state.selection_revision),
                (1, 1, 1),
            )

            rules = (FilterRule("x", lower=2),)
            self.assertTrue(state.commit_filters(state.revision, rules, np.array([False, True])))
            self.assertEqual(
                (state.revision, state.dataset_revision, state.selection_revision),
                (2, 1, 2),
            )
            before = state.snapshot()
            state.cancel_load()
            self.assertEqual(
                (
                    state.revision,
                    state.dataset_revision,
                    state.selection_revision,
                    state.dataset,
                    state.mask,
                ),
                (
                    before.revision,
                    before.dataset_revision,
                    before.selection_revision,
                    before.dataset,
                    before.mask,
                ),
            )


class RunnerContractTests(unittest.TestCase):
    def make_runner(self, **kwargs):
        runner = QtTaskRunner(**kwargs)
        self.addCleanup(self._cleanup_runner, runner)
        return runner

    def _cleanup_runner(self, runner):
        if not runner.closing:
            runner.begin_closing()
        if runner.activity.physical == 0:
            runner.finalize()

    def test_success_and_error_outcomes(self):
        runner = self.make_runner()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        runner.submit(
            "ok",
            lambda cancel: "value",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        pump_until(lambda: len(outcomes) == 1)
        self.assertEqual(outcomes[0].status, TaskStatus.SUCCESS)
        self.assertEqual(outcomes[0].value, "value")

        def fail(cancel):
            raise ValueError("worker boom")

        runner.submit(
            "error",
            fail,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        pump_until(lambda: len(outcomes) == 2)
        self.assertEqual(outcomes[1].status, TaskStatus.ERROR)
        self.assertIsInstance(outcomes[1].error, ValueError)

    def test_cancel_is_logical_before_physical_completion(self):
        runner = self.make_runner(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        outcomes = []
        runner.outcome_ready.connect(outcomes.append)

        def work(cancel):
            entered.set()
            release.wait(timeout=5)
            return "late"

        runner.submit(
            "slow",
            work,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertTrue(entered.wait(timeout=2))
        runner.cancel("slow")
        self.assertEqual(runner.activity.logical, 0)
        self.assertEqual(runner.activity.physical, 1)
        release.set()
        pump_until(lambda: runner.activity.physical == 0)
        self.assertEqual(outcomes[-1].status, TaskStatus.CANCELLED)

    def test_burst_replacement_and_bounded_pending_queue(self):
        runner = self.make_runner(max_workers=1, max_pending=8)
        entered = threading.Event()
        release = threading.Event()

        def hold(cancel):
            entered.set()
            release.wait(timeout=5)
            return None

        runner.submit(
            "active",
            hold,
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertTrue(entered.wait(timeout=2))

        for index in range(8):
            runner.submit(
                f"q{index}",
                lambda cancel, value=index: value,
                application_epoch=0,
                dataset_revision=0,
                selection_revision=0,
            )
        self.assertEqual(runner.activity.queued, 8)
        with self.assertRaises(TaskQueueFull):
            runner.submit(
                "overflow",
                lambda cancel: None,
                application_epoch=0,
                dataset_revision=0,
                selection_revision=0,
            )

        old_generation = runner.current_generation("q0")
        replacement = runner.submit(
            "q0",
            lambda cancel: "replacement",
            application_epoch=0,
            dataset_revision=0,
            selection_revision=0,
        )
        self.assertGreater(replacement.generation, old_generation)
        self.assertEqual(runner.activity.queued, 8)

        runner.cancel_all()
        release.set()
        pump_until(lambda: runner.activity.physical == 0)


class CoordinatorContractTests(unittest.TestCase):
    def make(self, **kwargs):
        shell = RecordingShell()
        coordinator = QtCoordinator(shell, **kwargs)
        coordinator.start()
        self.addCleanup(self.cleanup_coordinator, coordinator, shell)
        return coordinator, shell

    def cleanup_coordinator(self, coordinator, shell):
        if coordinator.lifecycle not in (Lifecycle.CLOSING, Lifecycle.CLOSED):
            coordinator.begin_close()
        if coordinator.lifecycle is not Lifecycle.CLOSED:
            pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        shell.deleteLater()
        APP.processEvents()

    def write(self, directory, name, text):
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_bootstrap_headless_and_clean_close(self):
        app, coordinator, shell = create_application()
        self.assertIs(app, APP)
        self.assertEqual(coordinator.lifecycle, Lifecycle.STARTING)
        coordinator.start()
        self.assertEqual(coordinator.lifecycle, Lifecycle.RUNNING)
        self.assertEqual(shell.dataset_label.text(), "No dataset loaded")
        coordinator.begin_close()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)

    def test_callback_and_dataset_commit_run_on_gui_thread(self):
        coordinator, shell = self.make()
        gui_thread = threading.get_ident()
        callback_threads = []
        state_threads = []
        coordinator.state.subscribe(lambda reason: state_threads.append(threading.get_ident()))

        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "a.csv", "x,y\n1,2\n3,4\n")
            token = coordinator.load_path(path)
            self.assertIsNotNone(token)
            pump_until(lambda: coordinator.state.dataset is not None)
            self.assertEqual(state_threads, [gui_thread])

        coordinator.submit_task(
            "thread-check",
            lambda cancel: threading.get_ident(),
            success=lambda worker_thread: callback_threads.append(
                (worker_thread, threading.get_ident())
            ),
        )
        pump_until(lambda: bool(callback_threads))
        self.assertNotEqual(callback_threads[0][0], gui_thread)
        self.assertEqual(callback_threads[0][1], gui_thread)

    def test_worker_error_and_callback_exception_do_not_break_following_work(self):
        coordinator, shell = self.make()
        seen = []

        def fail(cancel):
            raise ValueError("expected worker error")

        coordinator.submit_task("failure", fail)
        pump_until(lambda: bool(shell.errors))
        self.assertIn("expected worker error", shell.errors[-1])

        def broken(value):
            raise RuntimeError("callback exploded")

        coordinator.submit_task("callback", lambda cancel: 1, success=broken)
        pump_until(lambda: any("callback exploded" in item for item in shell.errors))

        coordinator.submit_task("after", lambda cancel: 2, success=seen.append)
        pump_until(lambda: seen == [2])

    def test_cancel_and_late_result_never_publish(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def slow(cancel):
            entered.set()
            release.wait(timeout=5)
            return "late"

        coordinator.submit_task("slow", slow, success=seen.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.cancel_all()
        self.assertEqual(coordinator.runner.activity.logical, 0)
        self.assertEqual(coordinator.runner.activity.physical, 1)
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, [])

    def test_old_generation_is_discarded(self):
        coordinator, shell = self.make(max_workers=2)
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def old(cancel):
            entered.set()
            release.wait(timeout=5)
            return "old"

        coordinator.submit_task("same", old, success=seen.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.submit_task("same", lambda cancel: "new", success=seen.append)
        pump_until(lambda: "new" in seen)
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, ["new"])

    def test_dataset_change_invalidates_task_publication(self):
        coordinator, shell = self.make()
        entered = threading.Event()
        release = threading.Event()
        seen = []

        with tempfile.TemporaryDirectory() as directory:
            a = load_dataset(self.write(directory, "a.csv", "x\n1\n"))
            b = load_dataset(self.write(directory, "b.csv", "x\n2\n"))
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), a))

            def derived(cancel):
                entered.set()
                release.wait(timeout=5)
                return "stale"

            coordinator.submit_task("derived", derived, success=seen.append)
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), b))
            release.set()
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            self.assertEqual(seen, [])

    def test_failed_b_from_filtered_a_preserves_state_and_revisions(self):
        coordinator, shell = self.make()
        with tempfile.TemporaryDirectory() as directory:
            a_path = self.write(directory, "a.csv", "x,y\n1,2\n3,4\n")
            bad_path = self.write(directory, "bad.csv", "x,y\n1,2,3\n")
            a = load_dataset(a_path)
            self.assertTrue(coordinator.state.commit_dataset(coordinator.state.begin_load(), a))
            self.assertTrue(
                coordinator.state.commit_filters(
                    coordinator.state.revision,
                    (FilterRule("x", lower=2),),
                    np.array([False, True]),
                )
            )
            before = coordinator.state.snapshot()
            coordinator.load_path(bad_path)
            pump_until(lambda: coordinator.runner.activity.physical == 0)
            after = coordinator.state.snapshot()
            self.assertIs(after.dataset, before.dataset)
            self.assertIs(after.mask, before.mask)
            self.assertEqual(after.filters, before.filters)
            self.assertEqual(
                (after.revision, after.dataset_revision, after.selection_revision),
                (before.revision, before.dataset_revision, before.selection_revision),
            )
            self.assertTrue(shell.errors)

    def test_load_replacement_waits_for_physical_predecessor(self):
        entered_b = threading.Event()
        release_b = threading.Event()
        started_c = threading.Event()

        def loader(path, options, *, cancel=None):
            path = Path(path)
            if path.name == "b.csv":
                entered_b.set()
                release_b.wait(timeout=5)
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
            if path.name == "c.csv":
                started_c.set()
            return load_dataset(path, options, cancel=cancel)

        coordinator, shell = self.make(loader=loader)
        with tempfile.TemporaryDirectory() as directory:
            a = self.write(directory, "a.csv", "x\n1\n")
            b = self.write(directory, "b.csv", "x\n2\n")
            c = self.write(directory, "c.csv", "x\n3\n")

            coordinator.load_path(a)
            pump_until(
                lambda: (
                    coordinator.state.dataset is not None
                    and coordinator.state.dataset.path.name == "a.csv"
                )
            )
            coordinator.load_path(b)
            self.assertTrue(entered_b.wait(timeout=2))
            coordinator.load_path(c)
            self.assertFalse(started_c.is_set())
            self.assertEqual(coordinator.runner.activity.physical, 1)
            release_b.set()
            pump_until(lambda: started_c.is_set())
            pump_until(
                lambda: (
                    coordinator.state.dataset is not None
                    and coordinator.state.dataset.path.name == "c.csv"
                )
            )
            self.assertEqual(coordinator.state.dataset.frame.iloc[0, 0], 3)

    def test_destroyed_receiver_discards_result(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        seen = []
        receiver = QObject()

        def work(cancel):
            entered.set()
            release.wait(timeout=5)
            return 1

        coordinator.submit_task("receiver", work, success=seen.append, receiver=receiver)
        self.assertTrue(entered.wait(timeout=2))
        del receiver
        gc.collect()
        release.set()
        pump_until(lambda: coordinator.runner.activity.physical == 0)
        self.assertEqual(seen, [])

    def test_close_with_active_temporarily_noncancelable_worker(self):
        coordinator, shell = self.make(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        published = []

        def noncancelable(cancel):
            entered.set()
            release.wait(timeout=5)
            return "finished"

        coordinator.submit_task("physical", noncancelable, success=published.append)
        self.assertTrue(entered.wait(timeout=2))
        coordinator.begin_close()
        self.assertEqual(coordinator.lifecycle, Lifecycle.CLOSING)
        self.assertEqual(coordinator.runner.activity.logical, 0)
        self.assertEqual(coordinator.runner.activity.physical, 1)
        self.assertIsNone(
            coordinator.submit_task("rejected", lambda cancel: None, success=published.append)
        )
        release.set()
        pump_until(lambda: coordinator.lifecycle is Lifecycle.CLOSED)
        self.assertEqual(published, [])


class SeparationGateTests(unittest.TestCase):
    def run_guarded_import(self, forbidden, statement):
        code = f"""
import builtins
real_import = builtins.__import__
forbidden = {forbidden!r}
def guarded(name, *args, **kwargs):
    if any(name == item or name.startswith(item + '.') for item in forbidden):
        raise RuntimeError('forbidden import: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
{statement}
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_core_imports_without_pyside6(self):
        self.run_guarded_import(
            ["PySide6", "shiboken6"],
            "import csv_analyzer.data, csv_analyzer.analysis, csv_analyzer.state, "
            "csv_analyzer.plots, csv_analyzer.exports",
        )

    def test_qt_does_not_import_tk_and_ctk_does_not_import_qt(self):
        self.run_guarded_import(
            ["tkinter", "customtkinter"],
            "import csv_analyzer.qt_app.bootstrap",
        )
        self.run_guarded_import(
            ["PySide6", "shiboken6"],
            "import csv_analyzer.app",
        )

    def test_packaging_keeps_both_ctk_entrypoints_and_adds_qt(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('varia-studio = "csv_analyzer.app:main"', text)
        self.assertIn('csv-analyzer = "csv_analyzer.app:main"', text)
        self.assertIn('varia-studio-qt = "csv_analyzer.qt_app:main"', text)


if __name__ == "__main__":
    unittest.main()
