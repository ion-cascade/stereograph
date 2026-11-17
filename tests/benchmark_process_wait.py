"""Isolate background process wait overhead with paired native Blender launches."""

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from stereograph.process import run_background


def benchmark(blender, count, destination):
    command = [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-expr", "pass"]
    rows = []
    with TemporaryDirectory(prefix="stereograph-process-") as directory:
        logfile = Path(directory) / "output.log"
        for index in range(count):
            order = ("timed-poll", "blocking-wait") if index % 2 == 0 else ("blocking-wait", "timed-poll")
            for method in order:
                with logfile.open("wb") as output:
                    started = perf_counter_ns()
                    if method == "timed-poll":
                        returncode = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                                    stdin=subprocess.DEVNULL, env=os.environ, timeout=30).returncode
                    else:
                        returncode = run_background(command, output, os.environ, 30)
                    elapsed = perf_counter_ns() - started
                if returncode:
                    raise RuntimeError(logfile.read_text(errors="replace")[-2000:])
                rows.append(dict(method=method, pair=index, elapsed_ns=elapsed))
    destination.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for method in ("timed-poll", "blocking-wait"):
        values = [row["elapsed_ns"] / 1e6 for row in rows if row["method"] == method]
        print(f"{method}: median {statistics.median(values):.2f} ms across {count} launches", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--pairs", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.blender or args.pairs < 1:
        parser.error("A Blender executable and positive pair count are required.")
    benchmark(args.blender, args.pairs, args.output)
