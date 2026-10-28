"""Draw the home-screen and tab icons into frontend/public/ (run: python3 frontend/scripts/make-icons.py).

The mark is the header's wordmark (BrandMark in src/components/Shell.tsx) reduced to its
two initials: "P" in ink and "S" in the copper accent, set in the self-hosted Fraunces at
weight 600 on the paper canvas. Colours are the light tokens (the Měď scheme) in
src/styles/tokens.css. Needs Pillow (FreeType reads the woff2 directly).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FRONTEND = Path(__file__).resolve().parent.parent
FONT = FRONTEND / "src/assets/fonts/fraunces-latin.woff2"
PUBLIC = FRONTEND / "public"

PAPER = (0xF2, 0xEF, 0xE7)
INK = (0x16, 0x18, 0x1D)
COPPER = (0x1F, 0x6B, 0x5C)
FIRST, SECOND = "P", "S"

WEIGHT = 600
SUPERSAMPLE = 4
# Cap height as a share of the canvas. A maskable icon keeps its mark inside the central
# 80 % circle the platform may crop to, so it draws smaller.
CAP_ANY = 0.40
CAP_MASKABLE = 0.30


def _font(px: float, opsz: float) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), max(1, round(px)))
    # Axis order in the file: Optical Size (9..144), Weight (100..900).
    font.set_variation_by_axes([min(144.0, max(9.0, opsz)), WEIGHT])
    return font


def _cap_ratio() -> float:
    probe = _font(1000, 72)
    top = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), FIRST, font=probe, anchor="ls")[1]
    return -top / 1000


def draw(size: int, cap: float) -> Image.Image:
    canvas = size * SUPERSAMPLE
    image = Image.new("RGB", (canvas, canvas), PAPER)
    pen = ImageDraw.Draw(image)
    px = cap * size / _cap_ratio()
    # Optical size follows the rendered size, as CSS font-optical-sizing: auto does, so the
    # small favicons get Fraunces' sturdier small-size hairlines.
    font = _font(px * SUPERSAMPLE, opsz=px)
    left, top, _, _ = pen.textbbox((0, 0), FIRST, font=font, anchor="ls")
    advance = font.getlength(FIRST)
    right = advance + pen.textbbox((0, 0), SECOND, font=font, anchor="ls")[2]
    x = (canvas - (right - left)) / 2 - left
    y = (canvas - top) / 2
    pen.text((x, y), FIRST, font=font, fill=INK, anchor="ls")
    pen.text((x + advance, y), SECOND, font=font, fill=COPPER, anchor="ls")
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    (PUBLIC / "icons").mkdir(parents=True, exist_ok=True)
    outputs = {
        "icons/icon-192.png": draw(192, CAP_ANY),
        "icons/icon-512.png": draw(512, CAP_ANY),
        "icons/icon-512-maskable.png": draw(512, CAP_MASKABLE),
        "apple-touch-icon.png": draw(180, CAP_ANY),
    }
    for name, image in outputs.items():
        image.save(PUBLIC / name, optimize=True)
    favicon_48 = draw(48, CAP_ANY)
    favicon_48.save(PUBLIC / "favicon.ico", sizes=[(32, 32), (48, 48)], append_images=[draw(32, CAP_ANY)])
    for name in [*outputs, "favicon.ico"]:
        print(f"wrote public/{name}")


if __name__ == "__main__":
    main()
