"""Guard the CLIP encoder pin — the rail that keeps 10.36M vectors comparable.

`image_clip_embeddings` is keyed (image_id, model) where `model` is a NAME. Passing
that name to from_pretrained() without a revision resolves it to whatever the
HuggingFace hub's head holds at download time, so an upstream re-upload would change
every vector written afterwards while the `model` column stayed byte-identical — two
incomparable populations, no way to tell them apart, every per-tag centroid quietly
wrong. Migration 456 adds the `revision` column; these tests keep the code side from
regressing, because that failure is invisible at runtime.

AST, not text matching: a `revision=` substring elsewhere in the file would satisfy a
grep while the actual call site had dropped it.
"""

import ast
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

# Loaders that legitimately do NOT pin, with the reason. Kept as an explicit map so
# adding a file here is a deliberate, reviewed act rather than a silent exemption.
_UNPINNED_ALLOWED = {
    # Secondary proposal tagger. Its checkpoint is operator-chosen at runtime
    # (`labeling_secondary_model`, toolkit/dedup_sim_settings.py), so there is no
    # file to pin it in — and it writes label PROPOSALS, never a row of
    # image_clip_embeddings, so it cannot contaminate the vector corpus.
    "scraper/label_proposal_tagger.py",
}

_TAGGING_WORKFLOWS = (
    "clip_tag.yml",
    "label_proposal_backfill.yml",
)


def _from_pretrained_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_pretrained"
    ]


def test_every_embedding_loader_pins_a_revision():
    offenders: list[str] = []
    for path in sorted((_ROOT / "scraper").rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text())
        for call in _from_pretrained_calls(tree):
            pinned = any(kw.arg == "revision" for kw in call.keywords)
            if pinned or rel in _UNPINNED_ALLOWED:
                continue
            offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "from_pretrained() without revision= at "
        + ", ".join(offenders)
        + " — an unpinned checkpoint silently changes every vector it writes. "
          "Pin it, or add the file to _UNPINNED_ALLOWED with the reason it writes "
          "no rows to image_clip_embeddings."
    )


def test_allowlisted_files_still_exist():
    # An allowlist entry for a deleted/renamed file is a hole that looks like a rule.
    for rel in _UNPINNED_ALLOWED:
        assert (_ROOT / rel).exists(), f"_UNPINNED_ALLOWED names a missing file: {rel}"


def test_clip_extra_bounds_transformers():
    # The revision pin fixes the WEIGHTS; this bounds the LIBRARY, so a major release
    # cannot change the from_pretrained/CLIPModel API under the tagging workflows.
    spec = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    deps = spec["project"]["optional-dependencies"]["clip"]
    transformers = [d for d in deps if d.startswith("transformers")]
    assert transformers, "the clip extra must declare transformers"
    assert "<5" in transformers[0], (
        f"transformers needs an upper bound, got {transformers[0]!r}"
    )


def test_embedding_writer_stamps_the_revision():
    # The column exists (456) only to answer "which weights made this row?" — a writer
    # that never fills it leaves the question unanswerable for everything it wrote.
    sql = (_ROOT / "scripts" / "clip_tag_backfill.py").read_text()
    assert "INSERT INTO image_clip_embeddings (image_id, model, revision, embedding)" in sql
    assert "revision  = EXCLUDED.revision" in sql


def test_tagging_workflows_exist():
    # These two are the only installers of the clip extra; if one is renamed the
    # pin's blast radius changes and this file's assumptions need re-checking.
    for name in _TAGGING_WORKFLOWS:
        assert (_WORKFLOWS / name).exists(), name


def test_retag_runs_in_the_tagging_workflow_after_tag_in_each_shard():
    # One torch install per shard serves both: the hourly retag workflow installed it four times
    # an hour to find nothing to re-tag (#2014).
    assert not (_WORKFLOWS / "clip_retag.yml").exists()
    doc = yaml.safe_load((_WORKFLOWS / "clip_tag.yml").read_text())
    (job,) = doc["jobs"].values()
    assert job["strategy"]["matrix"]["shard"] == [0, 1, 2, 3]
    runs = [step.get("run", "") for step in job["steps"]]
    tag = next(i for i, run in enumerate(runs) if "scripts.clip_tag_backfill" in run)
    retag = next(i for i, run in enumerate(runs) if "scripts.retag_from_embeddings" in run)
    assert tag < retag
    assert "--shards 4 --shard ${{ matrix.shard }}" in runs[retag]
    # A failed tag step (an R2 outage) must not stop the retag, which reads only stored vectors;
    # a failed install must (it would only fail again on the import).
    condition = job["steps"][retag]["if"]
    assert "!cancelled()" in condition and "steps.install.outcome == 'success'" in condition
    assert "inputs.dry_run != 'true'" in condition
    # Its budget is what is left of the job's clock after tagging, never a fixed slice of it.
    assert "JOB_STARTED" in runs[retag] and '--max-seconds "$BUDGET"' in runs[retag]
    assert any('JOB_STARTED=$(date +%s)' in run for run in runs[:tag])
