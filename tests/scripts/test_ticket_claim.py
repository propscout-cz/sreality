"""Ticket claims, offline: the fold rule, session names and the claim flow against a fake gh."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import ticket_claim as tc

T0 = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
HOUR = 3600


@pytest.fixture(autouse=True)
def no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_PID", raising=False)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def marker(action: str, session: str, claim: int | None = None, sid: str | None = None) -> str:
    tail = (f" sid={sid}" if sid else "") + (f" claim={claim}" if claim is not None else "")
    return f"<!-- ticket-claim:v1 action={action} session={session}{tail} -->\nhuman text"


def said(
    cid: int, session: str, action: str = "claim", t: float = 0, assoc: str = "OWNER", sid: str | None = None
) -> tc.Event:
    return tc.Event("commented", at(t), cid, marker(action, session, sid=sid), assoc)


def pr(number: int, state: str = "OPEN") -> tc.ClosingPr:
    return tc.ClosingPr(number, state, draft=True)


def xref(source: int, t: float) -> tc.Event:
    return tc.Event("cross-referenced", at(t), source=source)


def holder(events: list[tc.Event], now: float, closing: list[tc.ClosingPr] | None = None) -> str | None:
    h = tc.fold(events, at(now), closing or []).holder
    return h.who.name if h else None


def test_marker_is_the_first_line_only() -> None:
    s82 = tc.Session("sreality-82")
    assert tc.parse_marker(marker("claim", "sreality-82")) == ("claim", s82)
    assert tc.parse_marker(marker("renew", "sreality-82", 7).replace("\n", "\r\n")) == ("renew", s82)
    assert tc.parse_marker(marker("renew", "sreality-82", 7, "81a8cddd")) == ("renew", tc.Session("sreality-82", "81a8cddd"))
    assert tc.parse_marker("<!-- ticket-claim:v1 action=release session=s1 claim=9 -->") == ("release", tc.Session("s1"))
    assert tc.parse_marker(marker("claim", "sreality-82", sid="81A8CDDD")) is None
    assert tc.parse_marker("hello\n" + marker("claim", "sreality-82")) is None
    assert tc.parse_marker(marker("claim", "Sreality-82")) is None
    assert tc.parse_marker(marker("steal", "sreality-82")) is None
    assert tc.parse_marker("<!-- ticket-claim:v1 action=claim session=s1 --> trailing") is None
    assert tc.parse_marker("<!-- ticket-claim:v2 action=claim session=s1 -->") is None


def test_a_same_second_race_is_decided_by_the_lower_comment_id() -> None:
    v = tc.fold([said(101, "sreality-0f"), said(100, "sreality-82")], at(20), [])
    assert v.holder is not None and (v.holder.who.name, v.holder.claim_id) == ("sreality-82", 100)
    assert v.losses == [tc.Loss(tc.Session("sreality-0f"), 101, "sreality-82", 100)]


def test_a_lapsed_claim_is_taken_over_and_its_late_renew_ignored() -> None:
    events = [said(100, "a"), said(200, "b", t=5 * HOUR), said(201, "a", "renew", t=5 * HOUR + 1)]
    v = tc.fold(events, at(5 * HOUR + 60), [])
    assert v.holder is not None and v.holder.who.name == "b" and v.losses == []


def test_a_claim_lapses_after_ttl_without_activity() -> None:
    assert holder([said(100, "a")], 4 * HOUR) == "a"
    assert holder([said(100, "a")], 4 * HOUR + 1) is None


def test_only_trusted_associations_count() -> None:
    for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"):
        assert holder([said(50, "evil", assoc=assoc), said(100, "a", t=1)], 20) == "a"
    assert holder([said(50, "b", assoc="MEMBER"), said(100, "a", t=1)], 20) == "b"


def test_release_frees_the_ticket_and_the_loser_does_not_inherit() -> None:
    events = [said(100, "a"), said(101, "b"), said(102, "a", "release", t=60)]
    v = tc.fold(events, at(120), [])
    assert v.holder is None and [loss.who.name for loss in v.losses] == ["b"]
    assert holder([*events, said(103, "b", t=90)], 120) == "b"


def test_only_the_holder_renews_or_releases() -> None:
    assert holder([said(100, "a"), said(101, "b", "release", t=60)], 120) == "a"
    assert holder([said(100, "a"), said(102, "b", "renew", t=3 * HOUR)], 4 * HOUR + 1) is None


def test_closed_ends_every_claim_and_a_reopen_needs_a_new_claim() -> None:
    closed = tc.Event("closed", at(60))
    reopened = tc.Event("reopened", at(120))
    assert holder([said(100, "a"), closed, reopened], 180) is None
    assert holder([said(100, "a"), closed, reopened, said(300, "b", t=150)], 180) == "b"


def test_a_commit_reference_extends_the_claim() -> None:
    events = [said(100, "a"), tc.Event("referenced", at(3 * HOUR), 5)]
    assert holder(events, 6 * HOUR) == "a"
    assert holder(events, 7 * HOUR + 1) is None


def test_only_a_closing_pr_cross_reference_extends_the_claim() -> None:
    xref_issue = tc.Event("cross-referenced", at(3 * HOUR), source=2024)
    assert holder([said(100, "a"), xref_issue], 4 * HOUR + 1800, [pr(2044, "MERGED")]) is None
    xref_pr = tc.Event("cross-referenced", at(3 * HOUR), source=2044)
    assert holder([said(100, "a"), xref_pr], 4 * HOUR + 1800, [pr(2044, "CLOSED")]) == "a"


def test_an_open_closing_pr_holds_past_the_ttl() -> None:
    assert holder([said(100, "a"), xref(2044, HOUR)], 9 * HOUR, [pr(2044)]) == "a"
    assert holder([said(100, "a"), xref(2044, HOUR)], 9 * HOUR, [pr(2044, "CLOSED")]) is None


def test_an_open_closing_pr_holds_against_a_later_claim() -> None:
    events = [said(100, "a"), xref(2044, HOUR), said(300, "b", t=6 * HOUR)]
    v = tc.fold(events, at(6 * HOUR + 60), [pr(2044)])
    assert v.holder is not None and v.holder.who.name == "a"
    assert [loss.who.name for loss in v.losses] == ["b"]
    assert holder(events, 6 * HOUR + 60, [pr(2044, "CLOSED")]) == "b"


def test_an_open_pr_holds_only_a_claim_it_referenced_in_time_and_a_later_event_agrees() -> None:
    assert holder([said(100, "a")], 9 * HOUR, [pr(7)]) is None
    assert holder([said(100, "a"), tc.Event("labeled", at(5 * HOUR))], 9 * HOUR, [pr(7)]) is None
    assert holder([said(100, "a"), xref(7, 5 * HOUR)], 9 * HOUR, [pr(7)]) is None
    assert holder([said(100, "a"), said(200, "b", t=6 * HOUR)], 9 * HOUR, [pr(7)]) == "b"


def test_a_claim_made_after_an_open_pr_referenced_the_ticket_is_held_by_it() -> None:
    assert holder([xref(7, 0), said(100, "a", t=60)], 9 * HOUR, [pr(7)]) == "a"
    assert holder([xref(7, 0), said(100, "a", t=60)], 9 * HOUR, [pr(7, "MERGED")]) is None


def test_a_resumed_session_keeps_its_claim_under_a_new_name_and_a_reused_name_does_not() -> None:
    events = [said(100, "sreality-0f", sid="e6b78d33"), said(101, "sreality-0f", sid="2429f40d", t=60)]
    v = tc.fold([*events, said(102, "sreality-e9", "renew", t=3 * HOUR, sid="e6b78d33")], at(6 * HOUR), [])
    assert v.holder is not None and v.holder.who == tc.Session("sreality-0f", "e6b78d33")
    assert v.losses == [tc.Loss(tc.Session("sreality-0f", "2429f40d"), 101, "sreality-0f", 100)]
    assert holder([*events, said(102, "sreality-e9", "release", t=60, sid="e6b78d33")], 120) is None
    assert holder([*events, said(102, "sreality-0f", "release", t=60)], 120) is None


def test_two_sessions_are_one_by_id_when_both_carry_one_else_by_name() -> None:
    assert tc.Session("a", "11111111").same(tc.Session("b", "11111111"))
    assert not tc.Session("a", "11111111").same(tc.Session("a", "22222222"))
    assert tc.Session("a", "11111111").same(tc.Session("a"))
    assert not tc.Session("a").same(tc.Session("b", "11111111"))


def test_a_withdrawn_claim_does_not_count() -> None:
    assert holder([said(100, "a", "withdrawn"), said(101, "b", t=1)], 20) == "b"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PID", "4242")
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    return tmp_path / ".claude" / "sessions" / "4242.json"


def test_the_session_comes_from_as_then_the_sessions_file(home: Path) -> None:
    assert tc.session(None) is None
    home.write_text(json.dumps({"name": "sreality-82", "sessionId": "81a8cddd-f73f-4868-bd34-8958ac350b5d"}))
    assert tc.session(None) == tc.Session("sreality-82", "81a8cddd")
    assert tc.session("sreality-0f") == tc.Session("sreality-0f")
    assert tc.session("Not A Name") == tc.Session("not-a-name")
    assert tc.session("!!!") is None
    home.write_text(json.dumps({"name": "sreality-82"}))
    assert tc.session(None) == tc.Session("sreality-82")
    home.write_text(json.dumps({"name": "Github migration sprint", "sessionId": "4c3c569a-ed2d-49b7-8e48-bafb566d112b"}))
    assert tc.session(None) == tc.Session("github-migration-sprint", "4c3c569a")
    home.write_text(json.dumps({"name": "***", "sessionId": "81a8cddd"}))
    assert tc.session(None) is None
    for text in ("not json", "[]"):
        home.write_text(text)
        assert tc.session(None) is None


def test_an_unknown_session_exits_2_before_any_github_call(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_gh(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        raise AssertionError(args)

    monkeypatch.setattr(tc, "_run", no_gh)
    assert tc.main(["claim", "5"]) == tc.USAGE
    assert tc.main(["release", "5"]) == tc.USAGE
    assert tc.main(["status", "--mine"]) == tc.USAGE
    assert tc.main(["claim", "5", "--as", "!!!"]) == tc.USAGE


def test_a_session_name_is_slugified_only_when_a_marker_rejects_it(
    home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert tc.slug("Příprava -- Sprint 2/B") == "priprava-sprint-2-b"
    assert tc.slug("x" * 50) == "x" * 40
    assert tc.claim_name("a--b") == "a--b"
    assert tc.claim_name("Github migration sprint") == "github-migration-sprint"
    home.write_text(json.dumps({"name": "日本", "sessionId": "81a8cddd"}))
    assert tc.main(["claim", "5"]) == tc.USAGE
    assert "session name '日本' slugs to ''" in capsys.readouterr().err


class FakeGitHub:
    """Just enough of `gh api` for one issue: the issue with a Date header, its timeline, closing PRs, comment writes."""

    def __init__(self, state: str = "open", kind: str = "issue") -> None:
        self.now = T0
        self.state = state
        self.kind = kind
        self.timeline: list[dict] = []
        self.closing: list[dict] = []
        self.listing: list[dict] = []
        self.next_id = 1000
        self.posts: list[str] = []
        self.patches: dict[int, str] = {}
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[], None] | None = None

    def comment(self, body: str, t: datetime | None = None, cid: int | None = None) -> int:
        cid = cid if cid is not None else self.next_id
        self.next_id = max(self.next_id, cid) + 1
        self.timeline.append({
            "event": "commented", "id": cid, "created_at": (t or self.now).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author_association": "OWNER", "body": body,
        })
        return cid

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep:
            self.on_sleep()
        self.now += timedelta(seconds=seconds)

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        assert args[0] == "api"
        date = format_datetime(self.now, usegmt=True)
        if args[1] == "--include":
            if self.kind == "missing":
                return 1, f"HTTP/2.0 404 Not Found\nDate: {date}\n\n{{}}", "gh: Not Found (HTTP 404)"
            issue: dict = {"number": 5, "state": self.state, "title": "W9: a ticket", "labels": [{"name": "ready-for-agent"}]}
            if self.kind == "pr":
                issue["pull_request"] = {"url": "x"}
            return 0, f"HTTP/2.0 200 OK\r\nDate: {date}\r\n\r\n{json.dumps(issue)}", ""
        if args[1] == "graphql":
            nodes = {"nodes": self.closing}
            return 0, json.dumps({"data": {"repository": {"issue": {"closedByPullRequestsReferences": nodes}}}}), ""
        if "/timeline?" in args[1] or "/issues?" in args[1]:
            page = int(parse_qs(urlsplit(args[1]).query)["page"][0])
            rows = self.timeline if "/timeline?" in args[1] else self.listing
            return 0, json.dumps(rows[(page - 1) * 100: page * 100]), ""
        if args[1:3] == ["-X", "POST"]:
            body = json.loads(stdin or "")["body"]
            self.posts.append(body)
            return 0, json.dumps({"id": self.comment(body)}), ""
        if args[1:3] == ["-X", "PATCH"]:
            cid = int(args[3].rsplit("/", 1)[1])
            self.patches[cid] = json.loads(stdin or "")["body"]
            for row in self.timeline:
                if row.get("id") == cid:
                    row["body"] = self.patches[cid]
            return 0, "{}", ""
        raise AssertionError(args)


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(tc, "_run", fake)
    monkeypatch.setattr(tc.time, "sleep", fake.sleep)
    monkeypatch.setattr(tc, "current_branch", lambda: "feature/mine")
    return fake


def open_pr(branch: str) -> dict:
    return {"number": 2052, "state": "OPEN", "isDraft": True, "headRefName": branch}


def test_claiming_a_free_ticket_posts_settles_and_wins(gh: FakeGitHub) -> None:
    assert tc.main(["claim", "#5", "--as", "sreality-82"]) == tc.OK
    assert gh.sleeps == [tc.SETTLE_SECONDS]
    assert len(gh.posts) == 1 and tc.parse_marker(gh.posts[0]) == ("claim", tc.Session("sreality-82"))
    assert gh.patches == {}


def test_a_rival_with_a_lower_id_wins_the_settle_and_we_withdraw(gh: FakeGitHub) -> None:
    gh.next_id = 500
    gh.on_sleep = lambda: gh.comment(marker("claim", "sreality-0f"), t=T0, cid=499)
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.TAKEN
    assert list(gh.patches) == [500]
    assert tc.parse_marker(gh.patches[500]) == ("withdrawn", tc.Session("sreality-82"))
    assert "**sreality-0f** claimed first (comment 499)" in gh.patches[500]


def test_an_earlier_claim_under_a_reused_name_wins_the_settle(gh: FakeGitHub, home: Path) -> None:
    home.write_text(json.dumps({"name": "sreality-0f", "sessionId": "f311fba6-0000"}))
    gh.next_id = 500
    gh.on_sleep = lambda: gh.comment(marker("claim", "sreality-0f", sid="e6b78d33"), t=T0, cid=499)
    assert tc.main(["claim", "5"]) == tc.TAKEN
    assert list(gh.patches) == [500]
    assert tc.parse_marker(gh.posts[0]) == ("claim", tc.Session("sreality-0f", "f311fba6"))


def test_a_claim_that_never_shows_up_asks_for_a_rerun_which_keeps_the_first_claim(gh: FakeGitHub) -> None:
    hidden: list[dict] = []

    def hide() -> None:
        hidden.extend(gh.timeline)
        gh.timeline.clear()

    gh.on_sleep = hide
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.GH_ERROR
    gh.on_sleep = lambda: gh.timeline.extend(hidden)
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.OK
    assert len(gh.posts) == 2 and gh.patches == {}


def test_a_ticket_held_by_another_session_is_refused_without_a_write(gh: FakeGitHub) -> None:
    gh.comment(marker("claim", "sreality-0f"))
    gh.now += timedelta(hours=1)
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.TAKEN
    assert gh.posts == [] and gh.sleeps == []
    assert tc.main(["status", "5", "--as", "sreality-82"]) == tc.TAKEN
    assert tc.main(["status", "5", "--as", "sreality-0f"]) == tc.OK


def test_a_ticket_an_open_pr_from_another_branch_closes_is_refused(
    gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.closing = [open_pr("feature/other")]
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.TAKEN
    assert gh.posts == []
    assert tc.main(["status", "5"]) == tc.TAKEN
    assert "#5 unclaimed; draft PR #2052 closes it  " in capsys.readouterr().out


def test_claim_on_the_branch_of_the_open_pr_adopts_the_ticket_and_the_pr_holds_it(
    gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.closing = [open_pr("feature/mine")]
    gh.timeline.append(
        {"event": "cross-referenced", "created_at": "2026-10-26T09:00:00Z", "source": {"issue": {"number": 2052}}}
    )
    assert tc.main(["status", "5"]) == tc.OK
    assert "#5 unclaimed; draft PR #2052 closes it (your branch)" in capsys.readouterr().out
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.OK
    assert len(gh.posts) == 1 and gh.patches == {}
    gh.now += timedelta(hours=9)
    assert tc.main(["status", "5", "--as", "sreality-0f"]) == tc.TAKEN
    assert "held by sreality-82 since 2026-10-26 10:00Z" in capsys.readouterr().out


def test_a_rerun_withdraws_our_claim_that_lost(gh: FakeGitHub) -> None:
    winner = gh.comment(marker("claim", "sreality-0f"))
    ours = gh.comment(marker("claim", "sreality-82"))
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.TAKEN
    assert list(gh.patches) == [ours] and f"(comment {winner})" in gh.patches[ours]


def test_the_holder_renews_only_after_two_hours(gh: FakeGitHub) -> None:
    cid = gh.comment(marker("claim", "sreality-82"))
    gh.now += tc.RENEW_AFTER - timedelta(minutes=1)
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.OK
    assert gh.posts == []
    gh.now += timedelta(minutes=1)
    assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.OK
    assert len(gh.posts) == 1 and gh.posts[0].startswith(f"<!-- ticket-claim:v1 action=renew session=sreality-82 claim={cid} -->")
    assert gh.sleeps == []


def test_a_closed_ticket_a_pr_or_a_missing_number_exits_3(gh: FakeGitHub) -> None:
    for state, kind in (("closed", "issue"), ("open", "pr"), ("open", "missing")):
        gh.state, gh.kind = state, kind
        assert tc.main(["claim", "5", "--as", "sreality-82"]) == tc.NOT_OPEN
        assert tc.main(["status", "5"]) == tc.NOT_OPEN
    assert gh.posts == []


def test_release_posts_only_for_the_holder(gh: FakeGitHub) -> None:
    gh.comment(marker("claim", "sreality-0f"))
    assert tc.main(["release", "5", "--as", "sreality-82"]) == tc.OK
    assert gh.posts == []
    assert tc.main(["release", "5", "--as", "sreality-0f", "--reason", "blocked on a ruling"]) == tc.OK
    assert tc.parse_marker(gh.posts[0]) == ("release", tc.Session("sreality-0f")) and "blocked on a ruling" in gh.posts[0]
    assert tc.main(["status", "5", "--as", "sreality-82"]) == tc.OK


def test_the_timeline_is_read_past_its_first_page(gh: FakeGitHub) -> None:
    for i in range(150):
        gh.timeline.append({"event": "labeled", "id": 10 + i, "created_at": "2026-10-26T09:00:00Z"})
    gh.comment(marker("claim", "sreality-0f"))
    assert tc.main(["status", "5"]) == tc.TAKEN


def test_a_github_failure_or_a_crash_exits_4_never_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_run", lambda args, stdin=None: (1, "", "gh: Bad Gateway (HTTP 502)"))
    assert tc.main(["status", "5"]) == tc.GH_ERROR

    def no_gh(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(tc, "_run", no_gh)
    assert tc.main(["status", "5"]) == tc.GH_ERROR


def listed(number: int, blocked: int = 0, children: int = 0, labels: tuple[str, ...] = ("ready-for-agent",)) -> dict:
    return {
        "number": number, "labels": [{"name": name} for name in labels],
        "issue_dependencies_summary": {"blocked_by": blocked}, "sub_issues_summary": {"total": children},
    }


def test_the_frontier_lists_free_unblocked_leaf_tickets_only(gh: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    gh.listing = [listed(2007, children=29), listed(8, blocked=1), listed(9, labels=("gate",)), listed(5)]
    assert tc.main(["status", "--frontier"]) == tc.OK
    assert capsys.readouterr().out.splitlines() == ["#5 free  W9: a ticket"]


def test_the_frontier_fails_closed_without_the_blocker_count(gh: FakeGitHub) -> None:
    row = listed(5)
    del row["issue_dependencies_summary"]
    gh.listing = [row]
    assert tc.main(["status", "--frontier"]) == tc.GH_ERROR
