"""The browser images' offline check (`CI: runner image`, #2031): chromium launches, loads a page and closes through
the repo's own helper, `scraper.browser_fetch.BrowserFetcher`, inside the finished image with no network.

A loopback server stands in for the portal (`docker run --network none` keeps loopback). As PID 1 the check also
proves the teardown: once the fetcher is closed, this process holds no child, live or zombie (#2565).

`--page FILE --pages N` then loads FILE N times on one browser, as one exit IP's session does, and prints the memory
the check's process tree holds: information for a browser walk's `memory_mb`, never a verdict. Chromium runs as its
own process group (Playwright launches it detached), so the job runner's `peak_rss_mb` never counts it.
"""

from __future__ import annotations

import argparse
import http.server
import logging
import os
import sys
import threading
import time
from pathlib import Path

from scraper.browser_fetch import _CHROMIUM_COMM, BrowserFetcher, reap_browser_orphans

LOG = logging.getLogger("scripts.browser_image_check")

MARKER = "browser-image-check"
_PAGE = f"<!doctype html><html><head><title>{MARKER}</title></head><body><p id={MARKER}>ok</p></body></html>"
TEARDOWN_WAIT_S = 15.0


def _serve(page: bytes) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = page if self.path.startswith("/page") else _PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, name="loopback", daemon=True).start()
    return server


def _procs() -> dict[int, tuple[str, str, int]]:
    """pid -> (command name, state, ppid) of every process this check can read."""
    out: dict[int, tuple[str, str, int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            stat = Path(f"/proc/{name}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        state, ppid = stat[stat.rindex(")") + 2:].split()[:2]
        out[int(name)] = (stat[stat.index("(") + 1:stat.rindex(")")], state, int(ppid))
    return out


def _children(procs: dict[int, tuple[str, str, int]]) -> list[str]:
    me = os.getpid()
    return [f"{pid}:{comm}:{state}" for pid, (comm, state, ppid) in sorted(procs.items()) if ppid == me]


def _memory_mb() -> dict[str, int]:
    """PSS (shared pages split, as a cgroup charges them once) of this process, Playwright's driver and chromium."""
    me = os.getpid()
    procs, tree = _procs(), {me}
    for _ in range(8):  # python -> driver -> chromium -> its helpers
        tree |= {pid for pid, (_, _, ppid) in procs.items() if ppid in tree}
    pss = {"python": 0, "driver": 0, "chromium": 0}
    for pid in tree:
        try:
            rollup = Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        kb = next((int(line.split()[1]) for line in rollup.splitlines() if line.startswith("Pss:")), 0)
        pss["python" if pid == me else "chromium" if procs[pid][0].startswith(_CHROMIUM_COMM) else "driver"] += kb
    return {name: kb // 1024 for name, kb in pss.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--page", type=Path, help="an HTML file to load --pages times, for the memory reading")
    parser.add_argument("--pages", type=int, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stdout)
    server = _serve(args.page.read_bytes() if args.page else b"")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    fetcher = BrowserFetcher(proxy_url=None, timeout_s=30)
    try:
        first = fetcher.fetch(f"{base}/")
        if first.status_code != 200 or MARKER not in first.text:
            LOG.error("BROWSER check failed: status %d, marker %s", first.status_code, MARKER in first.text)
            return 1
        LOG.info("BROWSER check: chromium launched and served a page through BrowserFetcher, offline")
        if args.page and args.pages > 0:
            statuses = {fetcher.fetch(f"{base}/page?n={n}").status_code for n in range(1, args.pages + 1)}
            mb = _memory_mb()
            LOG.info("BROWSER memory pages=%d page_kb=%d pss_mb python=%d driver=%d chromium=%d total=%d statuses=%s",
                     args.pages, len(args.page.read_bytes()) // 1024, mb["python"], mb["driver"], mb["chromium"],
                     sum(mb.values()), sorted(statuses))
    finally:
        fetcher.close()
        server.shutdown()
    deadline = time.monotonic() + TEARDOWN_WAIT_S
    while (left := _children(_procs())) and time.monotonic() < deadline:
        reap_browser_orphans()
        time.sleep(0.5)
    if left:
        LOG.error("BROWSER check failed: %d child processes outlived the close: %s", len(left), " ".join(left))
        return 1
    LOG.info("BROWSER check: closed, no child process left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
