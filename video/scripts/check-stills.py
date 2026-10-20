"""Pixel checks on the rendered stills - the visual review done numerically.

Reports per frame: ink coverage, the bounding box of dark/text pixels, whether
that box sits inside the safe area, and whether the palette colours the story
needs actually made it into the frame.

    python3 scripts/check-stills.py "out/*.png"
"""

import glob
import sys

import numpy as np
from PIL import Image

BG = np.array([0xF5, 0xF5, 0xF6])
SKIP = np.array([0x9A, 0x3B, 0x2F])
ENTER = np.array([0x0F, 0x6E, 0x52])

# story: 214 top / 306 bottom. reels: 150 top / 430 bottom.
SAFE = {"story": (214, 306), "reels": (150, 430)}


def near(a, b, tol=26):
    return np.abs(a.astype(int) - b.astype(int)).max(axis=2) <= tol


def check(path):
    variant = "reels" if "reels" in path else "story"
    top, bottom = SAFE[variant]
    im = np.asarray(Image.open(path).convert("RGB"))
    h, w, _ = im.shape

    # ink = anything clearly darker than the paper background
    lum = im.astype(float).mean(axis=2)
    ink = lum < 170
    cover = float(ink.mean())
    # Continuous content strength: survives the low-opacity middle of a crossfade,
    # which a hard ink threshold reads as an empty frame.
    strength = float(np.abs(lum - BG.mean()).mean())
    # contrast across the whole frame, not per-pixel channel spread
    flat = float(lum.std())

    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    bbox = (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])) if len(rows) else None

    # the caption band must clear the platform chrome at the bottom
    lo = h - bottom
    cap_ink = float(ink[lo:lo + int((h - bottom) * 0.42)].mean())

    return {
        "file": path.rsplit("/", 1)[-1],
        "size": f"{w}x{h}",
        "cover": round(cover, 4),
        "strength": round(strength, 3),
        "flat": round(flat, 2),
        "bbox": bbox,
        "skip_px": int(near(im, SKIP).sum()),
        "enter_px": int(near(im, ENTER).sum()),
        "cap_ink": round(cap_ink, 4),
        "safe": None if bbox is None else bool(bbox[1] >= top - 14),
        "blank": bool(strength < 0.35),
    }


for arg in sys.argv[1:]:
    for path in sorted(glob.glob(arg)):
        r = check(path)
        flag = "BLANK" if r["blank"] else ("ok" if r["safe"] else "OFF-SAFE-TOP")
        print(
            f'{r["file"]:<18} {r["size"]} strength={r["strength"]:<7} cover={r["cover"]:<7} '
            f'skip={r["skip_px"]:<7} enter={r["enter_px"]:<6} cap={r["cap_ink"]:<7} '
            f'bbox={r["bbox"]} {flag}'
        )
