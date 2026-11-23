# Legacy skills (reference only)

These four folders lived in the repo-root `skills/` until #2065 (2026-10-29). They are kept here as reference for
reworking the estimator in M2 (#2076). Nothing loads them, and none of them is a runnable agent.

| Folder | What it documents | Where the live text is |
|---|---|---|
| `rental_estimator_full_v1`, `rental_estimator_v1` | the estimator's prompt, tools and limits | the `skills` table row (edited in Settings; every version in `skills_history`) |
| `skill_refiner_v1` | the refiner's single call | `app_settings.llm_skill_refiner_system_prompt` |
| `building_unit_extractor_v1` | the building extractor's single vision call | `app_settings` (`llm_building_extractor_*`) |

The files have drifted from those rows: AUDIT F31 counted three different texts of `rental_estimator_full_v1`.
Read them as history, not as the current prompt. The repo-root `skills/` now holds agents only (its README).
