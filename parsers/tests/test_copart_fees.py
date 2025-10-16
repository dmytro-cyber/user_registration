# tests/test_copart_fees.py
from bs4 import BeautifulSoup
from unittest.mock import patch, MagicMock

import os
import pytest

from services.fees.copart_fees_parser import (
    BiddingFeeScraper,
    GateFeeScraper,
    VirtualBidFeeScraper,
    EnvironmentalFeeScraper,
    FeeScraper,
    scrape_copart_fees,
)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ========== Bidding Fee ==========
def test_bidding_fee_scraper_ranges_and_percents():
    html = """
    <div>Secured Payment Methods</div>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>$25</td></tr>
      <tr><td>$15000.00+</td><td>75</td></tr>
      <tr><td>Any</td><td>10%</td></tr>
    </table>
    """
    data = BiddingFeeScraper().scrape(soup(html))
    secured = data["secured"]
    assert secured["0.00-100.00"] == 25.0
    assert secured["15000.00-1000000.00"] == 75.0
    # "Any" -> 0..MAX_PRICE_CAP
    assert secured["0.00-1000000.00"] == 10.0


def test_bidding_fee_scraper_no_tables_returns_empty():
    html = "<div>No tables here</div>"
    data = BiddingFeeScraper().scrape(soup(html))
    assert data == {}


# ========== Virtual Bid ==========
def test_virtual_bid_fee_parses_free_and_ranges():
    html = """
    <h2>Virtual Bid Fee</h2>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>FREE</td></tr>
      <tr><td>$100+</td><td>$50</td></tr>
    </table>
    """
    data = VirtualBidFeeScraper().scrape(soup(html))
    assert data["live_bid"]["0.00-100.00"] == 0.0
    assert data["live_bid"]["100.00-1000000.00"] == 50.0


def test_virtual_bid_fee_missing_returns_empty():
    data = VirtualBidFeeScraper().scrape(soup("<div>No Virtual Bid Fee here</div>"))
    assert data == {"live_bid": {}}


# ========== Gate Fee ==========
def test_gate_fee_parses_value_simple_number():
    html = "<div>Gate Fee</div><p>79.00</p>"
    fee = GateFeeScraper().scrape(soup(html))
    assert fee == 79.0


def test_gate_fee_missing_returns_zero():
    fee = GateFeeScraper().scrape(soup("<div>Other Fee</div>"))
    assert fee == 0.0


# ========== Environmental Fee ==========
def test_environmental_fee_parses_value():
    html = "<div>Environmental Fee</div><p>15.00</p>"
    fee = EnvironmentalFeeScraper().scrape(soup(html))
    assert fee == 15.0


def test_environmental_fee_missing_returns_zero():
    fee = EnvironmentalFeeScraper().scrape(soup("<div>Nothing here</div>"))
    assert fee == 0.0


# ========== Aggregator ==========
def test_collect_fees_aggregates_all_sections():
    html = """
    <div>Gate Fee</div><p>79.00</p>
    <div>Environmental Fee</div><p>15.00</p>
    <h2>Virtual Bid Fee</h2>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>FREE</td></tr>
    </table>
    <div>Secured Payment Methods</div>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>$25</td></tr>
    </table>
    """
    fs = FeeScraper("http://example")
    fees = fs.collect_fees(soup(html))

    assert fees["gate_fee"]["amount"] == 79.0
    assert fees["environmental_fee"]["amount"] == 15.0
    assert fees["virtual_bid_fee"]["live_bid"]["0.00-100.00"] == 0.0
    assert fees["bidding_fees"]["secured"]["0.00-100.00"] == 25.0


# ========== scrape_page Selenium paths ==========
# Простенькі "заглушки" для WebDriverWait/EC

class _AlwaysTrueWait:
    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout
    def until(self, condition_or_lambda):
        # просто повертаємо будь-що, що схоже на елемент
        return self.driver._clean_title_element


def _ok_presence(*args, **kwargs):
    # повертаємо callable, який імітує EC.presence_of_element_located
    def _inner(_driver):
        return True
    return _inner


class FakeEl:
    def __init__(self):
        self._enabled = True
    def is_enabled(self): return self._enabled


class FakeChrome:
    def __init__(self, *args, **kwargs):
        self.page_source = "<html><body><div>Secured Payment Methods</div></body></html>"
        self._iframes = []
        self._clean_title_element = FakeEl()
        self.switch_to = MagicMock()
    def get(self, url): pass
    def find_elements(self, by=None, value=None):
        if by == "tag name" or by == "tagName" or by == "_":
            return self._iframes
        return self._iframes
    def execute_script(self, script, *args):
        return True
    def maximize_window(self): pass
    def quit(self): pass


@patch("services.fees.copart_fees_parser.WebDriverWait", new=_AlwaysTrueWait)
@patch("services.fees.copart_fees_parser.EC.presence_of_element_located", new=_ok_presence)
def test_scrape_page_main_content_path():
    driver = FakeChrome()
    fs = FeeScraper("http://example")
    bs = fs.scrape_page(driver)
    assert isinstance(bs, BeautifulSoup)


class _WaitFactory:
    def __init__(self, driver, timeout): self.driver = driver
    def until(self, cond):
        return True


@patch("services.fees.copart_fees_parser.WebDriverWait", new=_WaitFactory)
@patch("services.fees.copart_fees_parser.EC.presence_of_element_located", new=_ok_presence)
def test_scrape_page_iframe_fallback():
    driver = FakeChrome()
    # зімітуємо iframe
    fake_iframe = object()
    driver._iframes = [fake_iframe]
    fs = FeeScraper("http://example")
    bs = fs.scrape_page(driver)
    assert isinstance(bs, BeautifulSoup)


def test_scrape_page_writes_debug_files_and_raises(tmp_path, monkeypatch):
    # driver, який завалить .scrape_page на пошуку елемента
    class BoomChrome(FakeChrome):
        def find_elements(self, *args, **kwargs):
            return [object()]  # один iframe
    monkeypatch.chdir(tmp_path)

    # підміняємо WebDriverWait так, щоб він кидав ексепшн
    class _FailWait:
        def __init__(self, *a, **k): pass
        def until(self, *a, **k): raise RuntimeError("no element")

    with patch("services.fees.copart_fees_parser.WebDriverWait", new=_FailWait), \
         patch("services.fees.copart_fees_parser.EC.presence_of_element_located", new=_ok_presence):
        driver = BoomChrome()
        fs = FeeScraper("http://example")
        with pytest.raises(Exception):
            fs.scrape_page(driver)

        # перевіряємо, що файли дампів створено
        assert (tmp_path / "debug_page.html").exists()
        assert (tmp_path / "debug_iframe_0.html").exists()


# ========== scrape_copart_fees wrapper ==========
def minimal_working_soup():
    html = """
    <div>Gate Fee</div><p>79.00</p>
    <div>Environmental Fee</div><p>15.00</p>
    <h2>Virtual Bid Fee</h2>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>FREE</td></tr>
    </table>
    <div>Secured Payment Methods</div>
    <table>
      <tr><th>Range</th><th>Fee</th></tr>
      <tr><td>$0 - $100</td><td>$25</td></tr>
    </table>
    """
    return soup(html)


@patch("services.fees.copart_fees_parser.ChromeDriverManager")
@patch("services.fees.copart_fees_parser.webdriver.Chrome", new=FakeChrome)
def test_scrape_copart_fees_returns_dict_and_quits(chrome_mgr_mock):
    chrome_mgr_mock().install.return_value = "/fake/driver"
    with patch.object(FeeScraper, "scrape_page", return_value=minimal_working_soup()):
        data = scrape_copart_fees()
    assert data["source"] == "copart"
    assert data["payment_method"] == "secured"
    assert data["fees"]["gate_fee"]["amount"] == 79.0
    assert data["fees"]["environmental_fee"]["amount"] == 15.0
    assert data["fees"]["virtual_bid_fee"]["live_bid"]["0.00-100.00"] == 0.0
    assert data["fees"]["bidding_fees"]["secured"]["0.00-100.00"] == 25.0
    # є timestamp
    assert "scraped_at" in data
