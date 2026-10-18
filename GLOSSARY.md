# Property intelligence platform

One market-wide view of Czech real estate built from the ads nine portals publish. Code and tables
still carry some older names; the terms below are the ones to use in prose, UI text, documents and
anything newly named.

## Language

### The two primary terms

**Property**:
One real-world unit for sale or rent as the platform knows it: the group of every ad that advertises
that same unit. Every ad belongs to exactly one property.
_Avoid_: card, group, cluster, record, listing group

**Ad**:
One advertisement as one portal publishes it, under that portal's own id. The thing the scrapers
collect and keep history for.
_Avoid_: listing, advert, offer, inzerát (in English prose)

### Around a property

**Singleton**:
A property with exactly one ad.
_Avoid_: standalone, unmerged property

**Canonical ad**:
The one ad whose facts a property shows: price, area, description, photos and link. Chosen by a fixed
order, never by hand.
_Avoid_: representative ad, repr, speaking ad, main ad, headline ad

**Duplicate**:
Two ads that advertise the same unit and therefore belong in one property.
_Avoid_: dupe, match, twin

**Re-list**:
A new ad for a unit whose earlier ad ended, on the same portal or another. A re-list is a duplicate.
_Avoid_: repost, relisting, re-listing

**Revival**:
The same ad, same portal id, showing again after it had disappeared.
_Avoid_: re-list (that is a new ad), reactivation

**Active ad / inactive ad**:
An ad is active while its portal still shows it and inactive once it has disappeared. Inactive ads
are kept forever.
_Avoid_: live, dead, delisted, deleted, removed, expired

**Portal**:
One of the advertising websites the platform collects from.
_Avoid_: site, source (in prose)

**Snapshot**:
One kept version of an ad's content. A new snapshot exists only when the content changed.
_Avoid_: version, revision, scrape

### Changing what belongs together

**Merge**:
Joining two or more properties into one, so that all their ads form one property.
_Avoid_: dedup (for the act), link, join, group (verb), unify, absorb

**Split**:
Taking one or more ads out of a property, into another property or into a new one.
_Avoid_: unmerge, detach, undo, separate

**Letter**:
In a split, the mark the user gives each ad; ads with the same letter are one property afterwards;
one letter keeps the property's number and page.
_Avoid_: unit, part, side, group

**Survivor**:
The property that keeps its identity: after a merge, the one that remains; after a split, the one
that keeps its number and its page.
_Avoid_: keeper, winner, target

**Ruling**:
A user's recorded verdict that two ads are the same unit or different units. Rulings bind the engine.
_Avoid_: label, verdict, decision (for this), judgement

**Engine**:
The automatic duplicate detector that proposes merges and performs them inside its allowed area.
_Avoid_: dedup, matcher, autodedup (in prose), new dedup

**Removed engine**:
The earlier automatic detector. Its merges remain in the data; its logic was deleted and is never
consulted.
_Avoid_: old engine, legacy engine, legacy dedup

### What users add

**Curation**:
Everything a user adds to a property by hand: notes, a pipeline card, collection membership, tags,
a dismissal. Curation belongs to the user's account and follows the property.
_Avoid_: operator state, user-defined information, user-created state, carried state

**Pipeline card**:
A property's place in one account's deal pipeline. At most one per property per account.
_Avoid_: deal, card (alone)

**Stage**:
One step of one account's pipeline, drawn as a board column; every account gets its own copy of the default board at signup.
_Avoid_: fáze (in code), column

**Collection**:
A named set of properties an account keeps, optionally watched for changes. It may be shared
read-only with other accounts.
_Avoid_: list, folder, favourites, saved list

**Share**:
A read-only grant of one collection to one account. The owner revokes it, the recipient may leave
it; the recipient can never change the collection.
_Avoid_: invite, membership, access (alone), collaborator

**Shared note**:
A note its author marked for the people they share its property with: readable by the other party
of any share the author is party to whose collection holds that property, including shares made
after the note was marked. Unmarked notes stay private.
_Avoid_: public note, team note

**Tag**:
A coloured label an account puts on a property; a property carries any number. A tag filter
has two sides: must carry every chosen tag, must carry none of the excluded ones.
_Avoid_: štítek (in code), label, category, flag

**Dismissal**:
An account's decision to hide a property from its own Browse.
_Avoid_: hide, mute, ignore, archive

**Fold**:
An item of one account's curation that gives way in a merge: the losing one of two of a kind (two
pipeline cards, collection entries, tags or dismissals), or a dismissal that a live deal lifts. It
is kept in the carry record, never lost.
_Avoid_: absorb, collapse, drop, dedupe

**Carry record**:
What a merge writes down about each item of curation it moved or folded, so a split can send it
back where it came from.
_Avoid_: carry log, curation ledger, snapshot

**Watchdog**:
A saved filter that alerts an account when a property newly matches it or drops its price.
_Avoid_: alert (for the saved filter), subscription, notification

**Broker contact record**:
A broker's email and phone that one account captured from one ad's portal page after showing
interest in that ad. It belongs to that account and stays tied to the ad, not the property.
_Avoid_: lead, contact (alone), broker contact (alone), revealed contact

### AI work

**Assistant**:
A user's own AI chat account, such as Claude or ChatGPT, acting for that user.
_Avoid_: chatbot, bot, AI platform

**Workflow**:
A named piece of AI-assisted work a user starts to get a result, such as an area takeoff, a yield
valuation or a rent estimation.
_Avoid_: job, automation, task

**Skill**:
The instructions an AI follows to carry out a workflow, whether it runs in a user's assistant or
inside the platform.
_Avoid_: prompt, agent profile, recipe

**Run**:
One execution of a workflow, with its inputs and its result. A run usually concerns one property.
_Avoid_: job, execution, session

**Area takeoff**:
The workflow that measures a property's rentable floor areas from its plans.
_Avoid_: offtake, area offtake

### Health

**Check**:
One fact about the platform's own health, measured on a fixed schedule and judged OK, Watch,
Problem or Not measured. Every check is either an alarm or a gauge.
_Avoid_: test, probe, monitor, health metric

**Cell**:
One verdict inside a check, about one part of what the check watches, such as one portal or one
scheduled job.
_Avoid_: row, item, sub-check

**Alarm**:
A check for something that should not happen, such as a portal that stops finding new ads. Its
Problem is told at once and reminded 6 hours, a day and three days after it began, then weekly,
until it recovers or is acknowledged.
_Avoid_: alert (that is the notice), trigger

**Gauge**:
A check of a standing condition the platform lives with while it is being fixed, such as ads that
still lack a town, shown with its trend. Its Problem is told once, then reminded once a day until it
recovers or is acknowledged.
_Avoid_: metric, indicator, KPI

**Incident**:
One unbroken spell of trouble in one cell, from its first Problem until a measured OK has held long
enough to count as recovery.
_Avoid_: outage, issue, ticket, problem (for the record)

**System alert**:
A notice about the platform's own health: an incident has started, is still open, or has recovered.
It appears among the app's notifications; a watchdog's notice about properties is not one.
_Avoid_: alarm (that is the check), alert (alone), health notification

**Acknowledge**:
An admin's deliberate act, on the Health page, of marking an incident as known, which stops its
reminders. Reading its system alert among the notifications does not acknowledge it.
_Avoid_: dismiss, mute, snooze, resolve, mark read

**Not measured**:
The verdict of a check that has no current measurement: it ran out of time, did not run, failed,
found nothing to judge, or is overdue. It never counts as OK, and it neither opens an incident
nor counts as its recovery.
_Avoid_: unknown (in prose), skipped, pending, OK

### Where users meet the platform

**Extension**:
The platform's Chrome extension, which shows a property's data and actions on portal pages.
_Avoid_: plugin, add-on

**Plugin**:
The package a user installs into their assistant to connect it to the platform: the skills plus the
connection to the platform's tools.
_Avoid_: connector, extension, integration

**Phone app**:
The platform's web app installed on a phone's home screen: the same app in a phone-sized shell.
_Avoid_: PWA (in prose), mobile app, native app, companion app

**Reference overlay**:
A map layer drawn from an outside public dataset — metro routes, flood zones, kraj borders, the MF
rent map, university sites — that a user switches on or off; it changes what the map shows around properties, never
which properties.
_Avoid_: data layer, overlay data, map data, GIS layer

**Count mark**:
The number a map shows for one world tile when more properties are in view than the pin limit (a
number the user sets above the map, 200 by default): exactly that many, in a pill at the tile's
centre. At or under the limit every property is its own pin, at any zoom. One tile holds one mark;
the marks on a map add up to the mapped total.
_Avoid_: cluster, bubble, cell, bin, group

### A property's working files

**Workbook**:
The Google Sheets file that holds a user's working figures for one property.
_Avoid_: spreadsheet, sheet, model

**Tab**:
One sheet inside a workbook, holding one kind of information.
_Avoid_: sheet, worksheet
