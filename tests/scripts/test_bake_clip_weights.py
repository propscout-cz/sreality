"""scripts/bake_clip_weights.py: the job-runner image's CLIP layer is keyed on the checkpoint pin alone, and it
downloads exactly the pinned revision's files, retried as Tagger.load retries (#2025). No network: a fake hub."""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import bake_clip_weights

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = ROOT / "data" / "clip_taxonomy.json"


def test_the_pin_is_the_taxonomy_s_model_and_revision_and_nothing_else(tmp_path: Path) -> None:
    """A prompt edit must reuse the weights layer: the pin file carries no field a prompt edit changes."""
    out = tmp_path / "clip_checkpoint.json"
    assert bake_clip_weights.main(["--pin", str(TAXONOMY), str(out)]) == 0
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    assert json.loads(out.read_text(encoding="utf-8")) == {"model": taxonomy["model"],
                                                           "revision": taxonomy["revision"]}
    other = tmp_path / "taxonomy.json"
    other.write_text(json.dumps({**taxonomy, "revision": "f" * 40, "prompts": {"kitchen": "a kitchen"}}))
    assert bake_clip_weights.main(["--pin", str(other), str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"model": taxonomy["model"], "revision": "f" * 40}


def test_an_unpinned_taxonomy_bakes_nothing(tmp_path: Path) -> None:
    """Tagger.load refuses an unpinned checkpoint; the build refuses it first."""
    unpinned = tmp_path / "taxonomy.json"
    unpinned.write_text(json.dumps({"model": "openai/clip-vit-base-patch32", "revision": ""}))
    with pytest.raises(SystemExit, match="no revision"):
        bake_clip_weights.main(["--pin", str(unpinned), str(tmp_path / "out.json")])


def _fake_hub(monkeypatch: pytest.MonkeyPatch, answer: Callable[[int], str]) -> list[tuple[str, str, list[str]]]:
    calls: list[tuple[str, str, list[str]]] = []

    def snapshot_download(repo_id: str, *, revision: str, allow_patterns: list[str]) -> str:
        calls.append((repo_id, revision, allow_patterns))
        return answer(len(calls))

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return calls


def _pinned(tmp_path: Path) -> Path:
    out = tmp_path / "clip_checkpoint.json"
    bake_clip_weights.main(["--pin", str(TAXONOMY), str(out)])
    return out


def test_the_bake_downloads_the_pinned_revision_s_files_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    """The revision ships no safetensors, so the weights are pytorch_model.bin; the rest is config and tokenizer."""
    calls = _fake_hub(monkeypatch, lambda n: "/root/.cache/huggingface/hub/snapshot")
    assert bake_clip_weights.main([str(_pinned(tmp_path))]) == 0
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    assert calls == [(taxonomy["model"], taxonomy["revision"], ["*.json", "*.txt", "pytorch_model.bin"])]
    assert "/root/.cache/huggingface/hub/snapshot" in capsys.readouterr().out


def test_a_flaky_hub_is_retried_four_times_with_a_growing_wait(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def flaky(n: int) -> str:
        if n < 4:
            raise OSError("503 Service Unavailable")
        return "/snapshot"

    waits: list[float] = []
    monkeypatch.setattr(bake_clip_weights.time, "sleep", waits.append)
    calls = _fake_hub(monkeypatch, flaky)
    assert bake_clip_weights.main([str(_pinned(tmp_path))]) == 0
    assert len(calls) == 4 and waits == [5, 10, 15]

    def down(n: int) -> str:
        raise OSError("503 Service Unavailable")

    waits.clear()
    calls = _fake_hub(monkeypatch, down)
    assert bake_clip_weights.main([str(_pinned(tmp_path))]) == 1, "the build fails; the old deployment stays live"
    assert len(calls) == 4 and waits == [5, 10, 15]


def test_a_bad_call_prints_its_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert bake_clip_weights.main([]) == 2
    assert bake_clip_weights.main(["--pin", "only-one"]) == 2
    assert "usage:" in capsys.readouterr().err
