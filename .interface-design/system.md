# Interface design system — PropScout.cz SPA

Saved 2026-10-19 after the Browse › Stats dashboard build (`docs/design/stats-dashboard.md`). The tokens are
`frontend/src/styles/globals.css` `@theme`; nothing here adds one (operator OK is needed for a new token, CLAUDE.md).

## Direction and feel
A civic-archive ledger for a property analyst: dense, quiet, numbers first. Paper surfaces, ink text,
**one accent (copper)** that always means "chosen or central" — a picked chip, the median mark, the first
series. **Brick** means a destructive action, an error, or a dimension that does not apply to the cohort.
The ink tints (`--color-ink-2/3/4`) carry secondary series and the ledger bar; `--color-ink-4` is for
disabled / placeholder text only, never for data (contrast).

## Colour schemes (2026-10-26, operator ask)
Three schemes, each with a light and a dark side; the user picks both (account menu, phone account sheet,
Settings → Appearance; stored per device in `lib/theme.ts`). A scheme changes token VALUES only: every token
keeps its name, so `--color-copper` means "the accent" in all three, and component code never asks which
scheme is on.
- **Měď** (default, in `@theme`): laid-paper canvas, verdigris accent `#1f6b5c`, and a dark ink top bar.
- **Kobalt** (`data-scheme="kobalt"`): cool porcelain canvas, cobalt accent `#1f45b5`, light top bar.
- **Grafit** (`data-scheme="grafit"`): monochrome — the accent is ink (`#18181b`), so colour on screen only
  ever means something (a live price, a drop, an error, a tag, a pin); light top bar.
- **Contrast**: `ink`, `ink-2` and `ink-3` hold 4.5:1 on paper, paper-2, paper-3 and inset in all six themes
  (`ink-3` darkened from `#7a7d86` for that); the accent holds 4.5:1 as text on paper and on its own soft wash;
  `--color-on-copper` / `--color-on-brick` are the text on an accent or danger FILL (never `text-white`: a
  dark scheme's accent is light). The soft washes are solid now, not alpha.
- **Top bar**: `--color-chrome`, `-chrome-ink`, `-chrome-ink-2` (idle nav, 4.5:1), `-chrome-active`,
  `-chrome-accent` (the active underline, the ".cz" of the wordmark, the account button when open). Only the
  Shell header and its account button draw in them; popovers below it stay on paper.
- **Tags and stages** are the user's colours: the same in every scheme, light/dark only, each 4.5:1 on its wash.
  `tag-copper/sage/brick/ochre` are literal now (they aliased the accent and status tokens).
- The [PropScout.cz design system artifact](https://claude.ai/artifact/NstEVRTDXyQ8ZNJhEpxv7m) previews every
  agenda in these schemes with the operator's rulings applied. It is direction, not code: what binds a build
  is written in this file. Terakota was proposed and dropped (ruling P35).

## Hierarchy rulings (2026-10-27, operator, removal review)
- **Nothing revealed on hover may move anything.** A hover or focus reveal is an overlay on what is already
  there (a tag over a card photo, the carousel chevrons), never a line that grows a card or pushes content.
- 24 changes are confirmed, and all are built: the Browse card's (P01, P02, P03, P05, P06 restyle) as
  agenda 1 alone (#2127), the rest as agendas 2–8 in one PR (operator, 2026-10-27; branch
  `feature/redesign-rulings`), from the
  [review page](https://claude.ai/artifact/WoFQVG1RwmaqXAfBBQoqbV) (P01–P35, each with a before/after picture). The 11 declined (P04, P06–P08, P11, P15–P18, P21, P23) stay as they are; don't re-propose.
- Render scoring is to be removed end to end, front end and backend (operator note on P03): its own scoped PR.
- Handoff with every ruling, the build queue with file pointers, and the open questions:
  `docs/design/propscout-redesign.md`.

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
- **Valuation ruler** (`estimation/RangeStrip.tsx`, the Valuo card): an outside band low · mid · high on the printer's
  ruler with a REQUIRED caption naming it (Valuo's confidence band `range_price`: "pásmo spolehlivosti (Valuo)", never
  its min/max); copper is the middle only, every other
  figure read against it (the ask, our run, MF) is an ink tick with its label on its own row, growing toward the wider
  side; one outside the band pins to the end it passed with "mimo pásmo" — never a sideways scroll at 360 px.
- **Provenance tag** (`listing-detail/ValuoSection.tsx` Param): after a field's caption, tracked small caps
  (`0.58rem`, `0.16em`) say where the value came from — "z inzerátu" ink-3, "odvozeno" ink-3, "předpoklad" ink-2,
  "chybí" ink-3 over a dashed control (nothing is sent), "upraveno" copper (the user's choice); the rule or note behind
  it sits in an InfoHint. Every estimate
  figure's card frame is ONE `estimation/EstimateCard.tsx`: plain rule = an outside authority, copper wash = ours,
  dashed = absent.
- **Comparables table** (the Valuo card): the first column is the comparable's first photo (fixed ~72×54 box, lazy;
  a broken image is a dashed empty box) opening the app's existing gallery; numbers mono tabular; a realised sale
  price is tagged "prodáno"; the table scrolls inside its own box at narrow widths, never the page. While our
  recalculation is offered (`lib/valuo.ts` `recalcBase`) a tick sits before each photo and the copper "Náš přepočet" card
  above the table keeps ONE height in every state (a sentence, a figure, a figure with its band), so the rows never
  jump under the cursor.

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
- **Browse map pins** (`ListingMap` `PIN_COLOR`, operator 2026-10-21): magenta `#b2067e`, a map literal (the
  basemap is not themed), not a token. Chosen as the one hue nothing else on that map wears: the OpenFreeMap
  liberty basemap, every overlay colour in `map_overlay_layers` (reds, orange, yellow, greens, blues,
  purples) and the ochre hover are all at least 46 CIE76 away; 3.7:1 on the worst basemap fill, 3.2:1 on the
  black exact-position contour. Red was rejected (the metro C and flood overlays are red), teal green (the old
  `#3c6e63`) sat in the woods' hue. The estimation `ComparablesMap` and `DetailMap` keep copper: there green
  is "comparable" against the orange subject.
- **Shared admin primitives**: `Panel` (paper-2, rule border, small-caps title) is the card of Health, Costs,
  LocationQuality, LocationPinAudit and NewDedupDashboard; `Stat` (label, mono value, ink-3 hint, an optional
  dot; never a coloured number) the tile of Health, Costs, LocationQuality and AutodedupProgress; `StatusDot`;
  `Skeleton`, the loading block (static: no pulse); `ErrorBanner`, the one failure banner (`flush` under a
  settings heading). A tile's trouble count is never a brick number (Costs' by-feature errors column, a table cell, awaits its own ruling): the page passes `status='fail'` only
  while its own condition holds (a count above zero; a share above the page's bound) and the tile draws a brick
  dot beside its label, the word Problem sr-only. Copper stays "chosen or central", never a number's emphasis.
  Acknowledge is an outlined copper button: the operator's own act.
- **Browse card price** (`ListingCards`, operator 2026-10-21 — the one exception to "numbers stay ink" outside
  Health): a live ask is `--color-sage` semibold (Inter ships 400/500/600, so semibold is the heaviest real
  weight; 4.58:1 on paper-2 in light, 7.22:1 in dark); a delisted card's price stays `ink-2` medium so a dead
  ask never reads as live. Sage here means "a live ask", beside PriceDelta's sage ↓: the arrow and the signed
  percentage carry the price move, never the colour alone. Copper stays off it ("never a number's emphasis").
