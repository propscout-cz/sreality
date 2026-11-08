---
status: accepted
date: 2026-11-07
---
# The settlement unit is an exact place level: it holds only the properties known to lie inside it

People search by names such as Barrandov or Spořilov that are not a place we can filter by; Mapy.cz suggested them,
and the pick matched no register place and was lost. They are the state register's settlement units (základní sídelní
jednotky), 23,604 of them, each tied to one cadastral area of one town and named in the ČÚZK package we already
download. The operator chose c1, against the recommendation of c2 (or c3): the settlement unit becomes an exact place
level, and a property is in a settlement unit only when the most precise location its location ad has (its RÚIAN
door, or else its street, quarter or town) lies wholly inside that one settlement unit; a portal's map pin alone never
places it. The rejected options were a signpost that adds the name's whole quarter or quarters under a label (c2:
full coverage, but wider than the name) and a signpost now with the exact level later (c3). With c1 the operator asked
for a waterfall that shows how many properties end at which level. What c1 keeps is that a pick never filters wider
than its name, at the cost of coverage: measured on 2026-11-07, about 57 % of the served Czech properties can be placed
in a settlement unit (every one with a RÚIAN door, about 58 % of those known at a street, 56 % of those known at a
quarter, and those in the 2,781 towns that are one settlement unit). The gap is shown, not hidden: Browse shows a quiet line under its result count saying how many properties in the quarter or quarters the
settlement unit lies in are not placed precisely enough for the settlement unit, with a link to those quarters
(Q17b). The watchdog matches exactly the properties Browse does, because "matches" has one definition (rule 16). The
Location quality page shows a waterfall of how many properties end at which level, shipped first, from today's
levels (Q18). This reverses migration 718, which retired the settlement-unit rows because nothing bound or served
them. Reversing again would take a destructive rewrite: presets, watchdogs and links will store settlement-unit
picks, which a removed level silently drops (widening the search) or invalidates (the watchdog stops firing). The net
addition (loader, level, column, filter) that rule 25 makes subject to a ruling is approved as its own ruling (Q21a),
not netted against deleting the Mapy.cz code. Operator rulings Q7 (c1) of 2026-11-06 and Q13–Q18, Q21 of 2026-11-07
(Mapy exit grill).
