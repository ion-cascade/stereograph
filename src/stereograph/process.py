"""Run background Blender with on-disk diagnostics and an exit deadline."""

from concurrent.futures import ThreadPoolExecutor
import subprocess


def run_background(args, output, environment, timeout):
    with subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, env=environment) as process:
        # A blocking wait in the worker wakes on process exit. A timed Popen
        # wait polls on POSIX, introducing jitter between Blender exit and reply.
        with ThreadPoolExecutor(max_workers=1) as waiter:
            finished = waiter.submit(process.wait)
            try:
                return finished.result(timeout)
            except BaseException:
                process.kill()
                raise
