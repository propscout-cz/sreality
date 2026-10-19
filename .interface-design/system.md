# Interface design system — Limen Reality SPA

Saved 2026-10-19 after the Browse › Stats dashboard build (`docs/design/stats-dashboard.md`). The tokens are
`frontend/src/styles/globals.css` `@theme`; nothing here adds one (operator OK is needed for a new token, CLAUDE.md).

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

## Health page (2026-10-19)
Added after the Health page rebuild (`docs/design/health-dashboard.md` § 5, wave 8). It extends the ledger
above for status and adds no token.
- **The matrix is the page's spine** (`components/health/PortalMatrix.tsx`): portals as rows, worst first, the
  portal checks as columns; a cell shows that check's worst measured verdict on that portal. Every other
  check is a row in the same register, worst first. Below 768 px OK cells hide, and a section with nothing
  open says "Nothing open".
- **The status word always rides with its dot** (`components/StatusDot.tsx`): OK sage, Watch ochre, Problem
  brick, each a filled dot; the word sits beside it (sr-only where the layout shows it elsewhere). Colour
  lives only in the dot: numbers, words and messages stay ink, because ochre text fails contrast and the
  status hues are too close for colour-blind readers (sage and brick for deuteranopia).
- **Hollow ring = Not measured**: a 1.5 px `--color-ink-3` ring with no fill, never a green or a filled
  `ink-4` dot, so a check nobody measured can never look healthy. Stale reads the same.
- **Shared admin primitives**: `Panel` (Costs' card: paper-2, rule border, small-caps title), `Stat` (label,
  mono value, ink-3 hint, an optional dot; never a coloured number) and `StatusDot`. Acknowledge is an
  outlined copper button: the operator's own act. Wave 10 moves the other admin pages onto them.
