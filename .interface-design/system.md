# Interface design system — Limen Reality SPA

Saved 2026-10-19 after the Browse › Stats dashboard build (`docs/design/stats-dashboard.md`). The tokens are
`frontend/src/globals.css` `@theme`; nothing here adds one (operator OK is needed for a new token, CLAUDE.md).

## Direction and feel
A civic-archive ledger for a property analyst: dense, quiet, numbers first. Paper surfaces, ink text,
**one accent (copper)** that always means "chosen or central" — a picked chip, the median mark, the first
series. **Brick** means a destructive action, an error, or a dimension that does not apply to the cohort.
The ink tints (`--color-ink-2/3/4`) carry secondary series and the ledger bar; `--color-ink-4` is for
disabled / placeholder text only, never for data (contrast).

## Depth
Borders only. `--color-rule` for structure, `--color-rule-soft` for row separation, `--color-rule-strong`
for emphasis; **dashed = absent** (a cell under the minimum n, an empty slot, "add a chart"). No shadows.
Surfaces: cards `--color-paper-2`; shelves / wells that RECEIVE content are inset one step; slots
`--color-paper-3`; a rollup (parent) row sits one step down on `--color-paper`.

## Typography
Fraunces (`font-display`) for card titles, Inter for labels, tracked small caps (`0.7rem`, `0.18em`) for
section and shelf names, JetBrains Mono (`font-mono`) with `tabular-nums` on EVERY number.

## Spacing
4 px base. Card padding `px-5 pt-4`; `space-y-5` between cards; `gap-3` inside rows.

## Patterns worth reusing
- **Sample ledger** (`components/stats/SampleLedger.tsx`): one line of counts over a thin three-segment
  bar in ink tints, above every chart view. Any surface that shows a statistic says how many rows stand
  behind it and how many were set aside.
- **In-place edit**: a card flips into its editor at the same width (no modal); Uložit / Zrušit in the
  card header; one confirm step before a destructive action (Odebrat).
- **Shelves + chips** (`ChartBuilder.tsx`): a palette of `PickButton` chips (copper = chosen) dropped on
  shelves with dnd-kit AND a click path (chip click appends, slot × removes, keyboard sensor reorders).
- **Box table** (`views/BoxTable.tsx`): one shared axis from every drawn row's p10–p90; the numbers are
  columns of the same table — no tooltips anywhere.
- **Charts**: every axis through `lib/chartAxis.valueAxisSpec`; series copper first, ink tints after,
  dashed beyond the solid set; a series cap with a refusal sentence instead of a legend of dozens.
- **Refusals** read as one Czech sentence in the body; the surrounding chrome (title, ledger) stays.
