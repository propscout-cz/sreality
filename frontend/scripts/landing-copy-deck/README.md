# Landing copy deck

An artifact where the operator edits the propscout.cz landing copy in place. It shows the built pages (`frontend/landing/`)
with every text editable, plus a tab for the texts that appear only in some states (tab titles, link previews, form
errors, share text). Artifact: https://claude.ai/artifact/BCWsBLb1Vj78wSdmJgd73x, slug `5296f91e-3c0b-43e8-8632-ef056230f833`
(owner: the operator; private until they share it).

**Where edits live:** the artifact's db, collection `edits`, one document per text, keyed by a hash of the PUBLISHED
text (`deck.js` `fnv`). Fields: `original` / `originalHtml` (what the page says now), `text` / `html` (the edit),
`context`, `pages`, `variant`, `legal`, `count`, `status` (`pending` | `applied`), `note`, `updatedAt`.

**Rebuild after any landing change** (keeps the deck mirroring the live page; edits survive because they key on text):

1. `npm run build` in `frontend/`, then `node scripts/landing-copy-deck/build.mjs` → `frontend/.copy-deck/` + the file list.
2. Republish with `publish_files` (slug + base_version from `get_artifact`, `mode: "replace"`, the printed list).
   The page declares `db` + `user`; republishing keeps the declaration.

**Applying edits:**

1. `db_query` collection `edits` where `status == "pending"`.
2. For each, find `originalHtml` in `frontend/landing/*.html` and replace it with `html`. Hidden texts are in
   `<title>`/`<meta>` or `src/landing/{shared,main,thanks}.ts`.
   - An edit flagged `legal` that touches the consent line also needs a new `api/waitlist.py` CONSENT_TEXTS version and
     the forms' hidden `consent` value. Never edit a published version.
   - Re-run `tests/api/test_waitlist_routes.py`.
3. Ship the PR. After the deploy, rebuild and republish the deck, then set each applied edit's `status` to `"applied"`
   (`db_write` update with `ifVersion`). An edit whose text is no longer on the page shows under "Text changed" in the
   deck: read it before applying.
