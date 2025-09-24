import json
import logging
import os
import re
from datetime import datetime
from io import BytesIO

import pytesseract
from bs4 import BeautifulSoup
from cairosvg import svg2png
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image

# Set up logging for debugging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Path to Tesseract executable for Linux (adjust if necessary)
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"


def parse_svg_table(svg_file_path):
    """Convert SVG file to PNG and parse fee table via OCR (supports open-ended ranges like $15,000+)."""
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    if not os.path.exists(svg_file_path):
        logger.error(f"SVG file {svg_file_path} not found.")
        return {}

    logger.info(f"Processing SVG file: {svg_file_path}")

    # --- SVG -> PNG ---
    try:
        with open(svg_file_path, "rb") as svg_file:
            png_data = svg2png(bytestring=svg_file.read(), output_width=2000)
        img = Image.open(BytesIO(png_data))
        img = img.convert("L").point(lambda x: 0 if x < 128 else 255)  # простий бінар
        # tesseract: psm 6 (один блок із декількома рядками)
        text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
        logger.debug(f"OCR raw text:\n{text}")
    except Exception as e:
        logger.error(f"OCR pipeline failed: {e}")
        return {}

    # --- helpers ---
    def _clean_line(s: str) -> str:
        s = s.strip()
        s = s.replace("—", "-").replace("–", "-")
        s = s.replace("O", "0")  # часта OCR-помилка
        # прибрати тисячні розділювачі
        s = re.sub(r"(?<=\d),(?=\d)", "", s)
        # нормалізувати пробіли
        s = re.sub(r"\s+", " ", s)
        return s

    def _to_float(num_str: str) -> float:
        return float(num_str.replace(",", ""))

    # патерни
    p_range_fee   = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?([\d,]+(?:\.\d{1,2})?)\s*\$?([\d,]+(?:\.\d{1,2})?)\s*$")
    p_range_free  = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?([\d,]+(?:\.\d{1,2})?)\s*(FREE|NO\s*FEE)\s*$", re.I)
    p_open_pct_in = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*\+\s*(\d+(?:\.\d+)?)\s*%(\s*of\s*sale\s*price)?\s*$", re.I)
    p_open_amt_in = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*\+\s*\$?([\d,]+(?:\.\d{1,2})?)\s*$")
    p_open_min    = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*\+\s*$")

    p_pct_only    = re.compile(r"(\d+(?:\.\d+)?)\s*%(\s*of\s*sale\s*price)?\s*$", re.I)
    p_amt_only    = re.compile(r"^\$?([\d,]+(?:\.\d{1,2})?)\s*$")
    p_free_only   = re.compile(r"^(FREE|NO\s*FEE)\s*$", re.I)

    lines = [_clean_line(l) for l in text.split("\n") if _clean_line(l)]
    logger.info(f"OCR lines: {len(lines)}")

    fees: dict[str, float | str] = {}
    OPEN_CAP = 1_000_000.00

    pending_min: float | None = None  # для кейсу "$15000+"
    i = 0
    while i < len(lines):
        line = lines[i]

        # 1) Фіксований діапазон із фіксованою сумою: "$11500 - $11999.99 $860.00"
        m = p_range_fee.match(line)
        if m:
            lo, hi, fee_amt = map(_to_float, m.groups())
            key = f"{lo:.2f}-{hi:.2f}"
            fees[key] = float(fee_amt)
            logger.debug(f"[range_fee] {key} -> {fees[key]}")
            i += 1
            continue

        # 2) Фіксований діапазон FREE
        m = p_range_free.match(line)
        if m:
            lo, hi = map(_to_float, m.group(1, 2))
            key = f"{lo:.2f}-{hi:.2f}"
            fees[key] = 0.0
            logger.debug(f"[range_free] {key} -> 0.0")
            i += 1
            continue

        # 3) Відкритий діапазон з відсотком у тому ж рядку: "$15000+ 6% of sale price"
        m = p_open_pct_in.match(line)
        if m:
            lo = _to_float(m.group(1))
            pct = float(m.group(2))
            for key in (f"{lo:.2f}+", f"{lo:.2f}-{OPEN_CAP:.2f}"):
                fees[key] = f"{pct}% of sale price"
            logger.debug(f"[open_pct_inline] {lo}+, {pct}%")
            i += 1
            continue

        # 4) Відкритий діапазон із фіксованою сумою у тому ж рядку: "$15000+ $500.00"
        m = p_open_amt_in.match(line)
        if m:
            lo = _to_float(m.group(1))
            fee_amt = _to_float(m.group(2))
            for key in (f"{lo:.2f}+", f"{lo:.2f}-{OPEN_CAP:.2f}"):
                fees[key] = float(fee_amt)
            logger.debug(f"[open_amt_inline] {lo}+ -> {fee_amt}")
            i += 1
            continue

        # 5) Лише мінімум "$15000+" → подивимось наступний рядок(и) на fee
        m = p_open_min.match(line)
        if m:
            pending_min = _to_float(m.group(1))
            # заглянемо вперед (1-2 рядки), бо інколи OCR ставить "6% of sale price" окремим рядком
            consumed = False
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j]
                mp = p_pct_only.search(nxt)
                if mp:
                    pct = float(mp.group(1))
                    for key in (f"{pending_min:.2f}+", f"{pending_min:.2f}-{OPEN_CAP:.2f}"):
                        fees[key] = f"{pct}% of sale price"
                    logger.debug(f"[open_min -> pct_next] {pending_min}+ -> {pct}%")
                    i = j + 1
                    pending_min = None
                    consumed = True
                    break
                ma = p_amt_only.match(nxt)
                if ma:
                    fee_amt = _to_float(ma.group(1))
                    for key in (f"{pending_min:.2f}+", f"{pending_min:.2f}-{OPEN_CAP:.2f}"):
                        fees[key] = float(fee_amt)
                    logger.debug(f"[open_min -> amt_next] {pending_min}+ -> {fee_amt}")
                    i = j + 1
                    pending_min = None
                    consumed = True
                    break
                mf = p_free_only.match(nxt)
                if mf:
                    for key in (f"{pending_min:.2f}+", f"{pending_min:.2f}-{OPEN_CAP:.2f}"):
                        fees[key] = 0.0
                    logger.debug(f"[open_min -> free_next] {pending_min}+ -> 0.0")
                    i = j + 1
                    pending_min = None
                    consumed = True
                    break
            if consumed:
                continue
            # якщо не знайшли fee поруч — просто зафіксуємо відкритий інтервал без значення
            for key in (f"{pending_min:.2f}+", f"{pending_min:.2f}-{OPEN_CAP:.2f}"):
                fees[key] = None  # невідомий fee; заповниш пізніше або пропустиш
            logger.warning(f"[open_min_only] {pending_min}+ -> fee not found nearby")
            pending_min = None
            i += 1
            continue

        # 6) Якщо рядок містить лише fee (інколи OCR може знести ліву частину)
        if p_pct_only.search(line) and pending_min is None:
            # немає контексту price_from — пропускаємо
            logger.warning(f"Percentage without base price range: {line}")
            i += 1
            continue
        if p_amt_only.match(line) and pending_min is None:
            logger.warning(f"Amount without base price range: {line}")
            i += 1
            continue

        # нічого не підійшло
        logger.debug(f"[unmatched] {line}")
        i += 1

    logger.info(f"Parsed fees: {fees}")
    return fees
