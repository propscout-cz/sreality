"""Draw the home-screen, tab and extension icons (run: python3 frontend/scripts/make-icons.py).

The mark is the header's wordmark (BrandMark in src/components/Shell.tsx) reduced to its
two initials: "P" in ink and "S" in the copper accent, set in the self-hosted Fraunces at
weight 600 on the paper canvas. The extension wears the edge tab's mark (chrome-extension/
src/edge_tab.ts), mint on the chrome colour, and its store tile. Colours are the light tokens
(the Měď scheme) in src/styles/tokens.css. Needs Pillow (FreeType reads the woff2 directly).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FRONTEND = Path(__file__).resolve().parent.parent
FONT = FRONTEND / "src/assets/fonts/fraunces-latin.woff2"
PUBLIC = FRONTEND / "public"
EXTENSION = FRONTEND.parent / "chrome-extension"

PAPER = (0xF2, 0xEF, 0xE7)
INK = (0x16, 0x18, 0x1D)
COPPER = (0x1F, 0x6B, 0x5C)
CHROME = (0x1D, 0x21, 0x25)
MINT = (0x7C, 0xC4, 0xB2)
FIRST, SECOND = "P", "S"

WEIGHT = 600
SUPERSAMPLE = 4
# Cap height as a share of the canvas. A maskable icon keeps its mark inside the central
# 80 % circle the platform may crop to, so it draws smaller.
CAP_ANY = 0.40
CAP_MASKABLE = 0.30
# The toolbar draws 16 px: the letters take what room there is.
CAP_EXTENSION = 0.56


def _font(px: float, opsz: float) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), max(1, round(px)))
    # Axis order in the file: Optical Size (9..144), Weight (100..900).
    font.set_variation_by_axes([min(144.0, max(9.0, opsz)), WEIGHT])
    return font


def _cap_ratio() -> float:
    probe = _font(1000, 72)
    top = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), FIRST, font=probe, anchor="ls")[1]
    return -top / 1000


def draw(
    size: int,
    cap: float,
    paper: tuple[int, int, int] = PAPER,
    first: tuple[int, int, int] = INK,
    second: tuple[int, int, int] = COPPER,
) -> Image.Image:
    canvas = size * SUPERSAMPLE
    image = Image.new("RGB", (canvas, canvas), paper)
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
    pen.text((x, y), FIRST, font=font, fill=first, anchor="ls")
    pen.text((x + advance, y), SECOND, font=font, fill=second, anchor="ls")
    return image.resize((size, size), Image.LANCZOS)


def extension_icon(size: int, padding: int = 0) -> Image.Image:
    """The rounded mark, `padding` px of transparency round it (the store wants 16 on its 128)."""
    art = size - 2 * padding
    mask = Image.new("L", (art * SUPERSAMPLE, art * SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1), radius=round(0.18 * mask.width), fill=255
    )
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(draw(art, CAP_EXTENSION, CHROME, MINT, MINT), (padding, padding), mask.resize((art, art), Image.LANCZOS))
    return icon


def store_tile() -> Image.Image:
    """The store's small promo tile: the icon beside the wordmark."""
    width, height = 440, 280
    tile = Image.new("RGB", (width * SUPERSAMPLE, height * SUPERSAMPLE), PAPER)
    icon = extension_icon(88 * SUPERSAMPLE)
    tile.paste(icon, (44 * SUPERSAMPLE, (height - 88) // 2 * SUPERSAMPLE), icon)
    pen = ImageDraw.Draw(tile)
    font = _font(40 * SUPERSAMPLE, opsz=40)
    x, y = 150 * SUPERSAMPLE, 154 * SUPERSAMPLE
    pen.text((x, y), "PropScout", font=font, fill=INK, anchor="ls")
    pen.text((x + font.getlength("PropScout"), y), ".cz", font=font, fill=COPPER, anchor="ls")
    return tile.resize((width, height), Image.LANCZOS)


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
    extension = {
        "icon-16.png": extension_icon(16),
        "icon-48.png": extension_icon(48),
        "icon-128.png": extension_icon(128, padding=16),
        "store-listing/promo-tile-440x280.png": store_tile(),
    }
    for name, image in extension.items():
        image.save(EXTENSION / name, optimize=True)
        print(f"wrote chrome-extension/{name}")


if __name__ == "__main__":
    main()
