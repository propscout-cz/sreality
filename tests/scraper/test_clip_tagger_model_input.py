"""Tagger.model_input (#2478): a photo becomes its model input with the image processor's own resize done first, on the
photo itself, so the processor never makes its full-size copies. Fakes only: the real processor's bit-identity with
its batch path is the runner image's offline proof (Dockerfile), at the pinned versions."""

from __future__ import annotations

from typing import Any

import pytest
from PIL import Image

from scraper.clip_tagger import Tagger


class _ImageProcessor:
    size = {"shortest_edge": 224}
    resample = Image.Resampling.BICUBIC


class _Processor:
    image_processor = _ImageProcessor()

    def __init__(self) -> None:
        self.calls: list[tuple[list[Any], str]] = []

    def __call__(self, *, images: list[Any], return_tensors: str) -> dict[str, Any]:
        self.calls.append((images, return_tensors))
        return {"pixel_values": ("input", len(self.calls))}


def _tagger(processor: _Processor) -> Tagger:
    return Tagger(object(), processor, [], None, {}, "openai/clip-vit-base-patch32")


@pytest.mark.parametrize(("photo", "resized"), [
    ((4032, 3024), (298, 224)),  # int(224 * 4032 / 3024): the processor's rounding, down
    ((3024, 4032), (224, 298)),
    ((225, 223), (226, 224)),
    ((100, 60), (373, 224)),     # a photo under the edge is enlarged, as the processor does
    ((300, 300), (224, 224)),
])
def test_the_processor_gets_the_photo_at_its_own_output_size(photo: tuple[int, int],
                                                              resized: tuple[int, int]) -> None:
    img = Image.effect_noise(photo, 64).convert("RGB")
    processor = _Processor()
    assert _tagger(processor).model_input(img) == ("input", 1)
    ((images, tensors),) = processor.calls
    assert tensors == "pt" and len(images) == 1
    assert images[0].size == resized
    assert images[0].tobytes() == img.resize(resized, resample=Image.Resampling.BICUBIC).tobytes()
