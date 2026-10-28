# Photo kinds — one label per photo, decided once

Program document (north star, decision rule, lane, waves, cost, rulings, measurements):
`docs/design/photo-kinds/PROGRAM.md`. Status: **PARKED by operator ruling 2026-10-27: kept as the design of
record; no build, no GPU spend; resuming needs a new ruling.** Proposed 2026-10-20; R6 answered 2026-10-25 (ruling 3b,
PR #1999: #1639/#1643 stay open, DINOv3 kept); R1–R5, R7, R8 open. Before unparking, read the program's "Since
2026-10-20" notes: W1 is mostly done by actions-exit W0, the GitHub-cron GPU lane (W3, R8) conflicts with ADR-0001 and the
actions-exit placement rules (GPU work = attended RunPod dispatches), and migrations 646/648 are taken on main (renumber at build time).

## Why

Users see CLIP's zero-shot label on every photo (precision 0.58 on the seven kinds it shares with the trained
DINOv3 heads, measured on the sealed holdout exam_v1); the Browse "Cover" already reads the DINOv3 heads with a
different rule, so one card can contradict itself. The trained heads are right about 9 times in 10 at a 0.80 floor
(pessimistic bound 0.80) but cover only 7.75% of active photos and nothing scores new ones. Dedup reads the CLIP tag
rows, so the CLIP lane stays; only its product read paths go.

## Waves (one draft PR each)

- [ ] **W1** `cleanup/photo-kinds-dead-paths` — dead lanes and flags out (mirror weights, render backfill, train_tag_head, drop_dinov3, FINE_TAG_KEYS). No behaviour change. All but `scripts/train_tag_head.py` already deleted on main (2026-10-21/25).
- [ ] **W2** `feature/photo-kinds-decision` — migration 646 (additive; number taken, renumber): `kind_floor` + `shown_heads` on the model version, `display_name` on the vocabulary, views `image_photo_kinds` + `photo_kinds_public`, `images_public` appended; `tag_model gate` (read-only report); `/new-dedup/tags/*` deleted (R5).
- [ ] **W3** `feature/photo-kinds-lane` — redesign first (actions-exit placement: GPU runs are attended RunPod dispatches). The scheduled GPU lane: identity from the registry, R2 vectors, in-pass scores, selection-side re-arm, done record, coverage health cell; then the active-listing backfill (R2).
- [ ] **W4** `feature/photo-kinds-spa` — every photo surface reads the kind; CLIP, render pill and head-score reads leave the SPA (R3).
- [ ] **W5** `cleanup/photo-kinds-contract` — migration 648 (destructive, operator window; number taken, renumber): `images_public` 15 → 8 columns, `listing_cover_public` 11 → 3, `image_dinov3_embeddings` dropped (R7).

## Next (named, not in this sprint)

- v2 as data: `promote` from R2 vectors (deletes the dedup_sim coupling), one shared split, per-head calibration, exam_v2 single-phase draw, heads ložnice / jídelna / nezařízená místnost / domovní vchod from existing labels; WC / chodba / zahrada / balkon / schodiště / situační plán / document after labelling (~12–16 operator hours).
- "Has a floor plan" Browse + Watchdog filter on `image_photo_kinds` (kind 46), one `filter_compiler` hook (rule 16).
- Render scoring is gone (2026-10-28, migration 686): `image_clip_tags_render_null_idx`, `render_score` and NEW DEDUP's render limb
  went with it (`docs/design/propscout-redesign.md`); `clip_retag.yml` was folded into `clip_tag.yml` 2026-10-25.
