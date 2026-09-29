import threading
import time
import unittest

from csv_analyzer.jobs import TaskRunner


class Scheduler:
    def __init__(self):
        self.main_thread = threading.get_ident()

    def after(self, delay, callback):
        assert threading.get_ident() == self.main_thread
        return "timer"

    def after_cancel(self, timer):
        assert threading.get_ident() == self.main_thread


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.runner = TaskRunner(Scheduler())
        self.addCleanup(self.runner.close)

    def drain(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.runner.poll()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_result_delivered_on_main_thread(self):
        main = threading.get_ident()
        seen = []
        self.runner.submit(
            "a",
            lambda cancel: threading.get_ident(),
            lambda worker: seen.append((worker, threading.get_ident())),
            self.fail,
        )
        self.drain(lambda: bool(seen))
        self.assertNotEqual(seen[0][0], main)
        self.assertEqual(seen[0][1], main)

    def test_replacement_discards_old_result(self):
        release = threading.Event()
        entered = threading.Event()
        seen = []

        def slow(cancel):
            entered.set()
            release.wait(timeout=2)
            return "old"

        self.runner.submit("same", slow, seen.append, self.fail)
        self.assertTrue(entered.wait(timeout=1))
        self.runner.submit("same", lambda cancel: "new", seen.append, self.fail)
        self.drain(lambda: bool(seen))
        release.set()
        self.runner.executor.shutdown(wait=True)
        self.runner.poll()
        self.assertEqual(seen, ["new"])

    def test_cancellation_and_failure(self):
        seen = []
        self.runner.submit("a", lambda cancel: 1, seen.append, self.fail)
        self.runner.cancel("a")
        self.runner.executor.submit(lambda: None).result(timeout=2)
        self.runner.poll()
        self.assertEqual(seen, [])

        def fail(cancel):
            raise ValueError("expected failure")

        self.runner.submit("b", fail, self.fail, seen.append)
        self.drain(lambda: bool(seen))
        self.assertIsInstance(seen[0], ValueError)

    def test_broken_result_and_error_callbacks_do_not_stop_other_jobs(self):
        def broken(value):
            raise RuntimeError("callback failed")

        seen = []
        self.runner.submit("a", lambda cancel: 1, broken, broken)
        self.runner.submit("b", lambda cancel: 2, seen.append, self.fail)
        with self.assertLogs("csv_analyzer.jobs", level="ERROR"):
            self.drain(lambda: seen == [2])
        self.assertFalse(self.runner.closed)


if __name__ == "__main__":
    unittest.main()
