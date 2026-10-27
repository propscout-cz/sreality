"""A throwaway job for the runner's real-child tests (tests/scraper/test_job_runner.py); pytest never collects it.

It prints `ready` once it is set up, then sleeps, so a test can signal it at a known point.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context


def _item(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--cooperative", action="store_true", help="install scraper.stop; exit 143 on SIGTERM")
    parser.add_argument("--pool", action="store_true", help="with --cooperative: finish a pool item on the stop")
    parser.add_argument("--hold-mb", type=int, default=0)
    parser.add_argument("--lines", type=int, default=0)
    parser.add_argument("--grandchild", action="store_true", help="fork a sleeper that keeps SIGTERM's default")
    parser.add_argument("--exit", type=int, default=None)
    args = parser.parse_args()

    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if args.cooperative:
        from scraper import stop

        stop.install()
    if args.grandchild:
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            time.sleep(60)
            os._exit(0)
        print(f"grandchild {pid}", flush=True)
    hold = b"\x01" * (args.hold_mb << 20)  # after the fork, so the grandchild shares none of it
    for i in range(args.lines):
        print(f"line {i}", flush=True)
    executor = future = None
    if args.pool:
        executor = ProcessPoolExecutor(1, mp_context=get_context("spawn"))
        future = executor.submit(_item, 0.5)
        while not (future.running() or future.done()):
            time.sleep(0.02)
    print("ready", flush=True)
    if args.exit is not None:
        sys.exit(args.exit)
    if args.cooperative:
        from scraper import stop

        stop.wait(30)
        if future is not None and executor is not None:
            future.result(timeout=10)
            print("pool ok", flush=True)
            executor.shutdown()
        sys.exit(stop.EXIT_CODE)
    time.sleep(30)
    del hold


if __name__ == "__main__":
    main()
