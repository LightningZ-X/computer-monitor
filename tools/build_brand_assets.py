"""Rasterize the approved VELTRIX vector paths once, outside the monitor runtime.

No SVG engine, web view, network service or new runtime dependency is needed.
The checked-in PNGs are loaded once by tkinter; Pillow already serves the tray.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def rasterize(part: str) -> Path:
    svg = ET.parse(ASSETS / f"veltrix-{part}.svg").getroot()
    _, _, width, height = map(float, svg.attrib["viewBox"].split())
    factor = 4
    mask = Image.new("L", (round(width * factor), round(height * factor)))
    draw = ImageDraw.Draw(mask)
    contours = []
    for path in svg.findall("{http://www.w3.org/2000/svg}path"):
        for section in path.attrib["d"].split("M")[1:]:
            points = [tuple(map(float, p.split(",")))
                      for p in section.rstrip("Z").split()]
            area = sum(a[0] * b[1] - b[0] * a[1]
                       for a, b in zip(points, points[1:] + points[:1]))
            contours.append((area, points))
    for area, points in sorted(contours, key=lambda item: -abs(item[0])):
        draw.polygon([(x * factor, y * factor) for x, y in points],
                     fill=255 if area > 0 else 0)
    mask = mask.resize((round(width), round(height)), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    image.putalpha(mask)
    target = ASSETS / f"veltrix-{part}-source.png"
    image.save(target, optimize=True)
    return target


def main() -> int:
    sys.path.insert(0, str(ROOT))
    for part in ("mark", "wordmark"):
        print(rasterize(part))
    from tools.install_menu_shortcut import (
        make_icon, prepare_header_mark, prepare_header_wordmark,
    )
    print(make_icon())
    print(prepare_header_mark())
    print(prepare_header_wordmark())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
