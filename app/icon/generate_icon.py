#!/usr/bin/env python3
"""Generate app/MacCleaner.icns from app/icon/icon.svg.tmpl.

Build-time asset tool only — NOT part of the cleaner.py runtime engine
(which stays stdlib-only). Requires `rsvg-convert` and `iconutil`
(both available via Homebrew / Xcode command line tools on macOS).

Usage:
    python3 app/icon/generate_icon.py

Regenerates:
    app/icon/icon.svg          (rendered from icon.svg.tmpl, kept for reference)
    app/icon/MacCleaner.iconset/*.png
    app/MacCleaner.icns
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent
APP_DIR = ICON_DIR.parent
TEMPLATE = ICON_DIR / "icon.svg.tmpl"
SVG_OUT = ICON_DIR / "icon.svg"
ICONSET_DIR = ICON_DIR / "MacCleaner.iconset"
ICNS_OUT = APP_DIR / "MacCleaner.icns"

# Broom glyph group transform: rotate an upright broom, scale it down, and
# place its visual center inside the 800x800 background squircle (which
# itself sits inset within the 1024x1024 canvas at x=y=112). Tuned by
# rendering + measuring the glyph's pixel bounding box — see
# `python3 app/icon/generate_icon.py --measure` in dev notes below.
BROOM_TRANSFORM = "translate(434, 455) rotate(-32) scale(0.92)"

# Standard macOS iconset sizes: (filename, pixel size)
ICONSET_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(f"error: required tool '{name}' not found on PATH")
    return path


def main() -> None:
    rsvg_convert = require_tool("rsvg-convert")
    require_tool("iconutil")

    template = TEMPLATE.read_text()
    svg = template.replace("{{BROOM_TRANSFORM}}", BROOM_TRANSFORM)
    SVG_OUT.write_text(svg)
    print(f"→ Wrote {SVG_OUT.relative_to(APP_DIR.parent)}")

    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)
    ICONSET_DIR.mkdir(parents=True)

    for filename, size in ICONSET_SIZES:
        out_path = ICONSET_DIR / filename
        subprocess.run(
            [
                rsvg_convert,
                "-w", str(size),
                "-h", str(size),
                str(SVG_OUT),
                "-o", str(out_path),
            ],
            check=True,
        )
    print(f"→ Rendered {len(ICONSET_SIZES)} PNGs into {ICONSET_DIR.relative_to(APP_DIR.parent)}")

    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_OUT)],
        check=True,
    )
    print(f"→ Built {ICNS_OUT.relative_to(APP_DIR.parent)}")


if __name__ == "__main__":
    main()
