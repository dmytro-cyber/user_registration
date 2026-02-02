# services/fees/copart_fees_parser.py
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

MAX_PRICE_CAP = 1_000_000.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ===== Strategy Interface =====
class FeeScraperStrategy(ABC):
    @abstractmethod
    def scrape(self, soup: BeautifulSoup):
        ...


# ====== Bidding Fee (secured) ======
class BiddingFeeScraper(FeeScraperStrategy):
    """
    Парсить таблицю комісій за торги (Secured Payment Methods).
    Повертає {"secured": {"min-max": fee_float, ...}}
    """

    def _parse_table(self, table):
        fees = {}
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            price_range_raw = cols[0].get_text(strip=True)
            fee_raw = cols[1].get_text(strip=True)

            fee_txt = (
                fee_raw.replace("$", "")
                .replace(",", "")
                .replace("%", "")
                .strip()
                .upper()
            )
            if fee_txt == "FREE":
                fee = 0.0
            else:
                try:
                    fee = float(fee_txt)
                except Exception:
                    fee = float(fee_txt.split("%")[0])  # "10%" -> "10"

            rng = price_range_raw.replace(",", "").strip()
            min_price, max_price = 0.0, MAX_PRICE_CAP
            if "-" in rng:
                left, right = [s.strip() for s in rng.split("-", 1)]
                left = left.replace("$", "").replace("+", "")
                right = right.replace("$", "").replace("+", "")
                try:
                    min_price = float(left) if left else 0.0
                except Exception:
                    min_price = 0.0
                try:
                    max_price = float(right) if right else MAX_PRICE_CAP
                except Exception:
                    max_price = MAX_PRICE_CAP
            elif rng.endswith("+"):
                base = rng.replace("$", "").replace("+", "")
                try:
                    min_price = float(base) if base else 0.0
                except Exception:
                    min_price = 0.0
                max_price = MAX_PRICE_CAP
            else:
                min_price, max_price = 0.0, MAX_PRICE_CAP

            key = f"{min_price:.2f}-{max_price:.2f}"
            fees[key] = fee

        return fees

    def scrape(self, soup: BeautifulSoup):
        # 1) primary path: знайти заголовок "Secured Payment Methods"
        header = soup.find(string=lambda t: isinstance(t, str) and "Secured Payment Methods" in t)
        table = header.find_next("table") if header else None

        # 2) fallback: перша таблиця, у якій не всі fee == FREE
        if not table:
            for tbl in soup.find_all("table"):
                tds = [td.get_text(strip=True).upper() for td in tbl.find_all("td")]
                if tds and any(v != "FREE" for v in tds):
                    table = tbl
                    break

        if not table:
            logger.warning("Bidding fees table not found.")
            return {}

        return {"secured": self._parse_table(table)}


# ===== Gate Fee =====
class GateFeeScraper(FeeScraperStrategy):
    def scrape(self, soup: BeautifulSoup):
        section = soup.find(string=lambda t: isinstance(t, str) and "Gate Fee" in t)
        if not section:
            logger.warning("Gate Fee section not found.")
            return 0.0
        txt = section.find_next("p").get_text(strip=True)
        # допускаємо "A $79.00 Gate Fee ..." і просто "79.00"
        cleaned = (
            txt.replace("A $", "")
            .replace("A$", "")
            .replace("$", "")
            .replace(".00", "")
        )
        cleaned = cleaned.split()[0]
        return float(cleaned)


# ===== Virtual Bid Fee =====
class VirtualBidFeeScraper(FeeScraperStrategy):
    def scrape(self, soup: BeautifulSoup):
        header = soup.find(string=lambda t: isinstance(t, str) and "Virtual Bid Fee" in t)
        if not header:
            logger.warning("Virtual Bid Fee section not found.")
            return {"live_bid": {}}
        table = header.find_next("table")
        if not table:
            logger.warning("Virtual Bid Fee table not found after header.")
            return {"live_bid": {}}

        live_bid = {}
        rows = table.find_all("tr")[1:]
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            price_range_raw = cols[0].get_text(strip=True)
            fee_raw = cols[1].get_text(strip=True)

            fee_txt = fee_raw.replace("$", "").replace(",", "").strip().upper()
            fee_value = 0.0 if fee_txt == "FREE" else float(fee_txt)

            rng = price_range_raw.replace(",", "").strip()
            min_price, max_price = 0.0, MAX_PRICE_CAP
            if "-" in rng:
                left, right = [s.strip() for s in rng.split("-", 1)]
                left = left.replace("$", "").replace("+", "")
                right = right.replace("$", "").replace("+", "")
                try:
                    min_price = float(left) if left else 0.0
                except Exception:
                    min_price = 0.0
                try:
                    max_price = float(right) if right else MAX_PRICE_CAP
                except Exception:
                    max_price = MAX_PRICE_CAP
            elif rng.endswith("+"):
                base = rng.replace("$", "").replace("+", "")
                try:
                    min_price = float(base) if base else 0.0
                except Exception:
                    min_price = 0.0
                max_price = MAX_PRICE_CAP
            else:
                min_price, max_price = 0.0, MAX_PRICE_CAP

            key = f"{min_price:.2f}-{max_price:.2f}"
            live_bid[key] = fee_value

        return {"live_bid": live_bid}


# ===== Environmental Fee =====
class EnvironmentalFeeScraper(FeeScraperStrategy):
    def scrape(self, soup: BeautifulSoup):
        section = soup.find(string=lambda t: isinstance(t, str) and "Environmental Fee" in t)
        if not section:
            logger.warning("Environmental Fee section not found.")
            return 0.0
        txt = section.find_next("p").get_text(strip=True)
        cleaned = (
            txt.replace("A $", "")
            .replace("$", "")
            .replace(".00", "")
        )
        cleaned = cleaned.split()[0]
        return float(cleaned)


# ===== Orchestrator =====
class FeeScraper:
    def __init__(self, url: str):
        self.url = url
        self.strategies = {
            "bidding_fees": BiddingFeeScraper(),
            "gate_fee": GateFeeScraper(),
            "virtual_bid_fee": VirtualBidFeeScraper(),
            "environmental_fee": EnvironmentalFeeScraper(),
        }

    def scrape_page(self, driver) -> BeautifulSoup:
        driver.get(self.url)
        logger.info("Page loaded: %s", self.url)

        try:
            clean_title_el = None

            # main content
            try:
                # FIX: шукаємо саме таб, а не будь-який текст
                clean_title_el = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "//a[contains(@aria-controls,'noncleanmore') and contains(., 'Non-Clean Title')]"
                        )
                    )
                )
                logger.info("Found 'Non-Clean Title Vehicles' tab in main content.")
            except Exception as e:
                logger.warning("Element not found in main content: %s. Checking iframe.", str(e))

            # iframe[0] fallback
            if clean_title_el is None:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                logger.info("Found %d iframe(s) on the page.", len(iframes))
                if iframes:
                    try:
                        driver.switch_to.frame(iframes[0])
                        logger.info("Switched to iframe 0.")

                        # FIX: той самий XPATH в iframe
                        clean_title_el = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable(
                                (
                                    By.XPATH,
                                    "//a[contains(@aria-controls,'noncleanmore') and contains(., 'Non-Clean Title')]"
                                )
                            )
                        )
                        logger.info("Found 'Non-Clean Title Vehicles' tab in iframe 0.")
                    except Exception as e:
                        logger.warning("Element not found in iframe 0: %s.", str(e))
                        driver.switch_to.default_content()
                        raise Exception("Could not find 'Non-Clean Title Vehicles' element in main content or iframe 0.")

            if clean_title_el is None:
                raise Exception("Could not find 'Non-Clean Title Vehicles' element.")

            # make clickable & click (твій код залишаємо)
            driver.execute_script(
                """
                var el = arguments[0];
                el.style.display = 'block';
                el.style.visibility = 'visible';
                el.removeAttribute('disabled');
                el.classList.remove('disabled');
                ['mouseover','mousemove','mouseenter','mousedown'].forEach(t=>{
                    el.dispatchEvent(new Event(t, {bubbles:true}));
                });
                """,
                clean_title_el,
            )
            logger.info("Prepared element for click.")

            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script(
                    "return arguments[0].offsetParent !== null && window.getComputedStyle(arguments[0]).visibility !== 'hidden';",
                    clean_title_el,
                )
                and clean_title_el.is_enabled()
            )

            time.sleep(1)
            driver.execute_script("arguments[0].click();", clean_title_el)
            logger.info("Clicked.")

            # FIX: чекаємо що активувався САМЕ ЦЕЙ таб
            WebDriverWait(driver, 10).until(
                lambda d: clean_title_el.get_attribute("aria-selected") == "true"
            )
            logger.info("Non-Clean tab is active.")

            # FIX: беремо id контейнера активного таба
            pane_id = clean_title_el.get_attribute("aria-controls")

            WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.ID, pane_id))
            )
            logger.info("Non-Clean pane visible.")

            driver.switch_to.default_content()

            # FIX: парсимо ТІЛЬКИ активний контейнер
            pane = driver.find_element(By.ID, pane_id)
            html = pane.get_attribute("innerHTML")

            return BeautifulSoup(html, "html.parser")

        except Exception as e:
            logger.error("Failed to interact with page elements: %s", str(e))
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            for idx, iframe in enumerate(driver.find_elements(By.TAG_NAME, "iframe")):
                driver.switch_to.frame(iframe)
                with open(f"debug_iframe_{idx}.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                driver.switch_to.default_content()
            raise


    def collect_fees(self, soup: BeautifulSoup):
        fees = {}
        for fee_type, strategy in self.strategies.items():
            if fee_type in ("gate_fee", "environmental_fee"):
                amount = strategy.scrape(soup)
                fees[fee_type] = {
                    "always_charged": True,
                    "amount": amount,
                    "currency": "USD",
                    "threshold": None,
                }
                if fee_type == "gate_fee":
                    fees[fee_type]["description"] = (
                        "Covers administrative costs and the movement of the item "
                        "from our storage location to the Buyer loading area"
                    )
                else:
                    fees[fee_type]["description"] = (
                        "Covers the cost of precise handling and care in compliance with environmental regulations"
                    )
                    fees[fee_type]["note"] = (
                        "The amount is listed as $0 on the official Copart page, "
                        "but this may be an error as other sources typically indicate $15."
                    )
            elif fee_type == "virtual_bid_fee":
                bid_data = strategy.scrape(soup)
                fees[fee_type] = {
                    "always_charged": True,
                    "live_bid": bid_data["live_bid"],
                    "currency": "USD",
                    "description": "Charges based on the high bid amount for online and live bids",
                }
            else:
                fees[fee_type] = strategy.scrape(soup)
        return fees


def scrape_copart_fees():
    url = "https://www.copart.com/content/us/en/member-fees-us-licensed-more"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")  # у CI хай лишається headless
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.maximize_window()
        logger.info("Maximized browser window.")
        scraper = FeeScraper(url)
        soup = scraper.scrape_page(driver)
        return {
            "source": "copart",
            "payment_method": "secured",
            "fees": scraper.collect_fees(soup),
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        driver.quit()
