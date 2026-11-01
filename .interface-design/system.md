# Interface design system — PropScout.cz SPA

Saved 2026-10-19 after the Browse › Stats dashboard build (`docs/design/stats-dashboard.md`). The tokens are
`frontend/src/styles/tokens.css`; nothing here adds one (operator OK is needed for a new token, CLAUDE.md).

## Direction and feel
A civic-archive ledger for a property analyst: dense, quiet, numbers first. Paper surfaces, ink text,
**one accent (copper)** that always means "chosen or central" — a picked chip, the median mark, the first
series. **Brick** means a destructive action, an error, or a dimension that does not apply to the cohort.
The ink tints (`--color-ink-2/3/4`) carry secondary series and the ledger bar; `--color-ink-4` is for
disabled / placeholder text only, never for data (contrast).

## Colour schemes (2026-10-26, operator ask)
Three schemes, each with a light and a dark side; the user picks both (account menu, phone account sheet,
Settings → Appearance; one store, `lib/theme.ts`, kept in the user's profile with a device cache; System
resolves to a concrete `data-theme`). A scheme changes token VALUES only: every token
keeps its name, so `--color-copper` means "the accent" in all three, and component code never asks which
scheme is on.
- **Měď** (default, the `:root, :host` block): laid-paper canvas, verdigris accent `#1f6b5c`, and a dark ink top bar.
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
- Render scoring is removed end to end, front end and backend (operator note on P03, 2026-10-28): no photo
  shows a render verdict.
- Handoff with every ruling, the build queue with file pointers, and the open questions:
  `docs/design/propscout-redesign.md`.

## Section cards (binding, operator ruling 2026-10-28: "go with section cards")
One small-caps label marked every level (a section, a group, a field, a table head: 276 uses in 121 files), so a
page's sections did not stand apart. Of four options drawn (headline and rule, section cards, ledger band, side
index), **B, section cards** is ruled. **Picture with each value tagged: `docs/design/propscout-section-cards.png`**
(its standalone page, `propscout-section-cards.html`, beside it). Build to match it.
- **The ladder**, on every page. L1 page title: Fraunces 24 (unchanged). **L2 section: the section card's title**,
  Fraunces 600 17/24, ink, sentence case. L3 group inside a section: Inter 600 14/20, ink, sentence case. L4 field
  label: small caps 11/14, weight 600, `0.08em`, ink-3.
- **Small caps are L4 only**: above a value, a control or a table column (and table heads, a list's day rule).
  Never a section, group or card title.
- **The card** (C1–C6): a `<section>` on `--color-paper-2`, 1px `--color-rule`, `--radius-md`, no shadow; 16 px between
  cards (`space-y-4`). A title bar, padding 13 / 20 / 12 px, with a `--color-rule-soft` line under it. On one
  baseline it holds the title (the h2), the count (mono 13, ink-3, a bare number, no brackets), then an optional
  note (12, ink-3) and the actions (small secondary buttons or links), pushed right. Body padding 16 / 20 / 18 px,
  12 px between blocks. Below `sm` the side padding is 16 px. A tab with one section still draws it as a card.
- **No box in a box** (C7, C9). Inside a card, a group is an L3 title with a `--color-rule-soft` line between
  groups, never a bordered or dashed box around it. The one inner frame kept is the figure tile
  (`estimation/EstimateCard`: it frames one figure; its eyebrow is L4). A table that scrolls keeps its own box.
- **Empty** (C10). A section with nothing keeps its card, title and count 0, with one muted line and the action
  ("Zatím žádné vlastní fotky." + Nahrát). Valuo's dashed "Zatím bez ocenění" tile becomes that line.
- **Where**: every section of the property page's tabs and of the other detail pages that stack `SectionLabel`
  blocks (estimation run, broker, collection, share), and the admin pages' panels. Not section cards: items in a
  list (Browse, map and kanban cards), the Stats tab's own settings panel and chart cards, dialogs and popovers.
- **Build (SPA)**: `components/Panel.tsx` becomes the one section card (title L2 in the bar, a `count` prop,
  `accessory` at the bar's right end, `lede` the body's first line). Wrap each detail section in it; Health, Costs,
  LocationQuality, LocationPinAudit and NewDedupDashboard get the same card through that change. The
  `SectionLabel` + `Hairline` pair between sections goes; `SectionLabel` stays only as the L4 field label. No new token.
- **Chrome extension** (X1; plain CSS, the app's palette `frontend/src/styles/tokens.css` on every shadow root and on
  the side panel's page, the host or `<html>` wearing the user's scheme and theme): the listing panel is **Chrome's
  side panel** (operator ruling 2026-10-31; it narrows the page, never covers it) — `--color-paper` under a
  `--color-chrome` top bar (wordmark, "V aplikaci", ⋯, › close), its width Chrome's — of the same cards; on a listing
  page a tab on the right edge opens it (the PS mark over the stage badge, the price drop and MF). Cards:
  `--color-paper-2` on the panel's `--color-paper`, 1px `--color-rule`, `--radius-xs`, no shadow. Title bar
  9 / 12 / 8 px with a `--color-rule` line under it; title 600 14/20 `--color-ink` in the system font (no Fraunces),
  the count `--color-ink-3` mono tabular, a note (12, ink-3) pushed right. Body 10 / 12 / 12 px, 10 px between cards.
  The header sits on the panel, not in a card. "AI agenti" is the one accent card: a `--color-copper` title band over a
  `--color-copper-soft` body. Field labels and group heads inside a card are L4. Built:
  `chrome-extension/src/card.ts` `buildCard` (every card of the panel), `panel.css` (the one stylesheet).

## Depth
Borders only. `--color-rule` for structure, `--color-rule-soft` for row separation, `--color-rule-strong`
for emphasis; **dashed = absent** (a cell under the minimum n, an empty slot, "add a chart"). No shadows.
Surfaces: cards `--color-paper-2`; shelves / wells that RECEIVE content are inset one step; slots
`--color-paper-3`; a rollup (parent) row sits one step down on `--color-paper`.

## Typography
Fraunces (`font-display`) for page, section-card and card titles, Inter for labels and group titles, tracked
small caps for field labels and table heads only (the ladder: § Section cards), JetBrains Mono (`font-mono`)
with `tabular-nums` on EVERY number.

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
- **Tab strip** (`components/Tabs.tsx`; binding, operator ruling 2026-10-27, option A "a rule across the column"): ONE
  component draws every page tab strip: Browse and its Explore windows, the property page (`PropertyTabs` keeps its
  tab list, address and panels), a collection's members, NEW DEDUP Labeling's statuses.
  - The 1px `--color-rule` rule belongs to the strip and runs its container's whole content column. A trailing slot
    (Browse's Split | Cards and Small | Large) sits at the strip's right end above the rule; on a row too narrow for
    both it wraps to a line ABOVE the tabs, never below them, so the tabs never leave the rule.
  - The first label sits on the title's edge: tab buttons have no horizontal padding, tabs are `gap-6` apart.
  - Open = `ink` plus a 2px copper line exactly as wide as the label and its count, laid ON the rule (covering it);
    idle = `ink-3`, hover `ink-2`. Colour only, never weight, so no label shifts. One size: `h-11 text-sm`, no tracking.
  - One count style, the inset pill (`rounded-full`, `--color-inset`, `px-1.5 py-0.5`, mono `0.68rem` tabular, `ink-2`);
    each caller decides WHEN (Browse its cohort total, 0 included; the property page above zero only).
  - Full WAI-ARIA tabs: a named `tablist`, `type="button"` tabs, `aria-selected`, a roving tabindex, ←/→ wrapping,
    Home/End, focus following the selection; `idPrefix` adds the ids and `aria-controls` where the caller has panels.
  - Too many tabs scroll sideways with no scrollbar. The scroller keeps 4px round every tab so the global focus ring
    is never clipped by it (no opt-out, no inner ring) and gives the room back with negative margins. That room hangs
    past the strip, below the rule too, so the scroller takes no pointer: one row inside it (the tabs and their gaps,
    exactly the strip's height) takes the clicks and the sideways swipes, and content under a pinned strip stays
    clickable up to the rule.
  - `sticky` pins the strip under the Shell bar (`top-14`, `z-20`: above the cards' z-10 controls, below the bar's
    z-30); a scroll target under it uses `scroll-mt-28` (bar 57px + strip 44px + air).
  - Out of scope: the phone pipeline's stage strip, and the view switches (`MapViewToggle`, `SizeToggle`,
    Notifications' pills), which are pick-one bars (§ Option groups).

## Option groups (binding, operator rulings 2026-10-27: "filter buttons reuse the same elements across the app")
- **A pick-one is ONE joined bar, `SegmentedBar`** (`components/controls.tsx`): any group where one option is chosen,
  an explicit Vše / All / any included. One track (`--color-paper-2`, rule border, `p-0.5`, `gap-0.5`) of adjoining
  segments; the chosen one is filled `--color-copper` with `--color-on-copper` text, idle `ink-3` (hover `ink-2`),
  colour only, never weight. An ink fill, a raised tile with a shadow, white + bold or a copper-soft chosen segment
  is not a variant: each converges on this one. `Segmented` (a Field's pick-one), `SizeToggle` / `ImageSizeToggle`,
  `TriRow` and the account menu's Light / Dark / System row draw it; a local copy that becomes the bar is deleted,
  never left beside it.
  - Width: `fill` = equal segments across the container (the filter sidebar's Status line), 4px of side padding
    (the label is centred, so padding only says when it runs out of room) and a label with no room left ends in an
    ellipsis inside its own segment, its name whole (the sidebar at its 240px floor); `hug` = each segment its
    label's width at the same reading size, the bar scrolling sideways with no scrollbar inside the tab strip's 4px
    focus-ring room (`Segmented`, toolbars, long labels with counts); neither = the compact amenity rows (equal
    segments, small type).
  - `deselectable` serves a pick-one-or-none group with no Vše (Listing for, by the 2026-10-20 ruling): nothing lit
    when none is set, a click on the lit segment releases it. Otherwise a click on the lit segment sets it again.
  - Per option a `glyph`, a `title`, `disabled` and an `ariaLabel` (the segment's own name where its label leans on
    the caption: ChartBuilder's "Hodnota: Cena"); `disabled` for the whole control; `box` + `aria` make it the form
    box (the Valuo editor: the track is the 34px box, dashed while absent, brick while `aria-invalid`).
  - `tone="phone"` on the phone's own screens (Makléři Řadit and the horizon bar, Prohlídky, the account sheet's
    Vzhled, Notifications inside the phone frame): a `rule-strong` track hairline, 32px, 13px type; chosen = ink on
    `--color-inset` wearing the phone chip's pressed `ink-2` hairline as an inset ring (`chipState`; the well alone
    is ~1.05:1 on paper; a ring, so no width moves, and no weight); colour there only ever means a stage. The
    Filtry sheet hosts desktop controls and keeps the copper bar.
  - A lone on/off in the filter sidebar or `FilterForm` is a two-segment bar "<off> | <label>", never one chip whose
    word flips. The off words are ONE list, `BOOLEAN_OFF` (`components/FilterForm.tsx`: "All ads | Seen at birth"),
    and `RegistryBar` there draws every registry pick-one and on/off bar; a new on/off adds its off word there.
  - The root is always the named `role="group"`; each segment is a button with `aria-pressed`, except inside a
    `role="menu"` (the account menu), where `segmentRole="menuitemradio"` makes each one a `menuitemradio` with
    `aria-checked`.
- **Pick-several and include / exclude stay chips** (`PickButton`, `MultiselectChips`; the one-chip-one-polarity rule
  in the interface-design skill § Controls).
- **Stay as they are**, by ruling: a group of more than six options (the phone Filtry sheet's 17 sort orders, the
  Estimation Adjust disposition grid) keeps chips; navigation-like pick-ones (the Pipeline board switcher, Makléři ·
  Review's reason queues, NEW DEDUP Exam's cohort and set links; page tabs are `Tabs`); lone on/off buttons outside
  the filter sidebar (Watchdog Active / Paused and its list toggle, the phone Feed's "jen aktivní", the bake-off's
  "show retired" / "widen"); colour swatch pickers (a text bar loses the preview); the coloured labelling tools
  (tri-state squares, subject state, training-set marks, head review state: a colour per state, hotkeys); map
  flyout radio lists; the phone Nové scope row (each chip loads a saved view). Every option keeps its order and
  wording: Vše is not moved.

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
- **Shared admin primitives**: `Panel` (the section card, § Section cards) is the card of Health, Costs,
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
