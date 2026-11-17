from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from report_benchmark import classify, p95, summary
from stereograph.process import run_background


class BenchmarkReportTests(unittest.TestCase):
    def test_percentile_and_failure_count(self):
        self.assertEqual(p95(list(range(1, 21))), 19)
        rows = [{"ok": True, "elapsed_ns": 2000000}, {"ok": False, "elapsed_ns": 999999999}]
        self.assertEqual(summary(rows), {"attempts": 2, "failures": 1, "p50_ms": 2, "p95_ms": 2})

    def test_compare_requires_every_paired_round(self):
        reference = [dict(ok=True, round=i, elapsed_ns=100) for i in range(4)]
        candidate = [dict(ok=True, round=i, elapsed_ns=80) for i in range(4)]
        self.assertEqual(classify(candidate, reference), "Stereograph faster")
        self.assertEqual(classify(reference, candidate), "Stereograph slower")
        candidate[-1]["elapsed_ns"] = 99
        self.assertEqual(classify(candidate, reference), "Inconclusive")
        candidate[-1]["ok"] = False
        self.assertEqual(classify(candidate, reference), "Inconclusive · failures")


class BackgroundProcessTests(unittest.TestCase):
    def test_preserves_output_and_return_code(self):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "background.log"
            with path.open("wb") as output:
                code = "import sys; print('diagnostics', file=sys.stderr); sys.exit(4)"
                self.assertEqual(run_background([sys.executable, "-c", code], output, None, 5), 4)
            self.assertEqual(path.read_text().strip(), "diagnostics")

    def test_deadline_stops_child(self):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "unexpected"
            script = f"from pathlib import Path; import time; time.sleep(5); Path({str(path)!r}).touch()"
            with self.assertRaises(TimeoutError):
                run_background([sys.executable, "-c", script], subprocess.DEVNULL, None, .1)
            self.assertFalse(path.exists())
