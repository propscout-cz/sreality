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
import re
import tomllib
from pathlib import Path

from scraper import jobs
from tests._railway_iac import iac_entry, iac_text

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

_TAGGING_WORKFLOWS = ("label_proposal_backfill.yml",)
# The runner image is the other installer: built by Railway and by its CI check, never by a pip step (#2032).
_IMAGE_WORKFLOWS = ("runner_image.yml",)
_PIP_CLIP = re.compile(r"\.\[[\w,]*\bclip\b[\w,]*\]")
_EXTRAS_CLIP = re.compile(r"EXTRAS=[\w,]*\bclip\b")
_RUNNER_EXTRAS = 'EXTRAS: "api,clip,geo"'


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


def clip_installers(workflows: dict[str, str]) -> tuple[set[str], set[str]]:
    """(files that pip-install the clip extra, files that build an image whose EXTRAS name clip)."""
    return ({f for f, text in workflows.items() if _PIP_CLIP.search(text)},
            {f for f, text in workflows.items() if _EXTRAS_CLIP.search(text)})


def test_the_clip_extra_has_exactly_its_known_installers():
    # Each installer resolves the clip stack on its own: a new one widens the pin's blast radius, so this file's
    # assumptions need re-checking before it lands.
    workflows = {p.name: p.read_text() for p in sorted(_WORKFLOWS.glob("*.yml"))}
    assert clip_installers(workflows) == (set(_TAGGING_WORKFLOWS), set(_IMAGE_WORKFLOWS))
    assert iac_text().count(_RUNNER_EXTRAS) == 1 and _RUNNER_EXTRAS in iac_entry("jobRunner")
    stray = {**workflows, "stray.yml": 'steps:\n  - run: pip install -e ".[api,clip]"\n'}
    assert clip_installers(stray)[0] == {*_TAGGING_WORKFLOWS, "stray.yml"}


def _calls_in_order(function: ast.FunctionDef) -> list[str]:
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    return [ast.unparse(c.func) for c in sorted(calls, key=lambda c: (c.lineno, c.col_offset))]


def test_clip_tags_then_retags_in_one_runner_job():
    # One model load serves both: the GitHub tag and retag workflows installed torch per shard (#2014, #2032).
    assert not (_WORKFLOWS / "clip_tag.yml").exists() and not (_WORKFLOWS / "clip_retag.yml").exists()
    clip = next(j for j in jobs.declared() if j.name == "clip")
    assert clip.host == jobs.RUNNER and clip.argv[0] == "scripts.clip_tag_backfill"
    tree = ast.parse((_ROOT / "scripts" / "clip_tag_backfill.py").read_text())
    assert any(isinstance(n, ast.ImportFrom) and n.module == "scripts"
               and "retag_from_embeddings" in {a.name for a in n.names} for n in tree.body)
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    calls = _calls_in_order(run)
    assert "_tag" in calls and "retag_from_embeddings.retag" in calls
    assert calls.index("_tag") < calls.index("retag_from_embeddings.retag"), "retag runs after the tag loop"
