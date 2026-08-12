"""Derive the app's logo assets from the Struzon print artwork.

Run this after replacing anything in ``web/logo/``::

    python tools/make_brand_assets.py

Two things about the source files make this a script rather than a one-off.

The ``webbience`` lockups are **CMYK** JPEGs - print files. Browsers handle
CMYK JPEG inconsistently and some render it with the colours inverted, so
everything is converted to RGB before it is written out.

The monogram is a 3100px square wrapped in a black frame on a white ground, and
neither belongs in a favicon. The frame is trimmed, then the white margin, then
the mark is re-squared with a little air so the "STANDS FOR TRUST" ring is not
flush against the edge.

Sizes are written at their display size where the consumer cannot rescale:
tkinter's PhotoImage only scales by whole-number factors, so the desktop
masthead is generated at exactly the height it is drawn at.

Requires Pillow, which is a dev dependency only - the shipped app reads the
generated PNGs and never needs to process an image.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "logo"
WEB = ROOT / "web" / "static" / "img"
PKG = ROOT / "fabdoc" / "assets"

LOCKUP = "Struzon-logo-webbience-04.jpg"     # full company lockup, one line
MONOGRAM = "st logo bg with-R.png"           # ST mark in its trust ring

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def load_rgb(name: str) -> Image.Image:
    """Open a source file as RGB, converting CMYK print artwork on the way."""
    image = Image.open(SRC / name)
    if image.mode == "CMYK":
        image = image.convert("RGB")
    return image.convert("RGB")


def trim(image: Image.Image, background: tuple[int, int, int],
         tolerance: int = 12) -> Image.Image:
    """Crop away a uniform border of the given colour."""
    reference = Image.new("RGB", image.size, background)
    mask = (ImageChops.difference(image, reference).convert("L")
            .point(lambda p: 255 if p > tolerance else 0))
    box = mask.getbbox()
    return image.crop(box) if box else image


def scale_to_height(image: Image.Image, height: int) -> Image.Image:
    width = round(image.size[0] * height / image.size[1])
    return image.resize((width, height), Image.LANCZOS)


def main() -> int:
    missing = [n for n in (LOCKUP, MONOGRAM) if not (SRC / n).is_file()]
    if missing:
        print(f"Missing source artwork in {SRC}:")
        for name in missing:
            print(f"  {name}")
        return 1

    for folder in (WEB, PKG):
        folder.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    # --- the horizontal lockup, for both mastheads -----------------------
    lockup = trim(load_rgb(LOCKUP), WHITE)
    # 120px for a masthead drawn at 46px, so it stays sharp on a HiDPI screen.
    scale_to_height(lockup, 120).save(WEB / "struzon-logo.png", optimize=True)
    # 34px exactly: tkinter cannot scale by a fraction.
    scale_to_height(lockup, 34).save(PKG / "struzon-logo.png", optimize=True)
    written += [WEB / "struzon-logo.png", PKG / "struzon-logo.png"]

    # --- the monogram, for tab icons and the desktop window --------------
    mark = trim(trim(Image.open(SRC / MONOGRAM).convert("RGB"), BLACK), WHITE)
    side = round(max(mark.size) * 1.10)
    square = Image.new("RGB", (side, side), WHITE)
    square.paste(mark, ((side - mark.size[0]) // 2, (side - mark.size[1]) // 2))

    for folder, name, size in (
        (WEB, "struzon-mark.png", 512),
        (WEB, "apple-touch-icon.png", 180),
        (WEB, "favicon.png", 128),
        (PKG, "struzon-mark.png", 256),      # tkinter iconphoto
    ):
        square.resize((size, size), Image.LANCZOS).save(folder / name, optimize=True)
        written.append(folder / name)

    square.save(WEB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    written.append(WEB / "favicon.ico")

    for path in written:
        image = Image.open(path)
        print(f"  {path.relative_to(ROOT)}"
              f"{'':<{max(0, 40 - len(str(path.relative_to(ROOT))))}}"
              f"{image.size[0]}x{image.size[1]}  {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
