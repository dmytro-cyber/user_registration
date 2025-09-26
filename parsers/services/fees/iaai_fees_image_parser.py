from typing import Dict, List, Tuple
from itertools import zip_longest
import re
import statistics
from io import BytesIO
import os

from PIL import Image
import pytesseract
from cairosvg import svg2png
import logging

logger = logging.getLogger(__name__)


# ------------------------ OCR / image helpers ------------------------

def _binarize(img: Image.Image, thr: int = 180) -> Image.Image:
    """
    Convert to grayscale and apply a fixed threshold. Returns 0/255 'L' image.
    """
    g = img.convert("L")
    bw = g.point(lambda x: 0 if x < thr else 255, mode="1")
    return bw.convert("L")


def _vertical_content_segments(bw_img: Image.Image) -> List[Tuple[int, int]]:
    """
    Find horizontal 'content' segments (potential columns) using vertical projection.
    A segment is a contiguous x-range where dark pixel count exceeds a data-driven threshold.
    """
    w, h = bw_img.size
    pix = bw_img.load()
    proj = []
    for x in range(w):
        dark = 0
        # sample every 2nd pixel in height for speed (robust enough for wide tables)
        for y in range(0, h, 2):
            if pix[x, y] < 128:
                dark += 1
        proj.append(dark)

    # Smooth projection to stabilize thresholds
    win = max(7, int(w * 0.005))
    smoothed = []
    for i in range(w):
        left = max(0, i - win)
        right = min(w, i + win + 1)
        smoothed.append(statistics.mean(proj[left:right]))

    # Dynamic threshold: values clearly above "background"
    # Use median + k * MAD-like factor for robustness
    med = statistics.median(smoothed)
    # fallback if all zeros
    spread = statistics.median([abs(v - med) for v in smoothed]) or 1.0
    thr = med + 0.75 * spread

    mask = [v > thr for v in smoothed]

    # Build segments
    segs: List[Tuple[int, int]] = []
    s = None
    for i, m in enumerate(mask):
        if m and s is None:
            s = i
        elif not m and s is not None:
            segs.append((s, i - 1))
            s = None
    if s is not None:
        segs.append((s, w - 1))

    # Merge very small gaps or very narrow segments
    merged: List[Tuple[int, int]] = []
    MIN_SEG_W = max(10, int(w * 0.03))
    i = 0
    while i < len(segs):
        a, b = segs[i]
        # skip tiny segments
        if (b - a + 1) < MIN_SEG_W:
            i += 1
            continue
        j = i + 1
        while j < len(segs):
            a2, b2 = segs[j]
            # if gap between segments is very small, merge
            if (a2 - b) <= int(w * 0.02):
                b = b2
                j += 1
            else:
                break
        merged.append((a, b))
        i = j

    if not merged:
        # fallback: treat whole width as a single content block
        merged = [(int(w * 0.05), int(w * 0.95))]

    return merged


def _ocr_lines(img: Image.Image) -> List[str]:
    """
    OCR a column crop into clean text lines.
    """
    config = "--oem 3 --psm 6"
    raw = pytesseract.image_to_string(img, config=config)

    def _clean_line(s: str) -> str:
        s = s.strip()
        s = s.replace("—", "-").replace("–", "-")
        s = s.replace("O", "0")  # common OCR error for zero
        s = re.sub(r"(?<=\d),(?=\d)", "", s)  # remove thousand separators in numbers
        s = re.sub(r"\s+", " ", s)
        return s

    lines = [_clean_line(l) for l in raw.split("\n")]
    return [l for l in lines if l]


# ------------------------ parsing helpers ------------------------

def _normalize_price_cell(cell: str, open_cap: float = 1_000_000.0) -> List[str]:
    """
    Normalize a price cell into canonical keys:
      - "$11500 - $11999.99" -> ["11500.00-11999.99"]
      - "$15000+" -> ["15000.00+", "15000.00-1000000.00"]
    Returns empty list if not parseable.
    """
    s = cell.strip()
    s = s.replace(",", "")
    # "$A - $B"
    m = re.match(r"^\$?(\d+(?:\.\d{1,2})?)\s*-\s*\$?(\d+(?:\.\d{1,2})?)$", s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return [f"{lo:.2f}-{hi:.2f}"]
    # "$A+"
    m = re.match(r"^\$?(\d+(?:\.\d{1,2})?)\s*\+$", s)
    if m:
        lo = float(m.group(1))
        return [f"{lo:.2f}+", f"{lo:.2f}-{open_cap:.2f}"]
    return []


def _parse_fee_cell(cell: str) -> float | str | None:
    """
    Parse fee cell to:
      - float for dollar fees ("$500.00" -> 500.0)
      - string "<pct>% of sale price" for percentages ("6%" or "6% of sale price")
      - 0.0 for FREE / NO FEE
      - None if not recognizable
    """
    s = cell.strip()
    s = s.replace(",", "")
    # FREE
    if re.search(r"^(FREE|NO\s*FEE)$", s, re.I):
        return 0.0
    # percent
    m = re.search(r"(\d+(?:\.\d+)?)\s*%(\s*of\s*sale\s*price)?", s, re.I)
    if m:
        pct = float(m.group(1))
        return f"{pct}% of sale price"
    # dollars
    m = re.match(r"^\$?(\d+(?:\.\d{1,2})?)$", s)
    if m:
        return float(m.group(1))
    return None


def _pair_rows(price_lines: List[str], fee_lines: List[str]) -> Dict[str, float | str | None]:
    """
    Pair price and fee lines by index; expand open-ended ranges into two keys.
    """
    OPEN_CAP = 1_000_000.0
    out: Dict[str, float | str | None] = {}
    for p_txt, f_txt in zip_longest(price_lines, fee_lines, fillvalue=""):
        price_keys = _normalize_price_cell(p_txt or "", open_cap=OPEN_CAP)
        if not price_keys:
            continue
        fee_val = _parse_fee_cell(f_txt or "")
        for k in price_keys:
            out[k] = fee_val
    return out


# ------------------------ main public function ------------------------

def parse_svg_table(svg_file_path: str) -> Dict[str, float | str | None]:
    """
    Parse a fee table from SVG or PNG by:
      1) Rendering SVG -> PNG if needed.
      2) Binarizing and detecting vertical content segments (columns).
      3) Taking the left-most segment as the PRICE column, and the right-most segment as the FEE column.
      4) OCRing both crops and pairing rows.

    No extra parameters; works for:
      - 2 columns (takes 1 & 2)
      - 3+ columns (takes 1 & last)
    """
    if not os.path.exists(svg_file_path):
        logger.error(f"File not found: {svg_file_path}")
        return {}

    # Load/render image
    try:
        _, ext = os.path.splitext(svg_file_path.lower())
        if ext == ".svg":
            with open(svg_file_path, "rb") as f:
                png_bytes = svg2png(bytestring=f.read(), output_width=2000)
            img = Image.open(BytesIO(png_bytes)).convert("RGB")
        else:
            img = Image.open(svg_file_path).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to open/render image: {e}")
        return {}

    # Binarize & detect segments
    bw = _binarize(img)
    segs = _vertical_content_segments(bw)
    w, h = bw.size

    # Choose left-most and right-most segments.
    if len(segs) == 1:
        # If only one big block found, split in half.
        a, b = segs[0]
        mid = (a + b) // 2
        left_seg = (a, mid)
        right_seg = (mid + 1, b)
    else:
        left_seg = segs[0]
        right_seg = segs[-1]

    # Crop with a small horizontal inset to avoid borders
    def _crop(seg: Tuple[int, int]) -> Image.Image:
        x0, x1 = seg
        inset = max(2, int((x1 - x0) * 0.02))
        return bw.crop((x0 + inset, 0, x1 - inset, h))

    price_img = _crop(left_seg)
    fee_img = _crop(right_seg)

    try:
        price_lines = _ocr_lines(price_img)
        fee_lines = _ocr_lines(fee_img)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return {}

    fees = _pair_rows(price_lines, fee_lines)
    logger.info(
        f"Parsed {len(fees)} rows from {os.path.basename(svg_file_path)} "
        f"(segments={len(segs)}; left={left_seg}, right={right_seg})"
    )
    return fees
