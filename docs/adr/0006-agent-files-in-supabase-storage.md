---
status: accepted
date: 2026-10-25
---
# Agent-generated files live in Supabase Storage

PROGRAM.md (D11) preferred a new EU-jurisdiction Cloudflare R2 bucket for agent files, next to the photos. The operator
chose Supabase Storage: the website reads a user's files directly under the same row-level rules as the rest of the
data, with no proxy routes to write; the plan includes 100 GB; the bytes sit in the project's region (Ireland), per Supabase's statement that storage follows the project region
(RESEARCH §6.6, rated likely). Files are
recorded in an artifacts table linked to the run, the account and, where it applies, the property. Photos and user
attachments stay on R2. Reopen if egress fees or direct browser uploads at scale make R2 cheaper. Operator ruling Q22
of 2026-10-25.

*Amended 2026-11-01 (#2073), for the operator's confirmation:* the website reads a file through the API
(`GET /runs/{id}/files/{file}`), not directly from Storage. A direct read comes from another origin with headers we do
not set, and a storage policy could not be tested in CI, where Storage does not run. The route reads the row under the same
row rules and sets the download headers itself. It proxies a read, so the "no proxy routes" above no longer holds, and the
API holds the project's secret key, in a service that already holds the database password. Storage stays the store, for
the same reasons.
