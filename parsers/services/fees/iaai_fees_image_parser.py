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
    """Convert SVG file to PNG and parse table using OCR."""
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    if not os.path.exists(svg_file_path):
        logger.error(f"SVG file {svg_file_path} not found.")
        return {}

    logger.info(f"Processing SVG file: {svg_file_path}")

    try:
        with open(svg_file_path, "rb") as svg_file:
            png_data = svg2png(bytestring=svg_file.read(), output_width=2000)
        img = Image.open(BytesIO(png_data))
        img.save("temp_image.png")
        logger.info("SVG converted to PNG and saved as temp_image.png")
    except Exception as e:
        logger.error(f"Failed to convert SVG to PNG: {str(e)}")
        return {}

    try:
        img = img.convert("L")
        img = img.point(lambda x: 0 if x < 128 else 255)
        text = pytesseract.image_to_string(img)
        logger.info(f"OCR extracted text:\n{text}")
    except Exception as e:
        logger.error(f"OCR failed: {str(e)}")
        return {}

    fees = {}
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    logger.info(f"Number of lines extracted from OCR: {len(lines)}")
    for i, line in enumerate(lines):
        logger.debug(f"Processing line {i+1}: {line}")
        line = line.replace("—", "-")
        line = re.sub(r"(\d+),(\d+)", r"\1\2", line)
        match = re.match(r"\$(\d+\.?\d*)\s*-\s*\$(\d+\.?\d*)\s*\$(\d+\.?\d*)", line)
        if match:
            min_price = float(match.group(1))
            max_price = float(match.group(2))
            fee = float(match.group(3))
            key = f"{min_price:.2f}-{max_price:.2f}"
            fees[key] = fee
            logger.info(f"Parsed fee: {key} -> {fee}")
        else:
            logger.warning(f"Line {i+1} does not match standard fee format: {line}")
        match_plus = re.match(r"\$(\d+\.?\d*)\+\s*(\d+\.?\d*)% of sale price", line)
        if match_plus:
            min_price = float(match_plus.group(1))
            fee_percent = float(match_plus.group(2))
            key = f"{min_price:.2f}+"
            fees[key] = f"{fee_percent}% of sale price"
            logger.info(f"Parsed fee (percent): {key} -> {fee_percent}% of sale price")
        elif not match:
            logger.warning(f"Line {i+1} does not match percentage fee format: {line}")
        match_free = re.match(r"\$(\d+\.?\d*)\s*-\s*\$(\d+\.?\d*)\s*FREE", line)
        if match_free:
            min_price = float(match_free.group(1))
            max_price = float(match_free.group(2))
            key = f"{min_price:.2f}-{max_price:.2f}"
            fees[key] = 0.0
            logger.info(f"Parsed fee (free): {key} -> 0.0")
        elif not match and not match_plus:
            logger.warning(f"Line {i+1} does not match free fee format: {line}")

    logger.info(f"Final parsed fees: {fees}")
    return fees
