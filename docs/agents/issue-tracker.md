# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues on `propscout-cz/sreality`. Use the `gh` CLI for all operations.

**An issue is read by everyone the repo is shared with, for good.** Keep tokens, DB URLs, operator-private rulings and personal data out of issue titles, bodies and comments.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,state,labels,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone.

## Always pass `--json` to a view

In this repo a plain `gh issue view <n>` or `gh pr view <n>` (with or without `--comments`) exits 1 and prints only a "Projects (classic) is being deprecated" GraphQL error: the default field set asks for `projectCards`, which GitHub no longer serves. Naming the fields with `--json` skips that query and works.

`gh pr edit` fails the same way and changes nothing. If an edit exits 1 with that error, use the REST call instead:

- **Add a label**: `gh api -X POST repos/{owner}/{repo}/issues/<n>/labels -f "labels[]=<label>"`
- **Remove a label**: `gh api -X DELETE repos/{owner}/{repo}/issues/<n>/labels/<label>`
- **Edit a body**: `gh api -X PATCH repos/{owner}/{repo}/issues/<n> -F body=@<file>` (`pulls/<n>` for a PR)

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --json number,title,body,state,labels,author,comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, the REST label calls above, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42 --json number` and fall back to `gh issue view 42 --json number`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --json number,title,body,state,labels,comments`.

## Claiming a ticket

Every session posts as the same GitHub account, so an assignee cannot tell sessions apart. A **ticket claim** does: a marker comment naming the session, which `scripts/ticket_claim.py` writes and judges (the first claim made while none is live wins). It is not a lease; in `GLOSSARY.md` a Lease is the engine's lock.

- **Claim before any work on a ticket**, whether you picked it, the user named it or `/implement` / `/implement-spec` reached it: `python3 -m scripts.ticket_claim claim <n>` from the repo root. Exit 0 makes it yours; on any other exit, do what the script prints. A checkout without the script exits 1 with `No module named`: run `python3 <a checkout of main>/scripts/ticket_claim.py` instead.
- **Claim again before your first push and before you open the PR.** This confirms you still hold the ticket and renews an old claim. If it now exits 1, stop and tell the user.
- **A claim lapses 4 hours after the ticket's last activity**: a claim or renewal, a commit naming `#<n>`, a PR that closes `#<n>`. An open PR that closes the ticket holds it, so open the draft PR early. On that PR's branch, `claim` makes a ticket nobody holds yours.
- **A PR says `Closes #<n>` only for tickets you hold.** Before opening one that closes several (the `/implement-spec` integration PR), claim each of them.
- **Release when you stop without finishing**: `python3 -m scripts.ticket_claim release <n> --reason "<why>"`. Closing a ticket ends its claim; a reopened ticket is claimed afresh.
- **A claim belongs to the session id**: subagents share it, so the orchestrating session claims each ticket before handing it over, and a resumed session keeps it under its new name. `--as <name>` acts by name alone, for a session the script cannot identify (exit 2) or to take over a ticket at the user's word.
- The script alone writes claim comments; leave them as written and the assignee empty.
- `status` shows who holds what (`--help`).

## Merging: the merge light

Railway deploys every merge to `main`, so merges go one PR at a time, each on its own light from the **merge coordinator**. That is a role, not a name: the session that owns the autodedup engine and runs merge windows. Its session name changes on every resume, so find it with `ListAgents` or ask the operator; never message a remembered name.

- **Ask for the light** ("green light #<n>") with the PR number, the head SHA, its CI state, and whether the PR adds a migration or touches `autodedup.incremental.DECISION_MODULES`, the apply scope (`app_settings.autodedup_apply_scope`), `.railway/` or the worker's config. Those may need an engine pause; the coordinator decides and runs it.
- **Claim a migration number** with the coordinator before writing the file, and never apply a migration without telling it.
- **Windows:** the coordinator may close backend merge windows, since every backend path rebuilds the job-runner. Don't assume a slot.
- **On the light**, merge current `origin/main` into the branch and wait for CI green on that head: confirm `gh pr view <n> --json headRefOid -q .headRefOid` is the SHA you pushed, then, once its checks have registered, `gh pr checks <n> --watch --fail-fast --interval 30` (10-minute timeout, or in the background). Then run, each as a standalone command (inside a compound command it can fall to the permission classifier), `gh pr ready <n>` (GitHub never merges a draft) and `gh pr merge <n> --merge --match-head-commit <sha>`, where `<sha>` is the full head SHA CI just passed, not the one in your light request. Send the coordinator the merge SHA and wait for its "live" before the next merge.
- **If the permission check refuses the merge**, tell the operator in your own session. Never ask another session to merge for you.

## Post-merge gates are their own tickets

A **gate** is a check that can pass only some time after a deploy: a lane green for 48 hours, a p95 within 10 % over seven days. Each gate is its own ticket, so the build PR's `Closes #<n>` is always right and the owed gates are one query.

**Drafting tickets** (`/to-tickets`, `/to-spec`, `/wayfinder`): put each gate ticket right after its build ticket, one per build and window.

- **Title:** `Gate: <what must hold> after #<build> (due <N> after deploy)`; when its clock starts, `(due YYYY-MM-DD)`.
- **Label:** `gate` alone, which keeps it off the `ready-for-agent` frontier.
- **Links:** a sub-issue of the spec; natively blocked by the build ticket; natively blocking only the tickets that need the gate passed (a later wave's cutover, a contract step), never those that need only the build's code.
- **Body:**

```markdown
## Gate for
#<build>: <build title>. Closed by a measurement comment.
## Clock
Starts when #<build>'s PR deploys. Window: <48 hours | 72 hours | 7 days>. Due: not started.
## Baseline
<the numbers to compare against, or where the build PR records them>
## Pass when
- [ ] <read-only check>
## How to measure
<the Health cell, SELECT or gh command; read-only>
## If it fails
Comment the numbers, leave this open and open a fix ticket that blocks this one; the clock restarts when the fix deploys.
## Blocked by
- #<build>
```

**Merging a build PR**: once the deploy is confirmed (CLAUDE.md, "A merge is not a deploy"), start the clock on each gate ticket the build ticket blocks, listed by `gh api repos/{owner}/{repo}/issues/<build>/dependencies/blocking -q '.[] | select(any(.labels[]; .name == "gate")) | .number'`: comment `Clock started: deployed <UTC>; due <UTC>. Baseline: <numbers>`, then date the title with `gh api -X PATCH repos/{owner}/{repo}/issues/<gate> -f title="…(due YYYY-MM-DD)"`.

**Owed gates**, soonest first: `gh issue list --label gate --state open --json number,title -q '.[] | "\((.title | capture("due (?<d>[0-9]{4}-[0-9]{2}-[0-9]{2})")).d // "not started")  #\(.number)  \(.title)"' | sort`

**A due gate** is measured by any session, unclaimed (measuring twice is harmless). Pass: comment the numbers and `gh issue close <gate>`. Fail: follow its "If it fails".

## How work closes

This is what `/implement-spec` means by "the way the issue tracker closes work", and every PR follows it.

- **Build ticket**: its PR body says `Closes #<n>`. A ticket of the agent platform (spec #2055) also flips its `done` in `frontend/src/lib/agentPlatformMap.ts`, the admin Agent platform page, in that PR (CI warns when it doesn't).
- **Gate ticket**: a measurement comment, then `gh issue close`; a PR touching it says `Refs #<gate>`.
- **Spec**: its tickets, gates included, are its sub-issues, which keeps it off `status --frontier`. It closes after its last child. While a gate ticket under it is open, a PR says `Refs #<spec>`, and whoever closes the last gate closes the spec.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`).
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or a live claim (`python3 -m scripts.ticket_claim status <n>` exits 1); first in map order wins.
- **Claim**: `python3 -m scripts.ticket_claim claim <n>`, the session's first write (§ Claiming a ticket).
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
