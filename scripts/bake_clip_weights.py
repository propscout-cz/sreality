"""The job-runner image's CLIP checkpoint, baked at build time by the root Dockerfile (#2025).

`--pin <taxonomy> <out>` writes only data/clip_taxonomy.json's model and revision, so the layer that downloads the
weights is keyed on the pin and a prompt edit reuses it. `<checkpoint.json>` downloads the files
`scraper.clip_tagger.Tagger.load` reads into the hub cache it reads them from; the runner sets HF_HUB_OFFLINE=1.
Stdlib only at import: the clip-ref build stage runs `--pin` on a bare python.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

# The pinned revision ships no safetensors (docs/design/new-dedup/ENCODER-DECISION.md), so the weights are the .bin.
PATTERNS = ("*.json", "*.txt", "pytorch_model.bin")
ATTEMPTS = 4


def pin(taxonomy: Path, out: Path) -> None:
    tax = json.loads(taxonomy.read_text(encoding="utf-8"))
    if not tax.get("revision"):
        raise SystemExit(f"{taxonomy} has no revision: refusing to bake an unpinned checkpoint")
    out.write_text(json.dumps({"model": tax["model"], "revision": tax["revision"]}) + "\n", encoding="utf-8")


def bake(checkpoint: Path) -> int:
    """0 once the snapshot is in the cache; 1 after the last attempt, which fails the build."""
    from huggingface_hub import snapshot_download  # the image's model layer installs it before this runs

    ref = json.loads(checkpoint.read_text(encoding="utf-8"))
    for attempt in range(1, ATTEMPTS + 1):
        try:
            path = snapshot_download(ref["model"], revision=ref["revision"], allow_patterns=list(PATTERNS))
        except Exception as exc:  # noqa: BLE001 - HuggingFace 503s and timeouts, retried as Tagger.load does
            print(f"CLIP bake attempt {attempt}/{ATTEMPTS} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            if attempt < ATTEMPTS:
                time.sleep(5 * attempt)
            continue
        print(f"CLIP checkpoint {ref['model']} {ref['revision']} in {path}")
        return 0
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "--pin":
        pin(Path(args[1]), Path(args[2]))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        return bake(Path(args[0]))
    print("usage: bake_clip_weights.py --pin <taxonomy.json> <out.json> | <checkpoint.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
