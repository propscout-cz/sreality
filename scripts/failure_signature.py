"""failure_signature.py — the reason line: the one log line that explains a red run.

The canary's poller stores it in `workflow_failures.reason`; the jobs check shows it in the job
cell's message. Three tiers, the first that matches wins, and inside a tier the LAST matching
line: verify_pipeline's `CHECK <key> status=fail` line, an exception line outside the runner's
`##[group]` blocks, the `##[error]` annotation. The exception tier once ran first over every
`Word: text` line, so the runner's setup rows won: job 116299577551 went red on
`CHECK llm_errors status=fail` and was filed as `Metadata: read`. Pure stdlib: the poller
installs base deps only.
"""

from __future__ import annotations

import re
from collections.abc import Callable

MAX_REASON_LEN = 300

# Actions prefixes EVERY log line with an ISO timestamp; nothing is at column 0 until it goes.
_LOG_TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ")

# verify_pipeline's `CHECK %s status=%s value=%s`, still behind logging's own prefix.
_CHECK_FAIL_RE = re.compile(r"\bCHECK [a-z0-9_]+ status=fail\b")

# `ValueError: …`, `requests.exceptions.HTTPError: …`, `Exception: …`. Classes named otherwise
# (CheckViolation, QueryCanceled) are read as a traceback's final line instead.
_EXC_LINE_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)*(?:[A-Z][A-Za-z0-9_]*)?(?:Error|Exception): \S")
_TRACEBACK = "Traceback (most recent call last):"

# Runner rows the retired engine read as reasons, never one: the runner image, the GITHUB_TOKEN
# permissions and checkout's detached-HEAD note.
_BOILERPLATE = ("Image:", "Version:", "Commit:", "Region:", "Cloud:", "Contents:", "Metadata:",
                "Packages:", "Actions:", "Note: switching to")


def _last(lines: list[str], keep: Callable[[str], bool]) -> str | None:
    return next((line for line in reversed(lines) if keep(line)), None)


def _exception_line(lines: list[str]) -> str | None:
    """The last exception line outside a `##[group]` block that is not a runner row."""
    found: str | None = None
    in_group = in_traceback = False
    for line in lines:
        if line.startswith("##[group]"):
            in_group, in_traceback = True, False
        elif line.startswith("##[endgroup]"):
            in_group = False
        elif in_group or line.startswith(_BOILERPLATE):
            continue
        elif line.startswith(_TRACEBACK):
            in_traceback = True
        elif in_traceback and (not line.strip() or line[0].isspace()):
            continue
        else:
            if (in_traceback and not line.startswith("##[")) or _EXC_LINE_RE.match(line):
                found = line
            in_traceback = False
    return found


def reason_line(text: str | None) -> str | None:
    """The line that explains a red run's log, without its Actions timestamp, or None."""
    lines = [_LOG_TS_PREFIX_RE.sub("", line) for line in (text or "").splitlines()]
    line = (_last(lines, lambda ln: _CHECK_FAIL_RE.search(ln) is not None)
            or _exception_line(lines)
            or _last(lines, lambda ln: ln.startswith("##[error]")))
    return line.strip()[:MAX_REASON_LEN] if line else None
