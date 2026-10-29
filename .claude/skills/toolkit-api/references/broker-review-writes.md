# Broker merge-review writes (`api/broker_review.py`, migration 401)

Moved from the toolkit-api skill's rule 5 (byte cap); the rule there points here.

**Broker merge-review writes are durable decisions, not just status flips** (`api/broker_review.py`,
migration 401). The nightly sweep re-derives every merge from scratch — name-gated and
portal-agnostic since 2026-08-20 — so an unmerge used to be re-applied the same night.
`unmerge_group` derives, inside its own transaction and BEFORE the re-point, every cross-owner
identity pair it pulled apart (SAME-PORTAL PAIRS INCLUDED: within-source merging is allowed now,
and the biggest duplicate fans are same-portal) and records it in `broker_merge_suppressions`. Both derivations read a **cohort**, never a remembered id: the
unmerge anchors on where the restored identities live NOW (the survivor on the event rows may itself
have been merged away since — that read returns nothing and the unmerge silently writes zero rows),
and a dismissal anchors on the candidate's BROKER pair, suppressing every cross-owner pair between
them (the card is keyed `contactbridge:{lo}:{hi}` and the evidence is last-write-wins, so sibling
identity pairs would otherwise stay live). Pairs already under one broker are skipped — an active
suppression over co-located identities is an instant `verify_pipeline` violation. A `name_firm`
candidate has no identity evidence: dismiss only. `merge_brokers` LIFTS (never deletes) every active
suppression whose two identities sit under DIFFERENT brokers being merged — an explicit operator
merge always beats the rail, which gates the AUTO path only; `GET /broker-review/suppressions` +
`POST /broker-review/suppressions/{id}/lift` (409 on a re-lift) are the ledger and the manual
override. Every mutating `/broker-review/*` route binds `require_admin`'s claims and threads
`claims.get("email") or claims.get("sub")` into `undone_by` / `resolved_by` / `created_by` /
`lifted_by`.
