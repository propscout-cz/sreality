"""Ticket claims: which session holds a GitHub issue, judged from marker comments on it."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
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
SID_RE = re.compile(r"[0-9a-f]{8}")
MARKER_RE = re.compile(
    r"<!-- ticket-claim:v1 action=(claim|renew|release|withdrawn) "
    r"session=([a-z0-9][a-z0-9-]{0,39})(?: sid=([0-9a-f]{8}))?(?: claim=\d+)? -->(?:\r?\n|$)"
)
REPO = "repos/{owner}/{repo}"
CLOSING_QUERY = (
    "query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){issue(number:$n)"
    "{closedByPullRequestsReferences(first:20,includeClosedPrs:true){nodes{number state isDraft headRefName}}}}}"
)
OK, TAKEN, USAGE, NOT_OPEN, GH_ERROR = 0, 1, 2, 3, 4
EXIT_HELP = (
    "exit codes: 0 yours or free, 1 another session holds it / claimed first / an open PR from another branch "
    "closes it, 2 usage or unknown session name, 3 closed / a PR / not found, 4 GitHub or other error"
)


class GhError(Exception):
    """A gh call failed."""


@dataclass(frozen=True)
class Session:
    name: str
    sid: str | None = None

    def same(self, other: Session) -> bool:
        """One session: the same id when both carry one (a resumed session gets a new name), else the same name."""
        return self.sid == other.sid if self.sid and other.sid else self.name == other.name

    def mark(self) -> str:
        """The marker's session fields."""
        return f"session={self.name}" + (f" sid={self.sid}" if self.sid else "")


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
    branch: str = ""


@dataclass
class Holder:
    who: Session
    claim_id: int
    since: datetime
    last: datetime
    pinned: bool = False


@dataclass(frozen=True)
class Loss:
    who: Session
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

    def open_prs(self) -> list[ClosingPr]:
        """Open PRs whose closing keyword names this ticket."""
        return [p for p in self.closing if p.state == "OPEN"]

    def taken(self, me: Session | None, branch: str = "") -> bool:
        """`claim` would refuse: another session holds it, or nobody does and another branch's open PR closes it."""
        h = self.verdict.holder
        if self.kind != "issue" or self.state != "open":
            return False
        if h:
            return me is None or not h.who.same(me)
        return any(not branch or p.branch != branch for p in self.open_prs())


def parse_marker(body: str) -> tuple[str, Session] | None:
    """(action, session) from a claim comment's first line, else None."""
    m = MARKER_RE.match(body)
    return (m.group(1), Session(m.group(2), m.group(3))) if m else None


def fold(events: list[Event], now: datetime, closing: list[ClosingPr]) -> Verdict:
    """Replay the timeline: the first claim made while none is live wins, lapsing TTL after its last activity
    unless an open closing PR referenced the ticket before then."""
    closing_numbers = {p.number for p in closing}
    open_numbers = {p.number for p in closing if p.state == "OPEN"}
    pr_holds = False
    v = Verdict()
    for e in sorted(events, key=lambda e: (e.at, e.id)):
        h = v.holder
        if h and not h.pinned and e.at - h.last > TTL:
            v.holder = h = None
        if e.kind == "closed":
            v.holder = None
        elif e.kind == "referenced" and h:
            h.last = e.at
        elif e.kind == "cross-referenced" and e.source in closing_numbers:
            pr_holds = pr_holds or e.source in open_numbers
            if h:
                h.last = e.at
                h.pinned = pr_holds
        elif e.kind == "commented" and e.association in TRUSTED:
            marker = parse_marker(e.body)
            if marker is None:
                continue
            action, who = marker
            if action == "claim" and h is None:
                v.holder = Holder(who, e.id, e.at, e.at, pinned=pr_holds)
            elif action == "claim" and h and not h.who.same(who):
                v.losses.append(Loss(who, e.id, h.who.name, h.claim_id))
            elif action in ("claim", "renew") and h and h.who.same(who):
                h.last = e.at
            elif action == "release" and h and h.who.same(who):
                v.holder = None
    if v.holder and not v.holder.pinned and now - v.holder.last > TTL:
        v.holder = None
    return v


def slug(name: str) -> str:
    """A name in the marker's alphabet: accents dropped, lowercased, each run of other characters one '-', at most 40."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:40]


def claim_name(name: str) -> str | None:
    """The name when a marker accepts it, else its slug (a /rename like 'Github migration sprint'), else None."""
    return next((n for n in (name, slug(name)) if SESSION_RE.fullmatch(n)), None)


def harness_session() -> tuple[str, str] | None:
    """This Claude Code session's raw name and 8-char id prefix, from its sessions file."""
    pid = os.environ.get("CLAUDE_PID", "")
    if not pid.isdigit():
        return None
    try:
        data = json.loads((Path.home() / ".claude" / "sessions" / f"{pid}.json").read_text())
        name, sid = data.get("name"), str(data.get("sessionId") or "")[:8]
    except (OSError, ValueError, AttributeError):
        return None
    return (name, sid) if isinstance(name, str) else None


def session(explicit: str | None) -> Session | None:
    """--as NAME (matched by name alone), else this Claude Code session's name and id; None when no claim name results."""
    if explicit is not None:
        name = claim_name(explicit)
        return Session(name) if name else None
    found = harness_session()
    name = claim_name(found[0]) if found else None
    if found is None or name is None:
        return None
    return Session(name, found[1] if SID_RE.fullmatch(found[1]) else None)


def current_branch() -> str:
    """The branch checked out here, '' when none: an open PR from it is the caller's own."""
    try:
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


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
    return [ClosingPr(p["number"], p["state"], bool(p["isDraft"]), p.get("headRefName") or "") for p in nodes]


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


def describe(t: Ticket, me: Session | None, branch: str = "") -> str:
    """One status line for a ticket."""
    if t.kind == "missing":
        return f"#{t.number} not found"
    if t.kind == "pr":
        return f"#{t.number} is a pull request"
    if t.state != "open":
        return f"#{t.number} closed  {t.title}"
    h, prs = t.verdict.holder, t.open_prs()
    if h:
        you = " (you)" if me and h.who.same(me) else ""
        until = f"held while PR #{prs[0].number} is open" if h.pinned else f"expires {_hm(h.last + TTL)}"
        line = f"#{t.number} held by {h.who.name}{you} since {_hm(h.since)}, last activity {_hm(h.last)}, {until}"
    else:
        line = f"#{t.number} {'unclaimed' if prs else 'free'}"
    for p in prs:
        yours = " (your branch)" if branch and p.branch == branch else ""
        line += f"; {'draft ' if p.draft else ''}PR #{p.number} closes it{yours}"
    for loss in t.verdict.losses:
        line += f"; {loss.who.name} lost to {loss.winner} (comment {loss.comment_id})"
    return f"{line}  {t.title}"


def _post(n: int, body: str) -> int:
    """Post a comment on the issue; its id."""
    return int(_gh(["-X", "POST", f"{REPO}/issues/{n}/comments", "--input", "-"], json.dumps({"body": body}))["id"])


def _withdraw(comment_id: int, me: Session, winner: str, winner_claim: int) -> None:
    """Rewrite our own losing claim comment so it no longer reads as a claim."""
    body = (
        f"<!-- ticket-claim:v1 action=withdrawn {me.mark()} -->\n"
        f"Claim by session **{me.name}** withdrawn: **{winner}** claimed first (comment {winner_claim})."
    )
    _gh(["-X", "PATCH", f"{REPO}/issues/comments/{comment_id}", "--input", "-"], json.dumps({"body": body}))


def claim(n: int, me: Session, branch: str) -> int:
    """Claim the ticket, renew our live claim, or report who holds it."""
    t = read_ticket(n)
    if t.kind != "issue" or t.state != "open":
        print(describe(t, me, branch))
        return NOT_OPEN
    h = t.verdict.holder
    if h and h.who.same(me):
        if t.now - h.last >= RENEW_AFTER:
            _post(n, f"<!-- ticket-claim:v1 action=renew {me.mark()} claim={h.claim_id} -->\n"
                     f"Session **{me.name}** is still working this ticket.")
            print(f"#{n} renewed by {me.name}")
        print(describe(t, me, branch))
        return OK
    if t.taken(me, branch):
        for loss in t.verdict.losses:
            if loss.who.same(me):
                _withdraw(loss.comment_id, me, loss.winner, loss.winner_claim)
        print(describe(t, me, branch))
        print("another session holds it: pick another ticket" if h else "an open PR closes it: ask the user")
        return TAKEN
    cid = _post(n, (
        f"<!-- ticket-claim:v1 action=claim {me.mark()} -->\n"
        f"Claimed by session **{me.name}**. Other sessions leave this ticket while the claim is live: it lapses "
        f"{TTL.total_seconds() / 3600:g} hours after the last activity here, "
        f"and an open PR that closes the ticket holds it. "
        f"Check: `python3 -m scripts.ticket_claim status {n}`."
    ))
    time.sleep(SETTLE_SECONDS)
    t = read_ticket(n)
    h = t.verdict.holder
    if h and h.who.same(me):
        print(f"#{n} claimed by {me.name}" + ("" if h.claim_id == cid else f" (its claim comment {h.claim_id})"))
        return OK
    if h:
        _withdraw(cid, me, h.who.name, h.claim_id)
        print(f"#{n} lost the race to {h.who.name} (comment {h.claim_id}); claim withdrawn: pick another ticket")
        return TAKEN
    print(describe(t, me, branch))
    if t.kind == "issue" and t.state == "open":
        print(f"claim comment {cid} did not count after {SETTLE_SECONDS} s: run claim again")
        return GH_ERROR
    return NOT_OPEN


def release(n: int, me: Session, reason: str) -> int:
    """Release our live claim on the ticket, if we hold one."""
    t = read_ticket(n)
    if t.kind != "issue":
        print(describe(t, me))
        return NOT_OPEN
    h = t.verdict.holder
    if h and h.who.same(me):
        _post(n, f"<!-- ticket-claim:v1 action=release {me.mark()} claim={h.claim_id} -->\n"
                 f"Released by session **{me.name}**: {reason}. The ticket is free to claim.")
        print(f"#{n} released by {me.name}")
    else:
        print(f"nothing to release: {describe(t, me)}")
    return OK


def status(numbers: list[int], me: Session | None, mine: bool, frontier: bool, branch: str) -> int:
    """Print who holds each ticket; exit 1 when any named ticket is taken, 3 when one is not an open issue."""
    code = OK
    for n in numbers:
        t = read_ticket(n)
        print(describe(t, me, branch))
        if t.taken(me, branch):
            code = TAKEN
        elif code == OK and (t.kind != "issue" or t.state != "open"):
            code = NOT_OPEN
    if mine and me:
        # The local clock only narrows the listing; every verdict below still runs on GitHub's.
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for r in _paged(f"{REPO}/issues?state=open&since={since}"):
            if "pull_request" not in r and r.get("comments"):
                t = read_ticket(r["number"])
                if t.verdict.holder and t.verdict.holder.who.same(me):
                    print(describe(t, me, branch))
    if frontier:
        for r in _paged(f"{REPO}/issues?state=open&labels=ready-for-agent&sort=created&direction=asc"):
            if "pull_request" in r or "gate" in {lb["name"] for lb in r.get("labels") or []}:
                continue
            try:
                blocked, children = r["issue_dependencies_summary"]["blocked_by"], r["sub_issues_summary"]["total"]
            except (KeyError, TypeError) as exc:
                raise GhError(f"#{r.get('number')}: the issue listing has no blocker or sub-issue count") from exc
            if blocked or children:
                continue
            t = read_ticket(r["number"])
            if t.verdict.holder is None and not t.open_prs():
                print(describe(t, me, branch))
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
        "--frontier", action="store_true",
        help="open ready-for-agent tickets with no gate label, no sub-issues and no open blocker, claimable",
    )
    for sp in (c, r, s):
        sp.add_argument(
            "--as", dest="session", help="act as this session name, matched by name alone "
            "(default: this Claude Code session's name and id)",
        )
    args = p.parse_args(argv)
    me = session(args.session)
    if me is None and (args.cmd != "status" or args.mine or args.session is not None):
        raw = args.session if args.session is not None else (harness_session() or (None, ""))[0]
        why = f"session name {raw!r} slugs to {slug(raw)!r}" if raw is not None else "no session name found"
        print(f"{why}, not a claim name: pass --as <name> (a-z, 0-9 and '-', at most 40)", file=sys.stderr)
        return USAGE
    if args.cmd == "status" and not (args.numbers or args.mine or args.frontier):
        p.error("status needs ticket numbers, --mine or --frontier")
    try:
        if args.cmd == "claim":
            return claim(args.number, me, current_branch())
        if args.cmd == "release":
            return release(args.number, me, args.reason)
        return status(args.numbers, me, args.mine, args.frontier, current_branch())
    except GhError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        return GH_ERROR
    except Exception as exc:  # exit 1 means "taken", so a crash must not exit 1 as Python's default would
        print(f"error: {exc!r}", file=sys.stderr)
        return GH_ERROR


if __name__ == "__main__":
    sys.exit(main())
