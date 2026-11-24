"""Generate the application icons in packaging/icons/ (needs Pillow).

Draws the same mark as the favicon in web/templates/index.html (a 32x32 SVG:
rounded green square, two white rails, three light-green rungs) at high
resolution and writes PNG, ICO (Windows) and ICNS (macOS) files. The outputs are
committed, so building the binaries does not need Pillow.

    python tools/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "packaging" / "icons"
SIZE = 1024
UNIT = SIZE / 32  # favicon viewBox is 32x32


def _line(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float, float, float],
    colour: str,
    width: float,
    round_caps: bool,
) -> None:
    x0, y0, x1, y1 = (v * UNIT for v in xy)
    w = width * UNIT
    draw.line((x0, y0, x1, y1), fill=colour, width=round(w))
    if round_caps:
        r = w / 2
        for x, y in ((x0, y0), (x1, y1)):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def draw_mark() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=7 * UNIT, fill="#176b5b")
    for y in (11, 21):
        _line(draw, (7, y, 25, y), "#ffffff", 3, round_caps=True)
    for x in (11, 16, 21):
        _line(draw, (x, 11, x, 21), "#a7e3d4", 2, round_caps=False)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark = draw_mark()
    mark.resize((512, 512), Image.Resampling.LANCZOS).save(OUT / "poet-primers.png")
    mark.save(OUT / "poet-primers.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    mark.save(OUT / "poet-primers.icns")
    for path in sorted(OUT.iterdir()):
        print(path.relative_to(OUT.parent.parent), path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
