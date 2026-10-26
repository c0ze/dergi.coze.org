"""Visual cleanup of one scanned page for the personal archive: crop the scanner bed,
straighten a tilted scan, recompress.

Conservative by design, because the archive replaces the originals (crop is off, see CROP):
- Crop: only bands at the edges that are uniform neutral dark grey (scanner bed, lid shadow;
  a dark *coloured* page background is not bed), at most MAX_CROP of the side, and the crop
  stops SAFETY short of the first non-bed line. White is never cropped: on bright scans the
  paper margin is white too, and cropping it cut into the text (Commodore Haberler 5).
- Deskew: the text angle is measured from the horizontal projection of dark pixels; the
  page is rotated only for a clear tilt between MIN_ANGLE and MAX_ANGLE degrees.
- Recompress only pages that changed, with the original JPEG's own quantisation tables, so
  quality and size stay as scanned; unchanged pages keep their original bytes.

Usage (preview): cleanup.py <image or pdf> [<page> ...]  → before/after sheet in data/cleanup-preview/
"""

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MAX_CROP = 0.12
MIN_ANGLE, MAX_ANGLE, STEP = 0.4, 3.0, 0.1
QUALITY = 88  # for sources that aren't JPEG
DARK, FLAT, GREY = 100, 14, 30  # bed band: mean < DARK, std < FLAT, saturation < GREY
SAFETY = 0.005                   # of the side, left uncropped next to the content
# Cropping is off for the archive: the bed detector can mistake a dark printed band with
# lettering near a corner for scanner bed (review, 2026-10-26), and in practice it almost
# never fired. Deskew alone stays on.
CROP = False
VERSION = "2026-10-26b"           # recorded in archive outputs; bump when behaviour changes


def crop_box(gray: np.ndarray, sat: np.ndarray) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) after trimming uniform scanner-bed bands at the edges."""
    h, w = gray.shape

    def trim(lines: np.ndarray, sats: np.ndarray, limit: int) -> int:
        n = 0
        for line, line_sat in zip(lines[:limit], sats[:limit]):
            mean, std = line.mean(), line.std()
            if std < FLAT and mean < DARK and line_sat.mean() < GREY:
                n += 1
            else:
                break
        margin = int(len(lines[0]) and SAFETY * limit / MAX_CROP)
        return n - margin if n > 2 * margin else 0

    # Look at the middle 80% of each edge so a corner of something else doesn't stop the trim.
    rows, cols = gray[:, w // 10: -w // 10], gray[h // 10: -h // 10, :]
    srows, scols = sat[:, w // 10: -w // 10], sat[h // 10: -h // 10, :]
    top = trim(rows, srows, int(h * MAX_CROP))
    bottom = trim(rows[::-1], srows[::-1], int(h * MAX_CROP))
    left = trim(cols.T, scols.T, int(w * MAX_CROP))
    right = trim(cols.T[::-1], scols.T[::-1], int(w * MAX_CROP))
    return left, top, w - right, h - bottom


def skew_angle(gray: np.ndarray) -> float:
    """Tilt in degrees (positive = rotate counter-clockwise to fix), 0 if no clear tilt."""
    small = cv2.resize(gray, None, fx=1000 / max(gray.shape), fy=1000 / max(gray.shape), interpolation=cv2.INTER_AREA)
    ink = (cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15) > 0)
    ink = ink.astype(np.float32)
    h, w = ink.shape
    center = (w / 2, h / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_NEAREST)
        profile = rotated.sum(axis=1)
        return float(np.var(profile))

    angles = np.arange(-MAX_ANGLE, MAX_ANGLE + STEP / 2, STEP)
    scores = [score(a) for a in angles]
    best = int(np.argmax(scores))
    flat = score(0.0)
    angle = float(angles[best])
    # A real tilt makes text lines line up much better than leaving the page as is.
    if abs(angle) < MIN_ANGLE or scores[best] < 1.15 * flat:
        return 0.0
    return angle


def clean(data: bytes, crop: bool = CROP) -> tuple[bytes, dict]:
    """Cleaned JPEG bytes and what was done (the original bytes if nothing needed doing)."""
    src = Image.open(io.BytesIO(data))
    img = src.convert("RGB") if src.mode not in ("RGB", "L") else src
    arr = np.asarray(img)
    gray = arr if arr.ndim == 2 else cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    angle = skew_angle(gray)
    if angle:
        h, w = gray.shape
        # Rotate onto a canvas large enough for the whole rotated page, so nothing near an
        # edge or corner is cut off; the new corners take the page's own edge colour.
        cos, sin = abs(np.cos(np.radians(angle))), abs(np.sin(np.radians(angle)))
        nw, nh = int(np.ceil(w * cos + h * sin)), int(np.ceil(h * cos + w * sin))
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        m[0, 2] += nw / 2 - w / 2
        m[1, 2] += nh / 2 - h / 2
        border = tuple(int(v) for v in np.median(arr[:, : max(2, w // 200)].reshape(-1, arr.shape[-1] if arr.ndim == 3 else 1), axis=0))
        arr = cv2.warpAffine(arr, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                             borderValue=border if arr.ndim == 3 else border[0])
        gray = arr if arr.ndim == 2 else cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    h0, w0 = gray.shape
    l, t, r, b = 0, 0, w0, h0
    if crop:
        sat = (cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[..., 1] if arr.ndim == 3 else np.zeros_like(gray))
        l, t, r, b = crop_box(gray, sat)
    info = {"angle": round(angle, 2), "crop": [l, t, w0 - r, h0 - b]}
    if not angle and (l, t, r, b) == (0, 0, w0, h0) and src.format == "JPEG":
        return data, info
    arr = arr[t:b, l:r]
    out = io.BytesIO()
    if src.format == "JPEG":
        # Same quantisation tables and chroma subsampling as the scan: no quality loss
        # beyond one generation, and no size growth.
        from PIL import JpegImagePlugin
        Image.fromarray(arr).save(out, "JPEG", qtables=src.quantization, optimize=True,
                                  subsampling=JpegImagePlugin.get_sampling(src))
    else:
        Image.fromarray(arr).save(out, "JPEG", quality=QUALITY, optimize=True)
    return out.getvalue(), info


def preview(src: Path, pages: list[int]) -> Path:
    import pymupdf
    out_dir = ROOT / "data" / "cleanup-preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src)
    tiles = []
    for p in pages:
        xref = max(doc[p - 1].get_images(), key=lambda im: im[2] * im[3])[0]
        before = doc.extract_image(xref)["image"]
        after, info = clean(before)
        pair = [Image.open(io.BytesIO(x)).convert("RGB") for x in (before, after)]
        for im in pair:
            im.thumbnail((520, 700))
        tile = Image.new("RGB", (1060, 740), "#7a7a7a")
        tile.paste(pair[0], (10, 30))
        tile.paste(pair[1], (540, 30))
        from PIL import ImageDraw
        ImageDraw.Draw(tile).text((10, 6), f"{src.stem} p{p}  before | after  angle {info['angle']}°  crop L/T/R/B {info['crop']}  "
                                  f"{len(before) // 1024} KB -> {len(after) // 1024} KB", fill="white")
        tiles.append(tile)
    sheet = Image.new("RGB", (1060, 740 * len(tiles)), "#7a7a7a")
    for i, t in enumerate(tiles):
        sheet.paste(t, (0, 740 * i))
    path = out_dir / f"{src.stem}.png"
    sheet.save(path)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    print(preview(Path(sys.argv[1]), [int(p) for p in sys.argv[2:]]))
