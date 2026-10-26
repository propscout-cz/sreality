"""Ticket claims: which session holds a GitHub issue, judged from marker comments on it."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

TTL = timedelta(hours=4)
RENEW_AFTER = timedelta(hours=2)
SETTLE_SECONDS = 15
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
SESSION_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MARKER_RE = re.compile(
    r"<!-- ticket-claim:v1 action=(claim|renew|release|withdrawn) "
    r"session=([a-z0-9][a-z0-9-]{0,39})(?: claim=\d+)? -->(?:\r?\n|$)"
)
REPO = "repos/{owner}/{repo}"
CLOSING_QUERY = (
    "query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){issue(number:$n)"
    "{closedByPullRequestsReferences(first:20,includeClosedPrs:true){nodes{number state isDraft}}}}}"
)
OK, TAKEN, USAGE, NOT_OPEN, GH_ERROR = 0, 1, 2, 3, 4
EXIT_HELP = (
    "exit codes: 0 yours or free, 1 another session holds it / claimed first / an open PR closes it, "
    "2 usage or unknown session name, 3 closed / a PR / not found, 4 GitHub error"
)


class GhError(Exception):
    """A gh call failed."""


@dataclass(frozen=True)
class Event:
    kind: str
    at: datetime
    id: int = 0
    body: str = ""
    association: str = ""
    source: int | None = None


@dataclass(frozen=True)
class ClosingPr:
    number: int
    state: str
    draft: bool


@dataclass
class Holder:
    session: str
    claim_id: int
    since: datetime
    last: datetime
    pinned: bool = False


@dataclass(frozen=True)
class Loss:
    session: str
    comment_id: int
    winner: str
    winner_claim: int


@dataclass
class Verdict:
    holder: Holder | None = None
    losses: list[Loss] = field(default_factory=list)


@dataclass
class Ticket:
    number: int
    kind: str
    now: datetime
    state: str = ""
    title: str = ""
    verdict: Verdict = field(default_factory=Verdict)
    closing: list[ClosingPr] = field(default_factory=list)

    def open_prs(self) -> list[int]:
        """Open PRs whose closing keyword names this ticket."""
        return [p.number for p in self.closing if p.state == "OPEN"]

    def taken(self, me: str | None) -> bool:
        """`claim` would refuse: another session holds it, or nobody does and an open PR closes it."""
        h = self.verdict.holder
        if self.kind != "issue" or self.state != "open":
            return False
        return h.session != me if h else bool(self.open_prs())


def parse_marker(body: str) -> tuple[str, str] | None:
    """(action, session) from a claim comment's first line, else None."""
    m = MARKER_RE.match(body)
    return (m.group(1), m.group(2)) if m else None


def fold(events: list[Event], now: datetime, closing: list[ClosingPr]) -> Verdict:
    """Replay the timeline: the first claim made while none is live wins; it lapses TTL after its last activity."""
    closing_numbers = {p.number for p in closing}
    open_numbers = {p.number for p in closing if p.state == "OPEN"}
    v = Verdict()
    for e in sorted(events, key=lambda e: (e.at, e.id)):
        h = v.holder
        if h and not h.pinned and e.at - h.last > TTL:
            v.holder = h = None
        if e.kind == "closed":
            v.holder = None
        elif e.kind == "referenced" and h:
            h.last = e.at
        elif e.kind == "cross-referenced" and h and e.source in closing_numbers:
            h.last = e.at
            h.pinned = h.pinned or e.source in open_numbers
        elif e.kind == "commented" and e.association in TRUSTED:
            marker = parse_marker(e.body)
            if marker is None:
                continue
            action, session = marker
            if action == "claim" and h is None:
                v.holder = Holder(session, e.id, e.at, e.at)
            elif action == "claim" and h and h.session != session:
                v.losses.append(Loss(session, e.id, h.session, h.claim_id))
            elif action in ("claim", "renew") and h and h.session == session:
                h.last = e.at
            elif action == "release" and h and h.session == session:
                v.holder = None
    if v.holder and now - v.holder.last > TTL and not open_numbers:
        v.holder = None
    return v


def session_name(explicit: str | None) -> str | None:
    """--as, else the name Claude Code gave this session; None unless that is a valid name."""
    name = explicit
    pid = os.environ.get("CLAUDE_PID", "")
    if name is None and pid.isdigit():
        try:
            name = json.loads((Path.home() / ".claude" / "sessions" / f"{pid}.json").read_text()).get("name")
        except (OSError, ValueError, AttributeError):
            name = None
    return name if isinstance(name, str) and SESSION_RE.fullmatch(name) else None


def _run(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """One gh invocation: (exit code, stdout, stderr)."""
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _gh(args: list[str], stdin: str | None = None) -> Any:
    """A gh api call's decoded JSON; GhError on failure."""
    code, out, err = _run(["api", *args], stdin)
    if code != 0:
        raise GhError(err.strip() or out.strip())
    return json.loads(out)


def _paged(path: str) -> list[dict]:
    """Every page of a per_page=100 listing."""
    out: list[dict] = []
    page = 1
    while True:
        batch = _gh([f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}"])
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def _utc(stamp: str) -> datetime:
    """A GitHub ISO timestamp as an aware UTC datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def read_issue(n: int) -> tuple[dict | None, datetime]:
    """The issue (None on 404) and GitHub's Date header, the clock every judgement uses."""
    code, out, err = _run(["api", "--include", f"{REPO}/issues/{n}"])
    gap = re.search(r"\r?\n\r?\n", out)
    head, body = (out[: gap.start()], out[gap.end():]) if gap else (out, "")
    lines = head.splitlines()
    status = lines[0].split()[1] if lines and len(lines[0].split()) > 1 else ""
    date = next((ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("date:")), "")
    if status in ("404", "410") and date:
        return None, parsedate_to_datetime(date)
    if code != 0 or status != "200" or not date:
        raise GhError(err.strip() or f"GET issue #{n}: HTTP {status or '?'}")
    return json.loads(body), parsedate_to_datetime(date)


def read_events(n: int) -> list[Event]:
    """The issue's timeline as fold() events."""
    events: list[Event] = []
    for r in _paged(f"{REPO}/issues/{n}/timeline"):
        if not r.get("created_at"):
            continue
        source = ((r.get("source") or {}).get("issue") or {}).get("number")
        events.append(Event(
            kind=r.get("event") or "", at=_utc(r["created_at"]), id=r.get("id") or 0,
            body=r.get("body") or "", association=r.get("author_association") or "", source=source,
        ))
    return events


def read_closing_prs(n: int) -> list[ClosingPr]:
    """Every PR, open or not, whose closing keyword names the issue."""
    data = _gh(["graphql", "-F", "owner={owner}", "-F", "name={repo}", "-F", f"n={n}", "-f", f"query={CLOSING_QUERY}"])
    nodes = data["data"]["repository"]["issue"]["closedByPullRequestsReferences"]["nodes"]
    return [ClosingPr(p["number"], p["state"], bool(p["isDraft"])) for p in nodes]


def read_ticket(n: int) -> Ticket:
    """The issue, and for an open one its fold() verdict."""
    issue, now = read_issue(n)
    if issue is None:
        return Ticket(n, "missing", now)
    t = Ticket(n, "pr" if issue.get("pull_request") else "issue", now, issue["state"], issue.get("title") or "")
    if t.kind == "issue" and t.state == "open":
        t.closing = read_closing_prs(n)
        t.verdict = fold(read_events(n), now, t.closing)
    return t


def _hm(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def describe(t: Ticket, me: str | None) -> str:
    """One status line for a ticket."""
    if t.kind == "missing":
        return f"#{t.number} not found"
    if t.kind == "pr":
        return f"#{t.number} is a pull request"
    if t.state != "open":
        return f"#{t.number} closed  {t.title}"
    h, prs = t.verdict.holder, t.open_prs()
    if h:
        you = " (you)" if h.session == me else ""
        until = f"held while PR #{prs[0]} is open" if prs else f"expires {_hm(h.last + TTL)}"
        line = f"#{t.number} held by {h.session}{you} since {_hm(h.since)}, last activity {_hm(h.last)}, {until}"
    else:
        line = f"#{t.number} free"
    for p in t.closing:
        if p.state == "OPEN":
            line += f"; {'draft ' if p.draft else ''}PR #{p.number} closes it"
    for loss in t.verdict.losses:
        line += f"; {loss.session} lost to {loss.winner} (comment {loss.comment_id})"
    return f"{line}  {t.title}"


def _post(n: int, body: str) -> int:
    """Post a comment on the issue; its id."""
    return int(_gh(["-X", "POST", f"{REPO}/issues/{n}/comments", "--input", "-"], json.dumps({"body": body}))["id"])


def _withdraw(comment_id: int, me: str, winner: str, winner_claim: int) -> None:
    """Rewrite our own losing claim comment so it no longer reads as a claim."""
    body = (
        f"<!-- ticket-claim:v1 action=withdrawn session={me} -->\n"
        f"Claim by session **{me}** withdrawn: **{winner}** claimed first (comment {winner_claim})."
    )
    _gh(["-X", "PATCH", f"{REPO}/issues/comments/{comment_id}", "--input", "-"], json.dumps({"body": body}))


def claim(n: int, me: str) -> int:
    """Claim the ticket, renew our live claim, or report who holds it."""
    t = read_ticket(n)
    if t.kind != "issue" or t.state != "open":
        print(describe(t, me))
        return NOT_OPEN
    h = t.verdict.holder
    if h and h.session == me:
        if t.now - h.last >= RENEW_AFTER:
            _post(n, f"<!-- ticket-claim:v1 action=renew session={me} claim={h.claim_id} -->\n"
                     f"Session **{me}** is still working this ticket.")
            print(f"#{n} renewed by {me}")
        print(describe(t, me))
        return OK
    if t.taken(me):
        for loss in t.verdict.losses:
            if loss.session == me:
                _withdraw(loss.comment_id, me, loss.winner, loss.winner_claim)
        print(describe(t, me))
        print("another session holds it: pick another ticket" if h else "an open PR closes it: ask the user")
        return TAKEN
    cid = _post(n, (
        f"<!-- ticket-claim:v1 action=claim session={me} -->\n"
        f"Claimed by session **{me}**. Other sessions leave this ticket while the claim is live: it lapses "
        f"{TTL.total_seconds() / 3600:g} hours after the last activity here, "
        f"and an open PR that closes the ticket holds it. "
        f"Check: `python3 -m scripts.ticket_claim status {n}`."
    ))
    time.sleep(SETTLE_SECONDS)
    t = read_ticket(n)
    h = t.verdict.holder
    if h and h.session == me and h.claim_id == cid:
        print(f"#{n} claimed by {me}")
        return OK
    if h:
        _withdraw(cid, me, h.session, h.claim_id)
        who = "an earlier claim by this session name" if h.session == me else h.session
        print(f"#{n} lost the race to {who} (comment {h.claim_id}); claim withdrawn: pick another ticket")
        return TAKEN
    print(describe(t, me))
    if t.kind == "issue" and t.state == "open":
        print(f"claim comment {cid} did not count after {SETTLE_SECONDS} s: run claim again")
        return GH_ERROR
    return NOT_OPEN


def release(n: int, me: str, reason: str) -> int:
    """Release our live claim on the ticket, if we hold one."""
    t = read_ticket(n)
    if t.kind != "issue":
        print(describe(t, me))
        return NOT_OPEN
    h = t.verdict.holder
    if h and h.session == me:
        _post(n, f"<!-- ticket-claim:v1 action=release session={me} claim={h.claim_id} -->\n"
                 f"Released by session **{me}**: {reason}. The ticket is free to claim.")
        print(f"#{n} released by {me}")
    else:
        print(f"nothing to release: {describe(t, me)}")
    return OK


def status(numbers: list[int], me: str | None, mine: bool, frontier: bool) -> int:
    """Print who holds each ticket; exit 1 when any named ticket is taken, 3 when one is not an open issue."""
    code = OK
    for n in numbers:
        t = read_ticket(n)
        print(describe(t, me))
        if t.taken(me):
            code = TAKEN
        elif code == OK and (t.kind != "issue" or t.state != "open"):
            code = NOT_OPEN
    if mine:
        # The local clock only narrows the listing; every verdict below still runs on GitHub's.
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for r in _paged(f"{REPO}/issues?state=open&since={since}"):
            if "pull_request" not in r and r.get("comments"):
                t = read_ticket(r["number"])
                if t.verdict.holder and t.verdict.holder.session == me:
                    print(describe(t, me))
    if frontier:
        for r in _paged(f"{REPO}/issues?state=open&labels=ready-for-agent&sort=created&direction=asc"):
            labels = {lb["name"] for lb in r.get("labels") or []}
            blocked = (r.get("issue_dependencies_summary") or {}).get("blocked_by", 0)
            if "pull_request" in r or "gate" in labels or blocked:
                continue
            t = read_ticket(r["number"])
            if t.verdict.holder is None and not t.open_prs():
                print(describe(t, me))
    return code


def _number(text: str) -> int:
    return int(text.lstrip("#"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, epilog=EXIT_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("claim", help="claim a ticket, or renew our claim; run again before the first push and the PR")
    c.add_argument("number", type=_number)
    r = sub.add_parser("release", help="release our claim when stopping without finishing")
    r.add_argument("number", type=_number)
    r.add_argument("--reason", default="stopped without finishing")
    s = sub.add_parser("status", help="who holds each ticket")
    s.add_argument("numbers", type=_number, nargs="*")
    s.add_argument("--mine", action="store_true", help="open tickets this session holds")
    s.add_argument(
        "--frontier", action="store_true", help="open ready-for-agent tickets, no gate label, no open blocker, claimable"
    )
    for sp in (c, r, s):
        sp.add_argument("--as", dest="session", help="session name (default: this Claude Code session's name)")
    args = p.parse_args(argv)
    me = session_name(args.session)
    if me is None and (args.cmd != "status" or args.mine or args.session is not None):
        print("unknown session name: pass --as <name> (ListAgents prints it)", file=sys.stderr)
        return USAGE
    if args.cmd == "status" and not (args.numbers or args.mine or args.frontier):
        p.error("status needs ticket numbers, --mine or --frontier")
    try:
        if args.cmd == "claim":
            return claim(args.number, me)
        if args.cmd == "release":
            return release(args.number, me, args.reason)
        return status(args.numbers, me, args.mine, args.frontier)
    except GhError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        return GH_ERROR


if __name__ == "__main__":
    sys.exit(main())
