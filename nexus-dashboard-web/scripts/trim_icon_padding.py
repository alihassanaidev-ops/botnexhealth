#!/usr/bin/env python3
"""Trim the transparent padding baked into a presentation icon PNG.

The icons in `src/assets/icons/presentation` are 256x256 canvases with the
artwork inset by a margin that varies per export. Because the UI scales the
whole canvas (`object-fit: contain`), an icon exported with a bigger margin
renders visibly smaller than its neighbours.

This crops an icon down to its actual (opaque) artwork, so the file carries no
padding of its own and spacing is left to CSS. The crop is lossless — pixels
are not resampled, only the empty border is removed.

    python scripts/trim_icon_padding.py --check                 # report only
    python scripts/trim_icon_padding.py callback-queue.png      # trim one
    python scripts/trim_icon_padding.py --all                   # trim the set

Requires Pillow. --check never writes; run it first.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from PIL import Image

ICON_DIR = pathlib.Path(__file__).resolve().parent.parent / "src/assets/icons/presentation"


def content_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the non-transparent artwork."""
    return image.getchannel("A").getbbox()


def report(path: pathlib.Path) -> None:
    with Image.open(path) as raw:
        image = raw.convert("RGBA")
        box = content_box(image)
        if box is None:
            print(f"{path.name:34} {'EMPTY':>11}")
            return
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        pad = (left, top, image.width - right, image.height - bottom)
        fill = max(width / image.width, height / image.height)
        print(
            f"{path.name:34} {f'{image.width}x{image.height}':>9}"
            f" {f'{width}x{height}':>11} {fill:>7.3f}"
            f"  pad L{pad[0]} T{pad[1]} R{pad[2]} B{pad[3]}"
        )


def trim(path: pathlib.Path) -> bool:
    with Image.open(path) as raw:
        image = raw.convert("RGBA")

    box = content_box(image)
    if box is None:
        print(f"{path.name}: fully transparent, skipped")
        return False

    left, top, right, bottom = box
    if (left, top, right, bottom) == (0, 0, image.width, image.height):
        print(f"{path.name}: already flush, skipped")
        return False

    cropped = image.crop(box)
    cropped.save(path, "PNG", optimize=True)
    print(
        f"{path.name}: {image.width}x{image.height} -> {cropped.width}x{cropped.height}"
        f"  (removed L{left} T{top} R{image.width - right} B{image.height - bottom})"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("icons", nargs="*", help="icon filenames under the presentation dir")
    parser.add_argument("--all", action="store_true", help="apply to every icon in the dir")
    parser.add_argument("--check", action="store_true", help="report padding, write nothing")
    args = parser.parse_args()

    if args.all:
        targets = sorted(ICON_DIR.glob("*.png"))
    else:
        targets = [ICON_DIR / name for name in args.icons]

    if args.check:
        targets = targets or sorted(ICON_DIR.glob("*.png"))
        print(f"{'icon':34} {'canvas':>9} {'content':>11} {'fill':>7}  padding")
        for path in targets:
            report(path)
        return 0

    if not targets:
        parser.error("name at least one icon, or pass --all / --check")

    trimmed = 0
    for path in targets:
        if not path.exists():
            print(f"{path.name}: not found in {ICON_DIR}", file=sys.stderr)
            return 1
        trimmed += trim(path)

    print(f"\n{trimmed} icon(s) trimmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
