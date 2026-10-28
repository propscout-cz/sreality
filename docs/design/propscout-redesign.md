# PropScout.cz redesign: handoff

Status 2026-10-28. This is the one place to pick the redesign up from. It records what is done, what the
operator ruled, and what is next. Read it top to bottom before touching any of the screens named here.

## Where everything lives

| What | Where | Binding? |
| --- | --- | --- |
| Rename + three colour schemes (built) | PR waiff/sreality#2115, merged and live 2026-10-27 | Yes |
| Design rules for every build | `.interface-design/system.md` (§ Colour schemes, § Hierarchy rulings) | **Yes** |
| Section cards: how every page separates its sections (app + extension), ruled and built 2026-10-28 | `.interface-design/system.md` § Section cards; picture `docs/design/propscout-section-cards.png` | **Yes** (§ Section cards below) |
| Design system artifact: previews of every agenda, as ruled | https://claude.ai/artifact/NstEVRTDXyQ8ZNJhEpxv7m | Direction only |
| Removal review: 35 before/after pictures and the operator's answers | https://claude.ai/artifact/WoFQVG1RwmaqXAfBBQoqbV | The rulings below are copied from it |
| Stats review: 10 proposals for Browse › Stats (S01–S10), pictures and answers | https://claude.ai/artifact/RRDNo2TfAa7iWuMUoUBMfg | **Yes** (§ Stats review below) |
| Sequencing | `roadmap/ui-track.md` § PropScout.cz and three colour schemes | Yes |

Reading the artifacts from a session: use the Artifact tool, `action: "read"` with the URL (add `path` for
one file, e.g. `project/README.md` or `project/components/ListingCard/preview.html`). The review page's answers
are in its database, collection `proposals` (documents `p01`–`p35`, fields `decision`, `note`), readable with
the ArtifactData tool (`action: "list"`); the Stats review works the same way (documents `s01`–`s10`). Never
fetch these links with curl or WebFetch.

## What is done

1. **PR #2115** (merged 2026-10-27; Railway deployed API, vite and realtime-worker):
   - the app is **PropScout.cz** (`lib/brand.ts`; tab titles are the page name alone (no prefix) under a "PS" favicon; a "PS" home-screen icon; extension name);
   - three colour schemes, **Měď** (default), **Kobalt**, **Grafit**, each light and dark, picked per device in the
     account menu, the phone account sheet and Settings → Appearance (`lib/theme.ts`, `globals.css`
     `data-scheme` blocks, `components/SchemeSwatch.tsx`);
   - new tokens `--color-chrome*` (top bar) and `--color-on-copper` / `--color-on-brick` (text on a fill);
   - CLAUDE.md "Where the detail lives" points at `.interface-design/system.md`; this file and the roadmap entry.
2. **The design system artifact** (version 8): tokens for the six themes, a component bundle, and a preview of
   Browse (cards, page, map, filters, stats), Pipeline, Watchdogs, Notifications, Brokers (+ broker page),
   Collections (+ collection page) and the Shell, with all 35 rulings applied.
3. **The removal review**: 35 proposals, each with a picture of today and of the change; the operator ruled
   all of them on 2026-10-27 (24 confirmed, 11 declined).

4. **Agenda 1, Browse card** (P01, P02, P03, P05 and the P06 chip restyle): built on branch
   `feature/browse-card-hierarchy` (PR #2127).
5. **Agendas 2–8** (every other confirmed ruling): built on branch `feature/redesign-rulings`, one commit per
   agenda; the as-built notes are under "What to build next". Not built: the render-scoring removal and the
   unruled additions (both below).
6. **Tab strips** (operator ruling 2026-10-27: option A, a rule across the column): one `components/Tabs.tsx`,
   built on branch `feature/shared-controls`; the binding pattern is `.interface-design/system.md` § Tab strip.

## How this operator wants design work done

- **Never remove or move an element on your own.** Keep everything the screen has today; list each removal,
  merge or move as a proposal with a before/after picture and let the operator confirm or decline it one by
  one. (A first pass that dropped elements was rejected: "put it back, I will ask for removal".)
- **Nothing revealed on hover may move anything** (ruling 2026-10-27). A hover or focus reveal is an overlay
  on what is already there, never a line that grows a card or pushes content.
- **Show, don't describe.** Proposals need pictures (shapes, mock-ups) of exactly what changes.
- Design tokens in `globals.css` `@theme` need the operator's OK (CLAUDE.md). The three schemes are approved.
- The operator is non-technical but learns fast: explain the why, define jargon, give click-by-click steps.

## The rulings (2026-10-27)

Where an operator note goes further than the proposal, the note wins; the table already says so.

| # | Area | Proposal | Ruling | What it means for the build | Operator note |
| --- | --- | --- | --- | --- | --- |
| P01 | Browse card | Move the portal names off the photo | **Confirmed** | Show the portals as quiet text at the top of the card’s foot, on a line above the MF yield; the photo keeps lifespan and ad count. |  |
| P02 | Browse card | Show the room tag only on hover | **Confirmed** | The tag appears while the pointer is on the card (and on keyboard focus). |  |
| P03 | Browse card | Show the photo verdict only when it is a render | **Confirmed, widened by the note** | No photo verdict badge at all; the photo shows just the counter. Render scoring itself is a separate removal (see below). | remove render scoring entirely, from front end and backend |
| P04 | Browse card | Shorten the lifespan badge to the day count | Declined | Keep as is: “od 5. 9. · 42 dní” (inactive: “5. 5. – 20. 6. · 46 dní”). |  |
| P05 | Browse card | Drop “Byt na prodej ·” from the card title | **Confirmed** | Title “2+kk · 54 m²”; the type and deal prefix stays when the filter spans several types or both deals. |  |
| P06 | Browse card | Storey as text, not a bordered chip | Declined | Keep as is: A bordered “3. patro z 5” chip after the place line. | keep separate, feel free to adjust the chip as you seem fit |
| P07 | Browse card | Small cards: hide MF yield and the estimate corner | Declined | Keep as is: Small cards (184px) show “Výnos MF” and “Odhad” like Large ones. |  |
| P08 | Browse page | Fold the preset bar into the tab row | Declined | Keep as is: A row of its own: Pipeline chip, Presety fold, every preset with grip · name · ⋯, group boxes, Aktualizovat / Uložit preset / Nová skupina. | Aktualizovat removed 2026-10-27 (operator): a dirty preset's ⋯ menu leads with Save changes and Revert to saved; don't re-add a bar button. |
| P09 | Browse page | Drop the long scope sentence once the applied-filter pills exist | **Confirmed** | The header keeps “12 345 bytů na prodej”; the rest is said by the removable pills under it. |  |
| P10 | Browse page | Move “Režim slučování” to the results header | **Confirmed** | A quiet “Sloučit…” next to Obálka and Řadit above the cards. |  |
| P11 | Browse page | Split “Základní” into Základní + Parametry | Declined | Keep as is: Základní holds 17 rows and is open by default. |  |
| P12 | Browse map | Drop the price-labels button from the map rail | **Confirmed** | Keep only the checkbox in Piny a ceny. |  |
| P13 | Browse map | One line for precision and last seen in the popup | **Confirmed** | One line: “Přesná poloha · viděno před 2 h”, the precision detail in its tooltip. |  |
| P14 | Browse stats | Chart actions into a ⋯ menu | **Confirmed** | Upravit stays; ↑, ↓ and Odebrat move into a ⋯ menu. |  |
| P15 | Browse stats | Trim the summary tables to n, medián, p10, p90 | Declined | Keep as is: Columns n, průměr, medián, p10, p25, p75, p90. |  |
| P16 | Pipeline | Drop the ⠿ drag handle — the whole card drags | Declined | Keep as is: A ⠿ handle at each card’s left edge; dragging starts there. |  |
| P17 | Pipeline | Move the trash into the stage menu | Declined | Keep as is: A trash icon on every card, with an inline confirm. |  |
| P18 | Pipeline | Show the broker line on hover | Declined | Keep as is: “Jana Dvořáková · Reality Vltava +1” on every card. |  |
| P19 | Pipeline | Show the city-index strip on hover | **Confirmed, widened by the note** | No city-index strip on the pipeline card at all (not on hover either); it stays on the property page. | remove the city index strip from the card entirely |
| P20 | Pipeline | Drop the MF yield from the dims line | **Confirmed** | Shown only in the Velké card size. |  |
| P21 | Pipeline | Drop per-m² from the dims line | Declined | Keep as is: “Byt 2+kk · 54 m² · 120 185 Kč/m²”. |  |
| P22 | Pipeline | Fold the four closed stages into one “Uzavřené” lane | **Confirmed** | One narrow lane with a row per closed stage and its count; dropping on a row closes the deal there; a row opens its cards. |  |
| P23 | Pipeline | Show “Artifact ↗” on hover | Declined | Keep as is: An accent “Artifact ↗” button on cards that have one. | in general, we will not have any elements that appear on hover and would make the elements jump up or down |
| P24 | Watchdogs | Move “Smazat” into a ⋯ menu | **Confirmed** | “Upravit” stays; “Smazat…” moves into ⋯ with its confirm. |  |
| P25 | Watchdogs | Fold “Vytvořeno” under the name | **Confirmed** | A grey line under the watchdog’s name; the filter column gets the width. |  |
| P26 | Shell | Drop the small-caps labels above page titles | **Confirmed** | The h1 alone; the nav already says where you are. |  |
| P27 | Notifications | Replace “Načíst další” with loading on scroll | **Confirmed** | Older notifications load as you reach the end (as on Browse). |  |
| P28 | Brokers | Drop the per-cell “nemovitostí / inzerátů” captions | **Confirmed** | The column headers say it once. |  |
| P29 | Brokers | Show five firm pills, the rest behind “+ N dalších” | **Confirmed** | The five largest firms, then “+ 19 dalších” to open the rest. |  |
| P30 | Collections | Move the new-collection form into the grid | **Confirmed** | A dashed “+ Nová kolekce” card at the end of the grid that opens into the same form. |  |
| P31 | Collections | Collection page: blocks into header actions | **Confirmed** | The header carries the Sledovat switch, “Sdílet · 2” and ⋯ (Přejmenovat, Smazat kolekci); each opens what the block holds today. |  |
| P32 | Collections | Collection page: cards first, table as a tab | **Confirmed** | The Browse card grid by default, with notes, pipeline and estimates; the table stays as the second tab. |  |
| P33 | Shell | Move “!Audit poloh” into the Admin menu | **Confirmed** | Under Admin ▾, with the count on the Admin trigger. |  |
| P34 | Shell | Drop the build line from the footer | **Confirmed** | The footer keeps only the map-tile attribution. |  |
| P35 | Design system | Drop the Terakota scheme from the design system | **Confirmed** | Three schemes, six themes: exactly what the app offers. |  |

The declined items stay exactly as they are; don't propose them again. P06's note allows restyling the
storey chip (the design system draws it as a quiet inset label without a border).

## Stats review (S01–S10, opened 2026-10-27)

The operator asked for more hierarchy in the Stats tab, above all its header (the tabs, the dashboard chips,
the Jako ve filtru / Prodáno (trh) / Vše switch, the Malé vzorky switch and the sample sentence). Ten
proposals, each with a before/after picture, are on https://claude.ai/artifact/RRDNo2TfAa7iWuMUoUBMfg and the
operator ruled them there. Before building the Stats tab, list its `proposals` collection with ArtifactData
and build exactly what it says (the rulings are also copied below).

- `decision: "confirm"` → build it, in the agenda-2 PR (Browse page, map and stats), alongside P14.
- `decision: "decline"` → leave that part as it is; don't propose it again.
- `decision: null` → not ruled yet: ask the operator before building it, and build the rest.
- A `note` goes further than the proposal where they differ, as in the removal review.

**The target picture is `docs/design/propscout-stats-target.png`** (its standalone page with the exact markup:
`propscout-stats-target.html`): the agreed design with a yellow tag on each change. Build the Stats tab to match it.

**Ruled 2026-10-27** (copied from the review page; the page stays the source if they ever differ):

| # | Proposal | Ruling | Build |
| --- | --- | --- | --- |
| S01 | One panel for the board's settings | **Confirmed** | The dashboard row and the sample row sit together in one panel above the charts. |
| S02 | Label the rows | **Confirmed** | A label column: "Dashboard" (which charts), "Vzorek" (which properties are counted). |
| S03 | Sample sentence under its switch | **Confirmed** | `MARKET_CAPTION` sits directly under the Jako ve filtru / Prodáno (trh) / Vše switch, the chosen option's name in bold. |
| S04 | Malé vzorky at the row's end | **Confirmed** | "Malé vzorky (pod 5)" at the right end of the Vzorek row; "min, max, průměr, medián" in its tooltip (an overlay). |
| S05 | Sample stated once for the board | Declined | Keep the chips on every chart as today. |
| S06 | Okolí badge | **Confirmed** | The okolí dashboard's chip shows its name, then a small "Okolí" badge with a pin; tooltip: the property page shows this dashboard. |
| S07 | Czech tab labels | **Confirmed** | "Nemovitosti" / "Statistiky" (these two labels only; other English stays until ruled). |
| S08 | Title: measure first | Declined | Keep "Cena · Plocha po 10 m² › Stav" as one title. |
| S09 | Every cell under 5: say so once | **Confirmed** | One line above the table, "Všechny buňky mají méně než 5 nemovitostí, takže statistiky jsou skryté.", with a "Zobrazit malé vzorky" button that turns the switch on; rows and counts stay. |
| S10 | Box legend only when a box is drawn | Declined | Keep the legend line on every chart. |

The page's overview picture shows all ten applied; build only the confirmed seven. Code pointers are in each
proposal's `where` field (`components/stats/StatsDashboard.tsx`, `DashboardBar.tsx`, `StatsChart.tsx`,
`lib/stats.ts` `MARKET_CAPTION`, `lib/neighbourhood` `PROPERTY_CARD_LABEL`, the tabs in `BrowseExperience.tsx`).

**Built** (2026-10-27, the agenda-2 Stats commit on `feature/redesign-rulings`; the seven confirmed only). As built:
- S01/S02: one bordered paper-2 panel (`StatsDashboard` `SettingsRow`), a rule-soft line between its two rows, each
  led by a tracked small-caps label ("Dashboard", "Vzorek"; a plain label, so the chart titles stay the board's only
  headings and the lens stays the one group named "Vzorek"); the label stacks over its row until the panel itself
  is 30rem wide inside its padding (a container query: the filter sidebar, open below lg, sets the column's width,
  and a dashboard chip, up to ~20rem, never shrinks), not at a screen breakpoint. The
  Dashboard row shows only where the bar does (a Browse view and the API). The skew notice follows the panel.
- S03: `lib/stats` `MARKET_LABEL` names the three options; `marketCaption` splits each caption into that name (bold,
  ink-2) and the rest. The "Jako ve filtru" caption gained its name lead; the sold and Vše captions, which the
  property page's Trh v okolí tile shows, are unchanged.
- S04: "Malé vzorky (pod 5)" ends the row and drops to its own line, right-aligned, before the lens and sentence
  get narrower than 18rem; "Buňky pod 5 nemovitostí ukážou min, max, průměr a medián" is the label's native
  tooltip. The switch's own name for a screen reader is unchanged.
- S06: `SavedSetBar` takes an optional `badge` (only the dashboards pass one): "Okolí" in the provenance tag's
  tracked caps with the pin glyph, in the chip's own colour, the tooltip "Tento dashboard se ukáže na kartě Okolí
  dashboard u každé nemovitosti". The name truncates alone, so a long name never hides the badge; a screen
  reader still hears "<name> — okolí dashboard".
- S07: the tab labels only; the tab keys, the URL's `tab=stats` and every other English word stay.
- S09: `lib/stats` `everyCellUnderMin` (every cell the payload sent, the Celkem rollup included, under its
  `min_n`). Only the table and box views show the note (the switch changes nothing on a line or bar), only with the
  switch off and only on the Stats tab (the property page has no switch). On a box chart it sits between the legend
  and the column heads; the legend moved out of the table's `<caption>` into a line that names the table
  (`aria-labelledby`), so the note and its button stay out of the table's name and its sideways scroll. The button
  turns the same switch on (so a loaded dashboard reads "Upraveno od načtení", as the switch does) and leaves focus
  on that chart's heading.

## Section cards (ruled 2026-10-28)

The operator found the small grey capital headings ("MOJE FOTKY (0)", "VÝVOJ CENY", "VALUO") too weak to tell
sections apart, and asked for one pattern for the app and the Chrome extension. Four options were drawn
(headline and rule, section cards, ledger band, side index; https://claude.ai/artifact/VYURnTgzeSshYX1Vsn3C3v,
private to the operator's account) and **B, section cards**, was chosen. Each section is a paper card with a
title bar: a Fraunces title, its count, the actions on the right. Small caps are left for field labels only.

**The binding spec is `.interface-design/system.md` § Section cards; the picture is
`docs/design/propscout-section-cards.png`** (standalone page `propscout-section-cards.html`), each value tagged
C1–C10 and X1 (the extension). **Built 2026-10-28** (branch `feature/section-cards`). As built:
`components/Panel.tsx` is the one card (`Panel`: title, bare `count`, `note`, `accessory`, `lede`), with
`PanelGroup` (C7, an L3 group), `PanelEmpty` (C10) and `PANEL_ACTION` (C6, the title bar's small secondary
button). It wraps every section of the property page's tabs, desktop and phone, and of the estimation run, broker
(phone too), collection, sharing (`ShareBlock`), building and pipeline (Sdílení, Fáze pipeline) pages; the admin
panels (Health, Costs, LocationQuality, LocationPinAudit, NewDedupDashboard) get it through `Panel`. Dialogs keep
their hairlines, their sections L3. `SectionLabel` is the L4 field label only. The extension's X1 cards are built
in the same PR.

## What to build next

Agenda 1 shipped as its own PR (#2127). **Operator ruling 2026-10-27: agendas 2–8 go in ONE PR**
(branch `feature/redesign-rulings`, one commit per agenda, so a disputed agenda can be dropped alone). The
build follows `.interface-design/system.md`, updates `roadmap/ui-track.md`, and keeps every element the rulings
don't touch. P26's header label goes with each page's own agenda.
File pointers are where the element lives today (2026-10-27); search for the quoted text if a file moved.

1. **Browse card** — **built** (branch `feature/browse-card-hierarchy`). As built: P05 drops the prefix only
   on a card whose own type and deal are the ones the filter pins (`cohortKind` from `BrowseExperience`); a
   subtype ("Rodinný dům") stays, since the filter doesn't name it (`lib/filters` `cohortKindOf`). P03 hides the verdict on the Browse card
   only (`ImageCarousel` `renderBadge={false}`); the gallery, lightbox and the other carousels keep it until
   the render-scoring removal. P02 is `fadeTag` (opacity; the pointer on the card, or keyboard focus in it: `:focus-visible`, so a
   clicked photo arrow doesn't keep the tag shown).
2. **Browse page, map and stats** — **built** (branch `feature/redesign-rulings`). P09 (the header keeps the count and what is counted, without the rest of the
   scope sentence: `components/BrowseExperience.tsx` `FilterSummary`, text built by `lib/filters.ts` `summarise`,
   "Showing …"). **Operator ruling 2026-10-27: build the applied-filter pills with P09** (removable pills under
   the count, one per active filter, × resets that filter). P10 (merge mode
   becomes a quiet "Sloučit…" above the cards; `MergeBar.tsx`, `BrowseExperience.tsx`), P12 (drop the
   price-label button from `components/MapRail.tsx`; keep the checkbox in `ListingMap.tsx`), P13 (the popup's
   `lp-precision` and `lp-seen` "last seen …" lines become one, the precision detail in its tooltip; the popup
   HTML in `ListingMap.tsx`), P14 (chart card shows Upravit + a ⋯ menu with move up / move down / remove
   chart; `components/stats/StatsChart.tsx`), and the confirmed Stats review items (§ Stats review, **built**: as-built
   notes there), in their own commit on the same branch.
   As built: the count line reads "12 345 apartments for sale" (Browse stays English, the count mono), and
   every filter beyond the defaults is a removable pill under it (`AppliedFilterPills`), from the one list the
   phone counts (`phone/filterRows` `appliedFilters`) in the words its sheet says (`phone/filterSummary`); the
   map-area and broker chips keep their words and tooltips as pills; the pill line keeps its height on the
   defaults, so a first map pan doesn't push the page down. A URL-only min/max pair (garden area) is one
   filter now, on the phone too. "Sloučit…" sits after Cover and Sort; the bar under the tabs is the mode,
   with Cancel (a production smoke check of merge mode is owed after the deploy). The popup moved to
   `lib/listingPopupHtml`; its rung/radius and exact time are the two halves' tooltips. The ⋯ menu is the
   shared `components/OverflowMenu` (also the watchdog and collection menus); Odebrat keeps its confirm.
3. **Pipeline** — **built** (branch `feature/redesign-rulings`). P19 (remove `CityIndexStrip` from `components/pipeline/BoardCard.tsx`), P20 (the
   `mf_gross_yield_pct` span beside the dims row shows on Velké cards only, `BoardCard.tsx`), P22 (terminal
   stages, `is_terminal`, fold into one "Uzavřené" lane of drop rows; `pages/Pipeline.tsx`). Rule #22 holds:
   closing a deal is a move into a terminal stage, never a remove toggle.
   As built: the board no longer reads the city indexes. The strip was mounted only on the pipeline card, so
   no surface shows it now: the ruling's "it stays on the property page" assumed one there, and the property
   page never had it (ask the operator whether it should get one). `CityIndexStrip` and `lib/useCityQuality`
   stay on disk, unmounted. P20 is `showsYield` in the card-size table, so the drag ghost and the card agree.
   P22: the lane sits at the board's right end, a row per closed stage (its colour bar, label and count under
   the active filters); each row is a droppable under its stage's id, so a drop closes the deal through the
   unchanged `planMove` → `usePipelineCard` move. A row opens its stage's full cards under the rows (which never
   move), one row at a time, the lane widening to the card size's column. `boardCollision` hits a row by a
   point (the pointer, or on a keyboard drag the ghost's centre); the columns keep the overlap rule. The
   skeleton draws the folded lane too.
4. **Watchdogs** — **built** (branch `feature/redesign-rulings`). P24 (Delete into a ⋯ menu, keeping its confirm), P25 (created date as a grey line under the
   name, drop the column): `pages/WatchdogManage.tsx`.
   As built: the page stays English, so ⋯ holds one danger item, "Delete…", running the same confirm before
   the delete. The created date is a grey line under the name, outside its link, the full timestamp in its
   tooltip; the filter cell's cap rose from 420 to 540 px.
5. **Notifications** — **built** (branch `feature/redesign-rulings`). P27 (load older items on reaching the end, as Browse does): `pages/Notifications.tsx`,
   `components/LoadMore.tsx`.
   As built: older rows load through Browse's `InfiniteSentinel` (window scroll, the same keyset cursor, page
   size 50 and 30 s poll); the list still ends silently, with no end line. `LoadMore`'s four Czech AUTODEDUP
   callers render as before.
6. **Brokers** — **built** (branch `feature/redesign-rulings`). P28 (drop the caption under each count, `Count` in `pages/Brokers.tsx`, labels passed from
   `Ledger`; the column headers say the unit once), P29 (five firm pills, then "+ N dalších": `CompanyFilter`).
   As built: one column-head row says each unit once; each row keeps its units as screen-reader text. The five
   pills are the busiest firms (the server's order); "+ N dalších" opens the rest in place, and a chosen firm
   always stays in view. Phone: the unit is said once above the figures; the phone has no firm filter (#1760
   Q12), so P29 has nothing to collapse there.
7. **Collections** — **built** (branch `feature/redesign-rulings`). P30 (a dashed "+ Nová kolekce" card at the end of the grid opens the form;
   `pages/Collections.tsx` `NewCollectionForm`), P31 (the collection page header carries the monitoring switch,
   "Share · N" and ⋯ with rename and delete, replacing `EditBlock`, `MonitoringBlock` and `SharingBlock`), P32
   (Browse cards first, `PropertiesBlock`'s table as the second tab): `pages/CollectionDetail.tsx`. The phone
   versions (`PhoneCollectionDetail`, `PhoneBrokers`) are separate components. **Operator ruling 2026-10-27: the
   phone versions follow desktop** (Brokers P28–P29, Collections P30–P32).
   As built: the collections page stays a ruled list (turning rows into cards is a restyle no ruling names), so
   the dashed card is full width after my list and before "Shared with me", in every state of the read, and
   flips in place into the unchanged form (Cancel, Escape or a created collection flip it back). The header
   reads "Sledovat", "Sdílet · N" (an inline panel under the header holding the unchanged sharing block, like
   Pipeline's "Sdílení") and ⋯: Přejmenovat flips the name and description into the edit fields in place;
   Smazat kolekci shows the existing two-step confirm in the header, and is absent on the default collection.
   The new controls take the ruling's Czech words; the rest of the page stays English. The cards are Browse's
   `ListingCards` over the collection scope (`lib/collectionCards`, `scroll="page"`), estimates through
   `lib/useCardEstimates` (Browse's state, moved out of `BrowseExperience`); `?tab=table` and `?sort=` live in
   the address; members without a card are counted under the grid and all listed in the table. Phone: "Nová
   sbírka" moved to the end of my list, dashed, keeping its bottom sheet; phone P32 was already cards with no
   table, so nothing changed; phone P31 has no blocks to move (its footer sends rename, delete, monitoring and
   sharing to the desktop), so adding Sledovat / Sdílet / ⋯ to the phone header **awaits the operator's answer**.
8. **Shell and page headers** — **built** (branch `feature/redesign-rulings`). P26 (drop the small-caps label above the h1; there is no shared header
   component, each page has its own: `Header` in `pages/WatchdogManage.tsx`, `pages/Notifications.tsx`,
   `pages/Collections.tsx` and `pages/CollectionDetail.tsx`, and an inline header in `BrokersDesktop` in
   `pages/Brokers.tsx`), P33 ("!AUDIT POLOH" moves to the top of the Admin menu, its count on the Admin trigger;
   `components/Shell.tsx`), P34 (drop the "read-only · scrape data" build line from the footer, `Shell.tsx`).
   P35 is design-system only (Terakota dropped; nothing to build).
   As built: "!AUDIT POLOH" is the first Admin item, above Health; its brick count sits on the Admin trigger
   (`AuditCountBadge`), and a failed count read leaves the trigger badge-less. P34's footer keeps the map-tile
   attribution at the right edge. P26 also dropped the label on the Estimations, Watchdog edit, Datasets and
   Outreach pages (and on /brokers/review with agenda 6); `pages/pageTitleLabels.test.ts` pins every label
   left above an h1 (only the error and not-found states keep one).

Several of today's screens are in English ("Browse", "12 345 apartments for sale", "Manage watchdogs", "Curated lists"). The
design system draws them in Czech; translating them is one of the unruled additions below, so keep each
screen's language as it is unless the operator rules otherwise.

## Render scoring: remove end to end (operator note on P03)

Not started. Scope it first and send the operator the list of what would go before building anything.
Known so far:
- Producer: `scraper/clip_tagger.py` writes a per-image render score (migration `239_image_clip_render_score.sql`;
  see also 245, 246, 456, 496 and 416 for where it reaches views).
- Front end: `components/ImageRenderBadge.tsx`, `lib/imageTags.ts`, the gallery, lightbox, carousel, map preview,
  sold comps, origin panel and the autodedup member rows read it (grep the front end for `render`).
- **Check before removing**: migration 239 notes that the old dedup engine excluded high-render images from
  its photo-match signal. Find whether autodedup or any SQL function still reads the score (CLAUDE.md rule 15:
  the CLIP signal producers stay live), and say so in the scope.
- Dropping a column is a destructive migration: it pauses for the operator's OK and a backup (`database`
  skill). Stopping the producer and every reader can come first, the column drop last.

## Shown in the design system but not ruled

These are additions in the previews that the operator has not ruled on yet. Ask before building any of them:
a Ctrl K "Přejít na…" jump box; a "Najít filtr" box and
active-count badges on filter bands; Σ asking-price sums on the pipeline and its stage headers; "days in stage"
on pipeline cards; a 7-day alert count on watchdogs; notifications grouped by day with a photo per row and the
price move as a struck-through old price; a share bar in the broker leaderboard; one look for every switch;
segmented controls that show the chosen segment raised instead of filled ("selection is not action");
Czech labels everywhere (several screens are English today).

## Updating the design system artifact

Its files are `project/README.md`, `project/tokens.json`, `project/components/bundle.css` and a `README.md` +
`preview.html` per component, plus the index `project/design-system.json`. Read the files you change, edit
them, and publish to the same URL with `root` set to the folder that holds `project/` and a `files` map of the
changed paths (a previous session's generator scripts were session-local and are gone; edit the files
directly). Keep `lastChange.note` in the index current.

## Suggested opening for the next session

> Read `docs/design/propscout-redesign.md` and `.interface-design/system.md`. Every agenda and the section
> cards are built; what is left is the render-scoring scope (send the operator the list first) and the open
> questions under agendas 3 and 7.
