#!/usr/bin/env python3
"""Scale how large an icon renders, by padding its canvas — never its pixels.

The icons are trimmed flush to their artwork, and the UI draws them with
`object-fit: contain` inside a square chip. That means every icon fills the
chip along its longest side, so an icon with an unusual aspect ratio renders
at a different optical size than the rest of the set.

Giving such an icon a transparent margin makes it render smaller, without
resampling a single pixel: the artwork is copied through untouched and only
the canvas around it grows.

    python scripts/pad_icon_canvas.py --check                    # report
    python scripts/pad_icon_canvas.py audit.png --scale 0.92     # apply

`--scale 0.92` means "render at 92% of the size you would otherwise", i.e.
the canvas grows to artwork / 0.92.

Requires Pillow. --check never writes.
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

from PIL import Image

ICON_DIR = pathlib.Path(__file__).resolve().parent.parent / "src/assets/icons/presentation"


def geometry(path: pathlib.Path) -> tuple[int, int, float, float]:
    """Artwork size, plus the fraction of a square chip it covers each way.

    Measured from the alpha bounding box, not the canvas, so an icon that has
    been given a transparent margin reports the size it actually renders at.
    """
    with Image.open(path) as raw:
        image = raw.convert("RGBA")
        box = image.getchannel("A").getbbox() or (0, 0, image.width, image.height)
        art_w, art_h = box[2] - box[0], box[3] - box[1]
        longest = max(image.width, image.height)
    return art_w, art_h, art_h / longest, art_w / longest


def report() -> None:
    rows = [(p.name, *geometry(p)) for p in sorted(ICON_DIR.glob("*.png"))]
    median_h = statistics.median(r[3] for r in rows)
    median_w = statistics.median(r[4] for r in rows)
    print(f"{'icon':28} {'size':>9} {'height':>7} {'width':>7}")
    for name, width, height, hfrac, wfrac in sorted(rows, key=lambda r: r[3]):
        odd = "  <- taller than wide" if height > width else ""
        print(f"{name:28} {f'{width}x{height}':>9} {hfrac:>7.2f} {wfrac:>7.2f}{odd}")
    print(f"\nmedian rendered height {median_h:.2f}, width {median_w:.2f}")


def pad(path: pathlib.Path, scale: float, dry_run: bool) -> bool:
    if not 0.1 < scale <= 1.0:
        print(f"scale must be in (0.1, 1.0], got {scale}", file=sys.stderr)
        return False

    with Image.open(path) as raw:
        image = raw.convert("RGBA")

    box = image.getchannel("A").getbbox()
    if box is None:
        print(f"{path.name}: fully transparent, skipped")
        return False

    art = image.crop(box)
    canvas_size = (round(art.width / scale), round(art.height / scale))
    print(
        f"{path.name}: artwork {art.width}x{art.height} kept as-is, "
        f"canvas {image.width}x{image.height} -> {canvas_size[0]}x{canvas_size[1]} "
        f"(renders at {scale:.0%})"
    )
    if dry_run:
        return False

    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    canvas.paste(
        art,
        ((canvas_size[0] - art.width) // 2, (canvas_size[1] - art.height) // 2),
    )
    canvas.save(path, "PNG", optimize=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("icons", nargs="*", help="icon filenames under the presentation dir")
    parser.add_argument("--scale", type=float, help="render size as a fraction, e.g. 0.92")
    parser.add_argument("--check", action="store_true", help="report geometry, write nothing")
    args = parser.parse_args()

    if args.check and not args.icons:
        report()
        return 0
    if not args.icons or args.scale is None:
        parser.error("name an icon and pass --scale, or use --check on its own")

    changed = 0
    for name in args.icons:
        path = ICON_DIR / name
        if not path.exists():
            print(f"{name}: not found in {ICON_DIR}", file=sys.stderr)
            return 1
        changed += pad(path, args.scale, args.check)

    if not args.check:
        print(f"\n{changed} icon(s) repadded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
