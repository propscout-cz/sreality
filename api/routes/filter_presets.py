"""Browse saved filter presets: the saved-set instance over `filter_presets` (api/saved_sets.py).

Mounted under `/filter-presets/*`, per account (ticket #1761, migration 290's policy is the boundary). A preset
is a named filter set restored client-side, so there is no matcher and no background work, and the blob is
stored verbatim once its place chips are coded. Deliberately decoupled from the Watchdog (`/notifications/*`)
so a preset can never fire a notification (migration 151).
"""

from __future__ import annotations

from api import saved_sets
from api import schemas as s

PRESETS = saved_sets.SavedSetKind(
    table="filter_presets",
    noun="preset",
    id_param="preset_id",
    blob_columns=("filter_spec",),
    create_in=s.CreatePresetIn,
    update_in=s.UpdatePresetIn,
)
# Every statement the kind runs, for the CI PREPARE sweep (tests/sql_corpus import-resolves `*_SQL`).
PRESET_SQL = saved_sets.statements(PRESETS)

router = saved_sets.saved_set_router(PRESETS, "/filter-presets")
