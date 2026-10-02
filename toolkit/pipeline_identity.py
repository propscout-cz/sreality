"""Carry the single-valued deal-pipeline card across a property merge and a detach, by its
identity (`property_pipeline.card_id`, migration 582).

These are the `Pipeline` carrier's implementation (`toolkit.property_carriers`), run inside
the merge/detach transactions. One card per property per account (rule 22), so a merge where
both sides hold one keeps ONE deal and parks the other:

  - on merge: snapshot BOTH sides' cards, each with its card_id, to the append-only
    `property_pipeline_events` ledger (`merge_absorb`). A retired card moves onto a survivor
    holding none for that account, row and card_id with it. Where both hold one, the
    MOST-ADVANCED deal stays (TERMINAL-AWARE: a live stage always beats a closed one; within
    the same terminality the higher position wins, tie -> later updated_at, then the
    survivor): the winning ROW ends on the survivor, so its card_id, stage and dates stay its
    own; the losing deal is PARKED: its row is deleted and its snapshot is its record.
  - on detach, when an advert's return reactivates the retired property R: the card R held at
    the merge that retired it (its snapshot at `undo[0]`) comes home BY ITS card_id. Live
    anywhere -> its row moves to R with its current stage (progress travels). Not live
    (parked, or ended by the operator) -> it is re-inserted on R at its last logged stage.
    Skipped where a later detach already sent it home to a property that held it BEFORE R did
    (that send-home's merge snapshot is older than R's): the older home keeps it, while a
    survivor it only passed through after R gives it up. Then R and each property a card
    left, if it holds no card for that account, take back the most advanced deal PARKED on
    it or on a property a standing merge folded into it. A live card placed on a property
    lifts that account's dismissal of it (`api.dismissals`' rule; Dismissals' own detach runs
    before this one and does nothing).
  - a merge snapshotted before migration 582 (card_id NULL) is detached as before: its snapshot
    is restored, and on one hop the survivor's absorbed card dropped.

Logged with the card_id: a send-home `unmerge_restore` twice (leaving: from stage only;
arriving: to stage only), a re-insert `unmerge_restore` (arriving), a put-back `split_carry`.

Every predicate over an account's rows names the account. That is the third shape of the
tenancy doctrine and the only place it is legal: this runs as service-role (BYPASSRLS), so the
predicate is the sole gate. Doctrine + the other three shapes:
`.claude/skills/database/references/tenancy.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import psycopg

if TYPE_CHECKING:  # property_carriers imports this module at runtime
    from toolkit.property_carriers import Hop

# (0) snapshot BOTH sides' cards, with their identity, so a detach can find each deal.
_SNAPSHOT_SQL = (
    "INSERT INTO property_pipeline_events "
    "  (account_id, property_id, to_stage_id, reason, merge_group_id, note_snapshot, card_id) "
    "SELECT account_id, property_id, stage_id, 'merge_absorb', %(g)s, note, card_id "
    "FROM property_pipeline WHERE property_id IN (%(r)s, %(s)s)"
)

# (1) survivor has no card FOR THAT ACCOUNT -> the retired card (row and card_id) moves over.
_MOVE_IF_EMPTY_SQL = (
    "UPDATE property_pipeline SET property_id = %(s)s "
    "WHERE property_id = %(r)s "
    "AND NOT EXISTS (SELECT 1 FROM property_pipeline s2 "
    "  WHERE s2.property_id = %(s)s "
    "  AND s2.account_id IS NOT DISTINCT FROM property_pipeline.account_id)"
)

# (2) both sides hold a card and the retired one is more advanced (terminal-aware) -> the
#     survivor's is parked (deleted; snapshotted in (0)) and the retired ROW moves onto the
#     survivor in the same statement, its card_id, stage, dates and note with it (the parked
#     note lives in its snapshot). The PK slot the DELETE frees is reusable within the
#     statement: a row this transaction deleted is dead to the unique check.
_RETIRED_WINS_SQL = """
WITH beaten AS (
    DELETE FROM property_pipeline s
    USING property_pipeline r, pipeline_stages ss, pipeline_stages rs
    WHERE s.property_id = %(s)s AND r.property_id = %(r)s
      AND r.account_id IS NOT DISTINCT FROM s.account_id
      AND ss.id = s.stage_id AND ss.account_id IS NOT DISTINCT FROM s.account_id
      AND rs.id = r.stage_id AND rs.account_id IS NOT DISTINCT FROM r.account_id
      AND ((NOT rs.is_terminal AND ss.is_terminal)
           OR (rs.is_terminal = ss.is_terminal
               AND (rs.position > ss.position
                    OR (rs.position = ss.position AND r.updated_at > s.updated_at))))
    RETURNING s.account_id
)
UPDATE property_pipeline r
SET property_id = %(s)s, updated_at = now()
FROM beaten b
WHERE r.property_id = %(r)s AND r.account_id IS NOT DISTINCT FROM b.account_id
"""

# (3) the retired cards still there lost to the survivor's: parked (their snapshot is in (0)).
_DROP_RETIRED_SQL = "DELETE FROM property_pipeline WHERE property_id = %(r)s"

# Detach (a0): lock the cards R held at that merge wherever they live now, in a statement of
# their own: an API move/remove or a merge on a property this detach did not lock finishes
# first, and (a)'s fresh READ COMMITTED snapshot sees what it did (`Dispatches.LOCK_SQL`).
_LOCK_OWN_SQL = (
    "SELECT pp.card_id FROM property_pipeline pp "
    "JOIN property_pipeline_events e ON e.card_id = pp.card_id AND e.account_id = pp.account_id "
    "WHERE e.merge_group_id = %(g)s AND e.reason = 'merge_absorb' AND e.property_id = %(r)s "
    "ORDER BY pp.card_id FOR UPDATE OF pp"
)

# Detach (a): the card R held at the merge that retired it comes home by its card_id. Live ->
# its row moves (stage, dates, board place travel); not live -> re-inserted at the stage of
# its newest event (a removal logs the stage it ended at) with its newest snapshot's note.
# Skipped where a later send-home (h) put it on another property whose claim is OLDER than
# R's: the snapshot that send-home answered (c, its merge_absorb on h's property in h's group)
# precedes R's (e). A send-home to a survivor it reached after R (the engine undoes a chain one
# group at a time, newest first) never blocks R. Returns each (account_id, property_id) the
# card was last on, for (b).
_SEND_HOME_SQL = """
WITH own AS (
    SELECT e.account_id, e.card_id FROM property_pipeline_events e
    WHERE e.merge_group_id = %(g)s::uuid AND e.reason = 'merge_absorb'
      AND e.property_id = %(r)s::bigint AND e.card_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM property_pipeline_events h
        JOIN property_pipeline_events c
          ON c.card_id = h.card_id AND c.account_id = h.account_id
         AND c.merge_group_id = h.merge_group_id AND c.reason = 'merge_absorb'
         AND c.property_id = h.property_id
        WHERE h.card_id = e.card_id AND h.account_id = e.account_id AND h.id > e.id
          AND h.reason = 'unmerge_restore' AND h.to_stage_id IS NOT NULL
          AND h.property_id <> %(r)s::bigint AND c.id < e.id)
), live AS (
    SELECT o.account_id, o.card_id, c.property_id AS was FROM own o
    JOIN property_pipeline c ON c.card_id = o.card_id AND c.account_id = o.account_id
), moved AS (
    UPDATE property_pipeline pp SET property_id = %(r)s::bigint
    FROM live l
    WHERE pp.card_id = l.card_id AND pp.account_id = l.account_id
      AND NOT EXISTS (SELECT 1 FROM property_pipeline c
                      WHERE c.property_id = %(r)s::bigint AND c.account_id = l.account_id)
    RETURNING pp.account_id, pp.card_id, pp.stage_id, pp.note, l.was
), gone AS (
    SELECT o.account_id, o.card_id, last.stage_id, snap.note_snapshot, last.property_id AS was
    FROM own o
    CROSS JOIN LATERAL (
        SELECT COALESCE(l.to_stage_id, l.from_stage_id) AS stage_id, l.property_id
        FROM property_pipeline_events l
        WHERE l.card_id = o.card_id AND l.account_id = o.account_id
        ORDER BY l.id DESC LIMIT 1) last
    CROSS JOIN LATERAL (
        SELECT n.note_snapshot FROM property_pipeline_events n
        WHERE n.card_id = o.card_id AND n.account_id = o.account_id
          AND n.reason = 'merge_absorb'
        ORDER BY n.id DESC LIMIT 1) snap
    WHERE NOT EXISTS (SELECT 1 FROM property_pipeline c
                      WHERE c.card_id = o.card_id AND c.account_id = o.account_id)
      AND NOT EXISTS (SELECT 1 FROM property_pipeline c
                      WHERE c.property_id = %(r)s::bigint AND c.account_id = o.account_id)
), restored AS (
    INSERT INTO property_pipeline (account_id, property_id, stage_id, note, card_id)
    SELECT account_id, %(r)s::bigint, stage_id, note_snapshot, card_id FROM gone
    RETURNING account_id, card_id, stage_id, note
), logged AS (
    INSERT INTO property_pipeline_events
        (account_id, property_id, from_stage_id, to_stage_id, reason, merge_group_id,
         note_snapshot, card_id)
    SELECT account_id, was, stage_id, NULL::bigint, 'unmerge_restore', %(g)s::uuid, NULL::text,
           card_id FROM moved
    UNION ALL
    SELECT account_id, %(r)s::bigint, NULL::bigint, stage_id, 'unmerge_restore', %(g)s::uuid,
           note, card_id FROM moved
    UNION ALL
    SELECT account_id, %(r)s::bigint, NULL::bigint, stage_id, 'unmerge_restore', %(g)s::uuid,
           note, card_id FROM restored
)
SELECT account_id, was FROM moved
UNION
SELECT g.account_id, g.was FROM gone g JOIN restored USING (account_id, card_id)
WHERE g.was <> %(r)s::bigint
"""

# Before migration 582 (snapshot card_id NULL): restore per (account_id, property_id).
_RESTORE_SQL = (
    "INSERT INTO property_pipeline (account_id, property_id, stage_id, note) "
    "SELECT e.account_id, e.property_id, e.to_stage_id, e.note_snapshot "
    "FROM property_pipeline_events e "
    "WHERE e.merge_group_id = %(g)s AND e.reason = 'merge_absorb' "
    "  AND e.property_id = %(r)s AND e.to_stage_id IS NOT NULL AND e.card_id IS NULL "
    "ON CONFLICT DO NOTHING"
)

# Detach (b): R (any account) and each (account, property) (a) returned, when active and
# holding no card for that account, take back the most advanced (terminal-aware) deal PARKED
# on it, or on a property a standing merge folded into it (that merge's own snapshot): a card
# whose newest event is that snapshot and which lives nowhere. Returns the properties it filled.
_REFILL_SQL = """
WITH RECURSIVE target(account_id, property_id) AS (
    SELECT NULL::uuid, %(r)s::bigint
    UNION ALL
    SELECT * FROM unnest(%(left_accounts)s::uuid[], %(left_ids)s::bigint[])
), fam AS (
    SELECT t.account_id, t.property_id AS t, t.property_id AS h, 0 AS depth
    FROM target t JOIN properties tp ON tp.id = t.property_id AND tp.status = 'active'
    UNION ALL
    SELECT f.account_id, f.t, p.id, f.depth + 1 FROM fam f
    JOIN properties p ON p.merged_into = f.h AND p.status = 'merged_away'
    WHERE f.depth < 20
), parked AS (
    SELECT DISTINCT ON (e.account_id, f.t)
           e.account_id, f.t AS property_id, e.card_id, e.to_stage_id, e.note_snapshot
    FROM fam f
    JOIN property_pipeline_events e
      ON e.property_id = f.h
     AND (f.account_id IS NULL OR e.account_id = f.account_id)
    JOIN pipeline_stages ps ON ps.id = e.to_stage_id AND ps.account_id = e.account_id
    WHERE e.reason = 'merge_absorb' AND e.card_id IS NOT NULL
      AND (f.h = f.t OR EXISTS (SELECT 1 FROM property_merge_events m
            WHERE m.merge_group_id = e.merge_group_id AND m.retired_property_id = f.h
              AND m.undone_at IS NULL))
      AND NOT EXISTS (SELECT 1 FROM property_pipeline c
                      WHERE c.card_id = e.card_id AND c.account_id = e.account_id)
      AND NOT EXISTS (SELECT 1 FROM property_pipeline_events n
                      WHERE n.card_id = e.card_id AND n.account_id = e.account_id
                        AND n.id > e.id)
      AND NOT EXISTS (SELECT 1 FROM property_pipeline c
                      WHERE c.property_id = f.t AND c.account_id = e.account_id)
    ORDER BY e.account_id, f.t, ps.is_terminal, ps.position DESC, e.id DESC
), placed AS (
    INSERT INTO property_pipeline (account_id, property_id, stage_id, note, card_id)
    SELECT account_id, property_id, to_stage_id, note_snapshot, card_id FROM parked
    ON CONFLICT (account_id, property_id) DO NOTHING
    RETURNING account_id, property_id, stage_id, note, card_id
), logged AS (
    INSERT INTO property_pipeline_events
        (account_id, property_id, to_stage_id, reason, merge_group_id, note_snapshot, card_id)
    SELECT account_id, property_id, stage_id, 'split_carry', %(g)s::uuid, note, card_id
    FROM placed
)
SELECT DISTINCT property_id FROM placed
"""

# Detach (c): a LIVE deal and a dismissal never coexist for one account (`api.dismissals`'
# rule, the array form of `property_carriers._DISMISSAL_LIFT_LIVE_DEAL_SQL`), over every
# property this detach placed a card on; a card closed into a terminal stage keeps it.
_LIFT_DISMISSALS_SQL = (
    "UPDATE property_dismissals d SET lifted_at = now(), lift_reason = 'pipeline' "
    "WHERE d.property_id = ANY(%(ids)s::bigint[]) AND d.lifted_at IS NULL "
    "AND EXISTS (SELECT 1 FROM property_pipeline pp "
    "  JOIN pipeline_stages ps ON ps.id = pp.stage_id AND ps.account_id = pp.account_id "
    "  WHERE pp.property_id = d.property_id AND pp.account_id = d.account_id "
    "  AND NOT ps.is_terminal)"
)

# Before migration 582, move-if-empty on one hop: a survivor that held no card of its own in
# that merge got this property's card; drop it there so the restored card isn't duplicated.
_DROP_ABSORBED_SQL = (
    "DELETE FROM property_pipeline "
    "WHERE property_id = %(s)s "
    "  AND EXISTS (SELECT 1 FROM property_pipeline_events e "
    "    WHERE e.merge_group_id = %(g)s AND e.reason = 'merge_absorb' "
    "      AND e.property_id = %(r)s AND e.to_stage_id IS NOT NULL AND e.card_id IS NULL "
    "      AND e.account_id IS NOT DISTINCT FROM property_pipeline.account_id) "
    "  AND NOT EXISTS (SELECT 1 FROM property_pipeline_events e "
    "    WHERE e.merge_group_id = %(g)s AND e.reason = 'merge_absorb' "
    "      AND e.property_id = %(s)s "
    "      AND e.account_id IS NOT DISTINCT FROM property_pipeline.account_id)"
)

# Every statement the carrier can run (its `sql`: the strict-cursor test and the PREPARE corpus).
STATEMENTS: tuple[str, ...] = (
    _SNAPSHOT_SQL, _MOVE_IF_EMPTY_SQL, _RETIRED_WINS_SQL, _DROP_RETIRED_SQL,
    _LOCK_OWN_SQL, _SEND_HOME_SQL, _RESTORE_SQL, _REFILL_SQL, _DROP_ABSORBED_SQL,
    _LIFT_DISMISSALS_SQL,
)


def reconcile_pipeline_on_merge(
    cur: psycopg.Cursor, *, retired_id: int, survivor_id: int, merge_group_id: str
) -> None:
    """Keep the most-advanced (terminal-aware) deal on the survivor, per account, with its
    card_id; park the other; snapshot both."""
    params = {"r": retired_id, "s": survivor_id, "g": merge_group_id}
    for statement in (_SNAPSHOT_SQL, _MOVE_IF_EMPTY_SQL, _RETIRED_WINS_SQL, _DROP_RETIRED_SQL):
        cur.execute(statement, params)


def reconcile_pipeline_on_detach(
    cur: psycopg.Cursor, *, restored_id: int, left_id: int, undo: Sequence[Hop]
) -> None:
    """Send the reactivated property's deal home by card_id from the merge that retired it
    (`undo[0]`); put back what was parked on every property left without a card; lift the
    dismissals a placed live card contradicts. A pre-582 merge restores its snapshot
    (dropping the absorbed card on one hop) as before."""
    params = {"g": undo[0].group, "r": restored_id, "s": left_id}
    cur.execute(_LOCK_OWN_SQL, params)
    cur.execute(_SEND_HOME_SQL, params)
    left = cur.fetchall()
    cur.execute(_RESTORE_SQL, params)
    cur.execute(_REFILL_SQL, {**params, "left_accounts": [a for a, _p in left],
                              "left_ids": [p for _a, p in left]})
    placed = [int(p) for (p,) in cur.fetchall()]
    if left_id == undo[0].survivor:
        cur.execute(_DROP_ABSORBED_SQL, params)
    cur.execute(_LIFT_DISMISSALS_SQL, {"ids": sorted({restored_id, *placed})})
