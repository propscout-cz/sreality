# PropScout.cz redesign: handoff

Status 2026-10-27. This is the one place to pick the redesign up from. It records what is done, what the
operator ruled, and what is next. Read it top to bottom before touching any of the screens named here.

## Where everything lives

| What | Where | Binding? |
| --- | --- | --- |
| Rename + three colour schemes (built) | PR waiff/sreality#2115, merged and live 2026-10-27 | Yes |
| Design rules for every build | `.interface-design/system.md` (§ Colour schemes, § Hierarchy rulings) | **Yes** |
| Design system artifact: previews of every agenda, as ruled | https://claude.ai/artifact/NstEVRTDXyQ8ZNJhEpxv7m | Direction only |
| Removal review: 35 before/after pictures and the operator's answers | https://claude.ai/artifact/WoFQVG1RwmaqXAfBBQoqbV | The rulings below are copied from it |
| Stats review: 10 proposals for Browse › Stats (S01–S10), pictures and answers | https://claude.ai/artifact/RRDNo2TfAa7iWuMUoUBMfg | **Yes, read live** (§ Stats review below) |
| Sequencing | `roadmap/ui-track.md` § PropScout.cz and three colour schemes | Yes |

Reading the artifacts from a session: use the Artifact tool, `action: "read"` with the URL (add `path` for
one file, e.g. `project/README.md` or `project/components/ListingCard/preview.html`). The review page's answers
are in its database, collection `proposals` (documents `p01`–`p35`, fields `decision`, `note`), readable with
the ArtifactData tool (`action: "list"`); the Stats review works the same way (documents `s01`–`s10`). Never
fetch these links with curl or WebFetch.

## What is done

1. **PR #2115** (merged 2026-10-27; Railway deployed API, vite and realtime-worker):
   - the app is **PropScout.cz** (`lib/brand.ts`; tab titles "PS: …"; a "PS" home-screen icon; extension name);
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

Nothing from the rulings is built in the app yet. Merging #2115 ships the name and the schemes only.

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
| P08 | Browse page | Fold the preset bar into the tab row | Declined | Keep as is: A row of its own: Pipeline chip, Presety fold, every preset with grip · name · ⋯, group boxes, Aktualizovat / Uložit preset / Nová skupina. |  |
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
operator rules them there. **This review is read live, not copied here:** before building the Stats tab, list
its `proposals` collection with ArtifactData and build exactly what it says.

- `decision: "confirm"` → build it, in the agenda-2 PR (Browse page, map and stats), alongside P14.
- `decision: "decline"` → leave that part as it is; don't propose it again.
- `decision: null` → not ruled yet: ask the operator before building it, and build the rest.
- A `note` goes further than the proposal where they differ, as in the removal review.

The proposals in short (the page's pictures are the spec): S01 one panel for the board's settings; S02 visible
row labels "Dashboard" and "Vzorek"; S03 the sample sentence under its switch, the chosen option in bold; S04
"Malé vzorky (pod 5)" at the end of the Vzorek row, its explanation in a tooltip; S05 the sample said once for the
board ("4 nemovitosti ve vzorku · byty · prodej · aktivní i neaktivní"), charts keep only differing chips; S06
an "Okolí" badge on the okolí dashboard's chip; S07 Czech tab labels "Nemovitosti" / "Statistiky"; S08 chart
titles lead with the measure ("Cena", then "podle Plocha po 10 m² › Stav"); S09 a one-line note with "Zobrazit
malé vzorky" when every cell is under 5; S10 the box legend only on charts that draw a box. Code pointers are
in each proposal's `where` field (`components/stats/StatsDashboard.tsx`, `DashboardBar.tsx`, `SampleLedger.tsx`,
`StatsChart.tsx`, `lib/stats.ts` `MARKET_CAPTION`). The hover rule holds: S04's tooltip is an overlay.

## What to build next

Build the confirmed items **one PR per agenda**, in this order (small and visible first). Each PR follows
`.interface-design/system.md`, updates `roadmap/ui-track.md`, and keeps every element the rulings don't touch.
File pointers are where the element lives today (2026-10-27); search for the quoted text if a file moved.

1. **Browse card** — P01, P02, P03, P05 (and the P06 chip restyle): `frontend/src/components/ListingCards.tsx`
   (the photo's badge stack, the room tag, the verdict badge from `components/ImageRenderBadge.tsx`, the title,
   `storeyTagOf`, the card foot). P05: keep the "Byt na prodej ·" prefix only when the filter spans several
   property types or both sale and rent. P02 must be an overlay (opacity), never a layout change.
2. **Browse page, map and stats** — P09 (the header keeps the count and what is counted, without the rest of the
   scope sentence: `components/BrowseExperience.tsx` `FilterSummary`, text built by `lib/filters.ts` `summarise`,
   "Showing …"). **P09's proposal is conditional on the applied-filter pills, which are an unruled addition**:
   ask the operator whether to build the pills with P09 or keep the sentence until they exist. P10 (merge mode
   becomes a quiet "Sloučit…" above the cards; `MergeBar.tsx`, `BrowseExperience.tsx`), P12 (drop the
   price-label button from `components/MapRail.tsx`; keep the checkbox in `ListingMap.tsx`), P13 (the popup's
   `lp-precision` and `lp-seen` "last seen …" lines become one, the precision detail in its tooltip; the popup
   HTML in `ListingMap.tsx`), P14 (chart card shows Upravit + a ⋯ menu with move up / move down / remove
   chart; `components/stats/StatsChart.tsx`), and the confirmed Stats review items (§ Stats review, read live).
3. **Pipeline** — P19 (remove `CityIndexStrip` from `components/pipeline/BoardCard.tsx`), P20 (the
   `mf_gross_yield_pct` span beside the dims row shows on Velké cards only, `BoardCard.tsx`), P22 (terminal
   stages, `is_terminal`, fold into one "Uzavřené" lane of drop rows; `pages/Pipeline.tsx`). Rule #22 holds:
   closing a deal is a move into a terminal stage, never a remove toggle.
4. **Watchdogs** — P24 (Delete into a ⋯ menu, keeping its confirm), P25 (created date as a grey line under the
   name, drop the column): `pages/WatchdogManage.tsx`.
5. **Notifications** — P27 (load older items on reaching the end, as Browse does): `pages/Notifications.tsx`,
   `components/LoadMore.tsx`.
6. **Brokers** — P28 (drop the caption under each count, `Count` in `pages/Brokers.tsx`, labels passed from
   `Ledger`; the column headers say the unit once), P29 (five firm pills, then "+ N dalších": `CompanyFilter`).
7. **Collections** — P30 (a dashed "+ Nová kolekce" card at the end of the grid opens the form;
   `pages/Collections.tsx` `NewCollectionForm`), P31 (the collection page header carries the monitoring switch,
   "Share · N" and ⋯ with rename and delete, replacing `EditBlock`, `MonitoringBlock` and `SharingBlock`), P32
   (Browse cards first, `PropertiesBlock`'s table as the second tab): `pages/CollectionDetail.tsx`. The phone
   versions (`PhoneCollectionDetail`, `PhoneBrokers`) are separate components: decide per PR whether they follow.
8. **Shell and page headers** — P26 (drop the small-caps label above the h1; there is no shared header
   component, each page has its own: `Header` in `pages/WatchdogManage.tsx`, `pages/Notifications.tsx`,
   `pages/Collections.tsx` and `pages/CollectionDetail.tsx`, and an inline header in `BrokersDesktop` in
   `pages/Brokers.tsx`), P33 ("!AUDIT POLOH" moves to the top of the Admin menu, its count on the Admin trigger;
   `components/Shell.tsx`), P34 (drop the "read-only · scrape data" build line from the footer, `Shell.tsx`).
   P35 is design-system only (Terakota dropped; nothing to build).

Several of today's screens are in English ("Browse", "Showing …", "Manage watchdogs", "Curated lists"). The
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
a Ctrl K "Přejít na…" jump box; applied filters as removable pills above the results; a "Najít filtr" box and
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

> Read `docs/design/propscout-redesign.md` and `.interface-design/system.md`, then build the next
> unbuilt agenda from "What to build next" as one PR.
