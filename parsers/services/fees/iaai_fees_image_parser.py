# parsers/services/fees/iaai_image_parser.py
from __future__ import annotations

import logging
import os
import re
import statistics
from io import BytesIO
from itertools import zip_longest
from typing import Dict, List, Optional, Tuple

import pytesseract
from cairosvg import svg2png
from PIL import Image

logger = logging.getLogger(__name__)

OPEN_CAP = 1_000_000.0


# ------------------------ OCR / image helpers ------------------------

def _binarize(img: Image.Image, thr: int = 180) -> Image.Image:
    """Convert to grayscale and apply a fixed threshold."""
    g = img.convert("L")
    bw = g.point(lambda x: 0 if x < thr else 255, mode="1")
    return bw.convert("L")


def _vertical_content_segments(bw_img: Image.Image) -> List[Tuple[int, int]]:
    """
    Find vertical content segments (columns) using a smoothed vertical projection.
    Returns list of (x_start, x_end).
    """
    w, h = bw_img.size
    pix = bw_img.load()
    proj = []
    for x in range(w):
        dark = 0
        for y in range(0, h, 2):
            if pix[x, y] < 128:
                dark += 1
        proj.append(dark)

    win = max(7, int(w * 0.005))
    smoothed = []
    for i in range(w):
        left = max(0, i - win)
        right = min(w, i + win + 1)
        smoothed.append(statistics.mean(proj[left:right]))

    med = statistics.median(smoothed)
    spread = statistics.median([abs(v - med) for v in smoothed]) or 1.0
    thr = med + 0.75 * spread

    mask = [v > thr for v in smoothed]

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

    merged: List[Tuple[int, int]] = []
    MIN_SEG_W = max(10, int(w * 0.03))
    i = 0
    while i < len(segs):
        a, b = segs[i]
        if (b - a + 1) < MIN_SEG_W:
            i += 1
            continue
        j = i + 1
        while j < len(segs):
            a2, b2 = segs[j]
            if (a2 - b) <= int(w * 0.02):
                b = b2
                j += 1
            else:
                break
        merged.append((a, b))
        i = j

    if not merged:
        merged = [(int(w * 0.05), int(w * 0.95))]

    return merged


def _ocr_text(img: Image.Image, psm: int = 6) -> str:
    """Run Tesseract OCR and return raw text."""
    return pytesseract.image_to_string(img, config=f"--oem 3 --psm {psm}")


def _ocr_lines(img: Image.Image, psm: int = 6) -> List[str]:
    """OCR a crop into clean lines."""
    raw = _ocr_text(img, psm=psm)

    def _clean_line(s: str) -> str:
        s = s.strip()
        s = s.replace("—", "-").replace("–", "-")
        s = s.replace("O", "0")
        s = re.sub(r"(?<=\d),(?=\d)", "", s)  # remove thousand separators
        s = re.sub(r"\s+", " ", s)
        return s

    lines = [_clean_line(l) for l in raw.split("\n")]
    return [l for l in lines if l]


# ------------------------ parsing helpers ------------------------

def _normalize_price_cell(cell: str, open_cap: float = OPEN_CAP) -> List[str]:
    """
    Normalize a price cell into canonical keys:
      "$11500 - $11999.99" -> ["11500.00-11999.99"]
      "$15000+"            -> ["15000.00+", "15000.00-1000000.00"]
      (no numbers)        -> ["0.00-1000000.00"]
    """
    s = cell.strip().replace(",", "")
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

    # If we can't parse any number, interpret as “any price”
    if not re.search(r"\d", s):
        return [f"0.00-{open_cap:.2f}"]

    return []


def _parse_fee_cell(cell: str) -> float | str | None:
    """
    Parse fee cell to:
      - float for dollars ("$500.00" -> 500.0)
      - "X% of sale price" for percentages ("6%"/"6% of sale price")
      - 0.0 for FREE / NO FEE
      - None if not recognizable
    """
    s = cell.strip().replace(",", "")
    if re.search(r"^(FREE|NO\s*FEE)$", s, re.I):
        return 0.0

    m = re.search(r"(\d+(?:\.\d+)?)\s*%(\s*of\s*sale\s*price)?", s, re.I)
    if m:
        pct = float(m.group(1))
        return f"{pct}% of sale price"

    m = re.match(r"^\$?(\d+(?:\.\d{1,2})?)$", s)
    if m:
        return float(m.group(1))

    return None


def _pair_rows(price_lines: List[str], fee_lines: List[str]) -> Dict[str, float | str | None]:
    """Pair price & fee lines by index; expand open-ended ranges into two keys; default to 0-OPEN_CAP if empty price."""
    out: Dict[str, float | str | None] = {}
    for p_txt, f_txt in zip_longest(price_lines, fee_lines, fillvalue=""):
        price_keys = _normalize_price_cell(p_txt or "", open_cap=OPEN_CAP)
        if not price_keys:
            continue
        fee_val = _parse_fee_cell(f_txt or "")
        for k in price_keys:
            out[k] = fee_val
    return out


def _crop_segment(img: Image.Image, seg: Tuple[int, int], inset_ratio: float = 0.02) -> Image.Image:
    w, h = img.size
    x0, x1 = seg
    inset = max(2, int((x1 - x0) * inset_ratio))
    return img.crop((x0 + inset, 0, x1 - inset, h))


def _detect_header_for_segment(bw_img: Image.Image, seg: Tuple[int, int]) -> str:
    """OCR only the top stripe of a column to guess its header."""
    x0, x1 = seg
    w, h = bw_img.size
    top = int(h * 0.12)  # take top 12% as header stripe
    stripe = bw_img.crop((x0, 0, x1, max(1, top)))
    txt = _ocr_text(stripe, psm=6)
    clean = re.sub(r"\s+", " ", txt.strip().upper())
    return clean


def parse_fee_table(svg_or_png_path: str, target_fee_header: Optional[str] = None) -> Dict[str, float | str | None]:
    """
    Parse a fee table:
      - If target_fee_header provided (e.g., "LIVE BID"), take that column as fee column.
      - Otherwise take the right-most column as fee column.
      - Price column = left-most column by default (or header containing PRICE/BID if found).
    """
    if not os.path.exists(svg_or_png_path):
        logger.error(f"File not found: {svg_or_png_path}")
        return {}

    # Load/render image
    try:
        _, ext = os.path.splitext(svg_or_png_path.lower())
        if ext == ".svg":
            with open(svg_or_png_path, "rb") as f:
                png_bytes = svg2png(bytestring=f.read(), output_width=2000)
            img = Image.open(BytesIO(png_bytes)).convert("RGB")
        else:
            img = Image.open(svg_or_png_path).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to open/render image: {e}")
        return {}

    bw = _binarize(img)
    segs = _vertical_content_segments(bw)

    # If only one block, split into two halves: left=price, right=fee
    if len(segs) == 1:
        a, b = segs[0]
        mid = (a + b) // 2
        price_seg = (a, mid)
        fee_seg = (mid + 1, b)
    else:
        # Heuristic 1: pick price segment (try by header)
        headers = [ _detect_header_for_segment(bw, s) for s in segs ]
        price_idx = None
        for i, hdr in enumerate(headers):
            if re.search(r"(PRICE|BID|SALE|WINNING)", hdr):
                price_idx = i
                break
        if price_idx is None:
            price_idx = 0  # left-most as fallback
        price_seg = segs[price_idx]

        # Heuristic 2: pick fee segment
        if target_fee_header:
            wanted = target_fee_header.strip().upper()
            fee_idx = None
            for i, hdr in enumerate(headers):
                if wanted in hdr:
                    fee_idx = i
                    break
            if fee_idx is None:
                # fallback: right-most
                fee_idx = len(segs) - 1
        else:
            fee_idx = len(segs) - 1

        fee_seg = segs[fee_idx]

    price_img = _crop_segment(bw, price_seg)
    fee_img   = _crop_segment(bw, fee_seg)

    try:
        price_lines = _ocr_lines(price_img, psm=6)
        fee_lines   = _ocr_lines(fee_img, psm=6)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return {}

    fees = _pair_rows(price_lines, fee_lines)
    logger.info(
        f"Parsed {len(fees)} rows from {os.path.basename(svg_or_png_path)} "
        f"(segments={len(segs)}; price_seg={price_seg}, fee_seg={fee_seg})"
    )
    return fees
