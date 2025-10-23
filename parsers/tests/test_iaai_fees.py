# tests/test_iaai_fees.py
from __future__ import annotations

import re
import sys
import types

import pytest

# --------------------------------------------------------------------------------------
# Stub external deps BEFORE importing the parser module
# --------------------------------------------------------------------------------------
# Avoid system cairo requirement:
if "cairosvg" not in sys.modules:
    cairosvg_stub = types.SimpleNamespace(svg2png=lambda *a, **k: b"")
    sys.modules["cairosvg"] = cairosvg_stub

# Avoid OCR runtime and binary deps entirely (we don't use OCR in these tests)
if "pytesseract" not in sys.modules:
    pytesseract_stub = types.SimpleNamespace(image_to_string=lambda *a, **k: "")
    sys.modules["pytesseract"] = pytesseract_stub

# --------------------------------------------------------------------------------------
# Import target helpers (with fallback module name)
# --------------------------------------------------------------------------------------
try:
    from services.fees.iaai_fees_image_parser import (
        OPEN_CAP,
        _normalize_price_cell,
        _pair_rows,
        _parse_fee_cell,
    )
except ModuleNotFoundError:
    # Fallback if the file is named differently in the repo
    from services.fees.iaai_image_parser import (  # type: ignore
        OPEN_CAP,
        _normalize_price_cell,
        _pair_rows,
        _parse_fee_cell,
    )


# ======================================================================================
# _normalize_price_cell tests
# ======================================================================================

@pytest.mark.parametrize(
    "cell, expected",
    [
        ("$11500 - $11999.99", ["11500.00-11999.99"]),
        ("11500-11999.99",      ["11500.00-11999.99"]),
        ("$0 - $100",           ["0.00-100.00"]),
        ("0-100",               ["0.00-100.00"]),
        ("$15000+",             [f"15000.00+", f"15000.00-{OPEN_CAP:.2f}"]),
        ("15000+",              [f"15000.00+", f"15000.00-{OPEN_CAP:.2f}"]),
        ("Any",                 [f"0.00-{OPEN_CAP:.2f}"]),
        ("  ",                  [f"0.00-{OPEN_CAP:.2f}"]),
    ],
)
def test_normalize_price_cell_happy(cell, expected):
    assert _normalize_price_cell(cell, open_cap=OPEN_CAP) == expected


@pytest.mark.parametrize(
    "cell",
    [
        "100 - x",       # digits present but pattern invalid => []
        "500 -",         # incomplete range => []
        "$0 - $",        # incomplete => []
        "123x+",         # contains digits but invalid format => []
    ],
)
def test_normalize_price_cell_digits_but_unparsable_returns_empty(cell):
    assert _normalize_price_cell(cell, open_cap=OPEN_CAP) == []


@pytest.mark.parametrize(
    "cell",
    [
        "$foo - $bar",   # no digits at all => treated as “Any”
        "x+",
        " $  + ",
        "foo bar",
    ],
)
def test_normalize_price_cell_no_digits_treated_as_any(cell):
    assert _normalize_price_cell(cell, open_cap=OPEN_CAP) == [f"0.00-{OPEN_CAP:.2f}"]


# ======================================================================================
# _parse_fee_cell tests
# ======================================================================================

@pytest.mark.parametrize(
    "cell, expected",
    [
        ("$500.00", 500.0),
        ("500",     500.0),
        ("$1,250",  1250.0),
        ("15%",     "15.0% of sale price"),
        ("6% of sale price", "6.0% of sale price"),
        ("FREE", 0.0),
        ("No fee", 0.0),
        ("no FEE", 0.0),
        ("   free  ", 0.0),
    ],
)
def test_parse_fee_cell_happy(cell, expected):
    assert _parse_fee_cell(cell) == expected


@pytest.mark.parametrize(
    "cell",
    [
        "USD 5",
        "about $10",
        "fee equals 7 percent",
        "",
        " - ",
        "N/A",
        "some text",
    ],
)
def test_parse_fee_cell_unrecognized_returns_none(cell):
    assert _parse_fee_cell(cell) is None


# ======================================================================================
# _pair_rows tests
# ======================================================================================

def test_pair_rows_basic_aligned():
    prices = ["$0 - $100", "$15000+"]
    fees   = ["$25", "6%"]
    out = _pair_rows(prices, fees)

    assert out["0.00-100.00"] == 25.0
    # open-ended expands to two keys
    assert out["15000.00+"] == "6.0% of sale price"
    assert out[f"15000.00-{OPEN_CAP:.2f}"] == "6.0% of sale price"
    assert len(out) == 3


def test_pair_rows_prices_longer_than_fees_missing_fee_becomes_none():
    prices = ["$0 - $100", "$200 - $300", "$15000+"]
    fees   = ["FREE", "$999"]  # third fee missing
    out = _pair_rows(prices, fees)

    assert out["0.00-100.00"] == 0.0
    assert out["200.00-300.00"] == 999.0
    # third maps to None
    assert out["15000.00+"] is None
    assert out[f"15000.00-{OPEN_CAP:.2f}"] is None


def test_pair_rows_fees_longer_than_prices_extra_fees_go_to_any_range():
    prices = ["$0 - $100"]
    fees   = ["FREE", "$999", "6%"]
    out = _pair_rows(prices, fees)
    # first pair applies
    assert out["0.00-100.00"] == 0.0
    # extra fee rows map to "Any" (empty price => 0..OPEN_CAP)
    assert out[f"0.00-{OPEN_CAP:.2f}"] == "6.0% of sale price"
    assert len(out) == 2


def test_pair_rows_any_or_empty_as_full_range_overwrites_last_wins():
    prices = ["Any", "", "Any"]  # кожен трактуємо як 0..OPEN_CAP
    fees   = ["$10", "$20", "$30"]
    out = _pair_rows(prices, fees)
    # останній перетирає попередні
    assert out[f"0.00-{OPEN_CAP:.2f}"] == 30.0
    assert len(out) == 1


def test_pair_rows_unparsable_price_skips_row():
    prices = ["$0 - $100", "100 - x", "$15000+"]  # "100 - x" => []
    fees   = ["FREE", "6%", "$999"]
    out = _pair_rows(prices, fees)

    assert out["0.00-100.00"] == 0.0
    assert out["15000.00+"] == 999.0
    assert out[f"15000.00-{OPEN_CAP:.2f}"] == 999.0

    # жодного запису для 2-го рядка (unparsable)
    keys_str = " | ".join(out.keys())
    assert not re.search(r"\b100\.00-\b", keys_str)


def test_pair_rows_percentage_and_free_mix():
    prices = ["$0 - $100", "$300 - $500", "Any"]
    fees   = ["FREE", "6%", "$15"]
    out = _pair_rows(prices, fees)

    assert out["0.00-100.00"] == 0.0
    assert out["300.00-500.00"] == "6.0% of sale price"
    assert out[f"0.00-{OPEN_CAP:.2f}"] == 15.0


def test_pair_rows_duplicate_overwrite_last_wins():
    prices = ["$0 - $100", "$0 - $100"]
    fees   = ["$10", "$12"]
    out = _pair_rows(prices, fees)

    # останній дубль перезаписує
    assert out["0.00-100.00"] == 12.0
    assert len(out) == 1
