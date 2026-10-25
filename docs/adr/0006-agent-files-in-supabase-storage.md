---
status: accepted
date: 2026-10-25
---
# Agent-generated files live in Supabase Storage

PROGRAM.md (D11) preferred a new EU-jurisdiction Cloudflare R2 bucket for agent files, next to the photos. The operator
chose Supabase Storage: the website reads a user's files directly under the same row-level rules as the rest of the
data, with no proxy routes to write; the plan includes 100 GB; the bytes sit in Ireland beside the database. Files are
recorded in an artifacts table linked to the run, the account and, where it applies, the property. Photos and user
attachments stay on R2. Reopen if egress fees or direct browser uploads at scale make R2 cheaper. Operator ruling Q22
of 2026-10-25.
