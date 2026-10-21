# MERGE BY LETTERS — Browse's merge mode as one statement

**Status:** BUILT 2026-10-21 (one PR, no migration); merges in the autodedup session's window.
**Track:** [roadmap/operator-workflow-track.md](../../../roadmap/operator-workflow-track.md) § U-MERGE-LETTERS.
**Binds with:** rule 15, MS12 / MS15 / MS16 / MS17 / MS18 of the merge sprint
([merge-sprint/PROGRAM.md](../merge-sprint/PROGRAM.md)); the glossary term **Letter**.

## North star

**One statement, one merge.** The operator writes a statement over the cards — a letter per
property — and the app makes it true in one transaction, exactly as the split by letters already
does over the adverts of one property. Every decision below is judged by whether it keeps one
statement shape, one target on screen and one path in the backend.

## The ask (2026-10-21)

Merge mode held one set at a time: tick, merge, repeat. The operator wanted many groups in one
session — a letter key on the card under the pointer, Tab / Shift+Tab to move card to card, ← → to
page the focused card's photos — and one click merging every group; the letters mean nothing
afterwards. Mandate: subtraction, a north star, no flags, no patchwork.

## What existed, verified

- Merge mode held a `Set<property_id>`; each card a real checkbox under a card-wide label; the bar
  listed the ticked properties with their marks and the MS12 count; `POST /properties/merge
  {property_ids}`; success left the mode. The Rulings page kept a second copy of the merge mutation.
- The letters already existed on the split: the glossary term, `lettersParam` / `parseLetters` in
  the SPA, `parse_letters` in the backend, and a join of a letter landing on two properties that
  already called `merge_property_set` per letter inside one transaction. The merge route was a
  second caller of the chokepoint with a second statement codec.
- Tab did not go card to card: the photo chevrons were tab stops.
- The keyboard precedent is the labeling page: the handler sits on the grid container, never the
  window, so a filter field keeps its keys.

## Rulings (2026-10-21, all defaults accepted)

| # | Ruling | Default taken |
| --- | --- | --- |
| D1 | Letters are a grouping only: no "different" ruling is recorded across letters. | yes |
| D2 | A click stamps the active letter (A at first); a letter key stamps and activates; the same letter again clears. The old click-click-merge habit is unchanged. | yes |
| D3 | The target is the focused card; in merge mode the pointer focuses the card it is over. One target for letters, arrows and Tab. | yes |
| D4 | Chevrons leave the tab order and ← → page the photos **on Browse cards** (both modes). Other carousel mounts keep their chevron tab stops: they have no other focus stop, so arrows alone would strand keyboard users there. (The proposal said "every carousel"; corrected at build.) | yes, narrowed |
| D5 | One transaction, one toast: a refusal anywhere writes nothing; one line per letter; "Otevřít #S" only for a one-letter statement. | yes |
| D6 | The letters statement replaces the id list on the route; the Rulings page merges as a one-letter statement; no dual acceptance of the old body. | yes |

## As built

**Backend.** `toolkit.property_identity`: `LETTERS`, `BadLetters`, `letter_tokens` (the one wire
codec, `id:A,…`), `letter_sets` (letters as sets, ordered by each letter's lowest id — the one lock
order), `merge_preview(letters)` (MS12 counts per letter) and **`merge_letters`** (each letter held
by two or more properties through `merge_property_set` as an operator merge under its own group, all
in one transaction; a lone letter merges nothing; nothing written on a refusal; no ruling crosses
letters). `toolkit.property_split._join` builds its landings as a statement and calls the same
function; `parse_letters` reads through `letter_tokens`. `api/property_merge.py`: `POST
/properties/merge {letters}` (a lone letter and anything but A–Z are a 400; 2–200 properties), one
receipt per letter (`merge_receipt` per group), a clash a 409 `{code, message, ids}` with
`LETTER_ENDING`, prefixed "Písmeno B: " when the statement has several letters; `GET
/properties/merge?letters=…`. `MERGE_ENDING` is gone.

**SPA.** `lib/useMergeMode` (the statement, the active letter, the grid key handler: a letter key,
Backspace / Delete; never the window), `components/MergeBar` (chips per letter in use plus the next
free one, the ledger per letter with the marks, the summed MS12 count, the button held while a letter
has one property), the card's one stretched `<button>` named by its letter (the pointer focuses it,
Tab moves card to card, ← → page through `ImageCarouselHandle.step`; chevrons `tabIndex=-1` on Browse
cards), `lib/useMergeProperties` over the statement, the receipt per letter in `lib/mergedAdverts`,
the Rulings page on the same hook. The letters' one home in the SPA is `lib/api`: `Letters` (the
type the split used to call `SplitLetters`), `LETTERS` (the alphabet `UnitSplit` used to define as
`UNIT_LETTERS`, which `lib` imported from a component module) and `lettersParam` (re-exported for
the split's pages).

**Not changed.** What a merge does (survivor, carry record, rulings), the split, the Explore modals
(merge mode off), rule 15 and MS1–MS23.

## Keys

| Key | On the target card |
| --- | --- |
| `A`…`Z` | stamp that letter and make it active; the same letter again clears |
| `Backspace` / `Delete` | clear the letter |
| `Tab` / `Shift+Tab` | next / previous card (native tab order) |
| `←` / `→` | previous / next photo |
| `Space` / `Enter` / click | stamp the active letter (or clear it) |

No `Esc` binding: Cancel is the one way out. The letters live only in memory for that session of
the mode; nothing is stored, nothing reaches the URL.

## Subtraction (measured in the PR body)

Goes: the id-list body and query parser, `MERGE_ENDING`, the split's own join loop, the
checkbox + stretched label + ✓ pair, the inline `MergeModeBar`, `MergeSelection`, the Rulings page's
duplicate `useMutation`, the second `lettersParam`. Comes: `merge_letters` + the codec, `useMergeMode`,
`MergeBar`, the carousel handle, the keyboard tests. Nothing user-facing was removed.

## Risks accepted

- Focus follows the pointer, in merge mode only; the ring is `focus-within`, so the target is always
  visible. Fallback if it feels wrong in use: "latest intent wins" (hover or focus, whichever moved
  last).
- A Browse tab left open across the deploy posts the old body once and gets one 422 toast until
  reloaded; no dual acceptance.

## Owed

- The first real multi-letter merge in production (a read-only browser check of the keys after the
  rollout).
- The open idea on the track stays open: naming twins outside the current filter in merge mode.
