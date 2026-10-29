---
status: accepted
date: 2026-10-25
---
# Agents get the same data access as the screens, under the user's own login

The agent set is open-ended, so the question was what data an agent may reach. The alternatives were a curated set of
agent operations (the first proposal) or parity with the screens. We chose parity: every database function a screen
calls (Browse search over all its filters, the stats dashboard, map counts and overlays, sold comparables, collections,
pipeline, notes, brokers) and every dataset we hold, including regional datapoints and the curated city indexes, is a
typed operation run under the user's own login through the tenant pool, so row-level security decides what it sees. One exception stands until the narrow database logins arrive: the
toolkit's market-wide analytics (comparables, velocity, distribution) keep reading base tables on the service
connection, as they do today, and return market facts only; every entry that touches a user's own data runs under the
user's login.
A screen capability an agent lacks is a bug. Two edges were ruled with it: city indexes may inform an agent that ranks
but never a price estimate (rule 17 amended), and agents see broker contacts as the user sees them, at most 20 per
call and every read recorded on the run, while assistants connected through MCP stay masked. Operator ruling Q8/Q14, Q20,
Q29 of 2026-10-25 (`docs/design/agent-platform/MVP.md` § 4).

*Amended 2026-10-29 (#2063, operator ruling of that day):* the property page's broker read is the second
service-connection read. The broker relations are the API's alone (Phase 0 A6), so no login reads them;
`get_property_brokers` reads them as `POST /brokers/by-listings` does, unmasked as the page shows every signed-in
user, at most 20 people per call, each call recorded on the run, bounded like every market read (READ ONLY, 8 s). The
dossier carries no broker.
