import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


MAX_PRICE_CAP = 1_000_000.0

# Set up logging for debugging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Abstract base class for scraping strategies
class FeeScraperStrategy(ABC):
    """Abstract base class defining the interface for fee scraping strategies."""

    @abstractmethod
    def scrape(self, soup):
        """Abstract method to scrape fee data from BeautifulSoup object."""
        pass


# Concrete strategy for scraping bidding fees
class BiddingFeeScraper(FeeScraperStrategy):
    """Concrete strategy to scrape bidding fees from tables."""

    def scrape(self, soup):
        """Scrape bidding fees from the provided soup object."""
        bidding_tables = soup.find_all("table")
        if len(bidding_tables) < 2:
            logger.warning("Bidding fees tables not found.")
            return {}

        def parse_table(table, payment_type):
            """Parse a price/fee table into { 'min-max': fee } with capped '+' ranges."""
            fees = {}
            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue

                # raw texts
                price_range_raw = cols[0].get_text(strip=True)
                fee_raw = cols[1].get_text(strip=True)

                # normalize fee -> float (strip $, %, commas)
                fee_txt = fee_raw.replace("$", "").replace(",", "").replace("%", "").strip()
                try:
                    fee = float(fee_txt)
                except Exception:
                    # try last-resort split on '%'
                    fee = float(fee_txt.split("%")[0])

                # normalize range
                rng = price_range_raw.replace(",", "").strip()

                # cases:
                # 1) "$X - $Y" => X..Y
                # 2) "$X+"     => X..MAX_PRICE_CAP
                # 3) anything else (unknown/”Any”) => 0..MAX_PRICE_CAP
                min_price, max_price = 0.0, MAX_PRICE_CAP
                if "-" in rng:
                    left, right = [s.strip() for s in rng.split("-", 1)]
                    # left like "$0" or "$15000.00"
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
                    # "$15000.00+" => 15000..MAX_PRICE_CAP
                    base = rng.replace("$", "").replace("+", "")
                    try:
                        min_price = float(base) if base else 0.0
                    except Exception:
                        min_price = 0.0
                    max_price = MAX_PRICE_CAP
                else:
                    # no explicit bounds => 0..MAX_PRICE_CAP
                    min_price, max_price = 0.0, MAX_PRICE_CAP

                key = f"{min_price:.2f}-{max_price:.2f}"
                fees[key] = fee

            return {payment_type: fees}

        # If you later add non-secured table parsing, plug it here as well.
        return {"secured": parse_table(bidding_tables[0], "secured")}


# Concrete strategy for scraping gate fee
class GateFeeScraper(FeeScraperStrategy):
    """Concrete strategy to scrape gate fee from the page."""

    def scrape(self, soup):
        """Scrape gate fee from the provided soup object."""
        gate_fee_section = soup.find(string="Gate Fee")
        if not gate_fee_section:
            logger.warning("Gate Fee section not found.")
            return 0.0
        gate_fee_text = gate_fee_section.find_next("p").get_text(strip=True)
        # Remove 'A $', ' Gate Fee is assessed to all Copart purchases.', and '.00'
        gate_fee_value = gate_fee_text.replace("A$", "").replace(".00", "")
        gate_fee_value = gate_fee_value.split()[0]
        return float(gate_fee_value)


# Concrete strategy for scraping virtual bid fee
class VirtualBidFeeScraper(FeeScraperStrategy):
    """Concrete strategy to scrape virtual bid fees from the table."""

    def scrape(self, soup):
        """Scrape virtual bid fees from the provided soup object."""
        virtual_bid_header = soup.find(string="Virtual Bid Fee")
        if not virtual_bid_header:
            logger.warning("Virtual Bid Fee section not found.")
            return {"live_bid": {}}

        table = virtual_bid_header.find_next("table")
        if not table:
            logger.warning("Virtual Bid Fee table not found after header.")
            return {"live_bid": {}}

        live_bid = {}
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            price_range_raw = cols[0].get_text(strip=True)
            fee_raw = cols[1].get_text(strip=True)

            # fee can be "$X" or "FREE"
            fee_txt = fee_raw.replace("$", "").replace(",", "").strip().upper()
            fee_value = 0.0 if fee_txt == "FREE" else float(fee_txt)

            rng = price_range_raw.replace(",", "").strip()

            # cases mirror the bidding scraper:
            # 1) "$X - $Y" => X..Y
            # 2) "$X+"     => X..MAX_PRICE_CAP
            # 3) else      => 0..MAX_PRICE_CAP
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


# Concrete strategy for scraping environmental fee
class EnvironmentalFeeScraper(FeeScraperStrategy):
    """Concrete strategy to scrape environmental fee from the page."""

    def scrape(self, soup):
        """Scrape environmental fee from the provided soup object."""
        environmental_fee_section = soup.find(string="Environmental Fee")
        if not environmental_fee_section:
            logger.warning("Environmental Fee section not found.")
            return 0.0
        environmental_fee_text = environmental_fee_section.find_next("p").get_text(strip=True)
        # Remove 'A $', description, and '.00'
        environmental_fee_value = (
            environmental_fee_text.replace("A $", "")
            .replace(
                " fee is applied to each item sold, which covers the cost of precise handling and care in compliance with environmental regulations.",
                "",
            )
            .replace(".00", "")
        )
        return float(environmental_fee_value)


# Class to manage the scraping process
class FeeScraper:
    """Class to manage the scraping process using different strategies."""

    def __init__(self, url):
        """Initialize with the target URL."""
        self.url = url
        self.strategies = {
            "bidding_fees": BiddingFeeScraper(),
            "gate_fee": GateFeeScraper(),
            "virtual_bid_fee": VirtualBidFeeScraper(),
            "environmental_fee": EnvironmentalFeeScraper(),
        }

    def scrape_page(self, driver):
        """Scrape the page using Selenium and return the parsed soup."""
        driver.get(self.url)
        logger.info("Page loaded: %s", self.url)

        try:
            # First, try to find the element in the main content
            clean_title_element = None
            try:
                clean_title_element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//*[contains(normalize-space(text()), 'Non-Clean Title Vehicles')]")
                    )
                )
                logger.info("Found 'Non-Clean Title Vehicles' element in main content.")
            except Exception as e:
                logger.warning("Element not found in main content: %s. Checking iframe.", str(e))

            # If not found in main content, check iframe 0 only
            if clean_title_element is None:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                logger.info("Found %d iframe(s) on the page.", len(iframes))
                if iframes:
                    try:
                        driver.switch_to.frame(iframes[0])
                        logger.info("Switched to iframe 0.")
                        clean_title_element = WebDriverWait(driver, 10).until(
                            EC.presence_of_element_located(
                                (By.XPATH, "//*[contains(normalize-space(text()), 'Non-Clean Title Vehicles')]")
                            )
                        )
                        logger.info("Found 'Non-Clean Title Vehicles' element in iframe 0.")
                    except Exception as e:
                        logger.warning("Element not found in iframe 0: %s.", str(e))
                        driver.switch_to.default_content()
                        raise Exception("Could not find 'Non-Clean Title Vehicles' element in main content or iframe 0.")

            if clean_title_element is None:
                raise Exception("Could not find 'Non-Clean Title Vehicles' element.")

            # Force the element to be clickable by modifying its properties
            driver.execute_script(
                """
                var element = arguments[0];
                element.style.display = 'block';
                element.style.visibility = 'visible';
                element.removeAttribute('disabled');
                element.classList.remove('disabled');
                element.classList.add('active');
                ['mouseover', 'mousemove', 'mouseenter', 'mousedown'].forEach(eventType => {
                    var event = new Event(eventType, { bubbles: true });
                    element.dispatchEvent(event);
                });
            """,
                clean_title_element,
            )
            logger.info("Forced 'Non-Clean Title Vehicles' element to be clickable and triggered mouse events.")

            # Wait for the element to become clickable
            def is_element_clickable(driver):
                try:
                    displayed = driver.execute_script(
                        "return arguments[0].offsetParent !== null && window.getComputedStyle(arguments[0]).visibility !== 'hidden';",
                        clean_title_element,
                    )
                    enabled = clean_title_element.is_enabled()
                    return displayed and enabled
                except:
                    return False

            WebDriverWait(driver, 10).until(lambda d: is_element_clickable(d))
            logger.info("'Non-Clean Title Vehicles' element is now clickable.")

            # Additional delay to ensure stability
            time.sleep(2)
            logger.info("Added additional delay for element to stabilize.")

            # Click the element using JavaScript as a fallback
            driver.execute_script("arguments[0].click();", clean_title_element)
            logger.info("Clicked on 'Non-Clean Title Vehicles' button using JavaScript.")

            # Wait for the Secured Payment Methods section
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Secured Payment Methods')]"))
            )
            logger.info("Secured Payment Methods section appeared.")

            # Switch back to default content
            driver.switch_to.default_content()

        except Exception as e:
            logger.error("Failed to interact with page elements: %s", str(e))
            # Save page source and iframe contents for debugging
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            for index, iframe in enumerate(driver.find_elements(By.TAG_NAME, "iframe")):
                driver.switch_to.frame(iframe)
                with open(f"debug_iframe_{index}.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                driver.switch_to.default_content()
            raise

        return BeautifulSoup(driver.page_source, "html.parser")

    def collect_fees(self, soup):
        """Collect all fees using the respective strategies."""
        fees = {}
        for fee_type, strategy in self.strategies.items():
            if fee_type in ["gate_fee", "environmental_fee"]:
                fees[fee_type] = {
                    "always_charged": True,
                    "amount": strategy.scrape(soup),
                    "currency": "USD",
                    "threshold": None,
                }
                if fee_type == "gate_fee":
                    fees[fee_type][
                        "description"
                    ] = "Covers administrative costs and the movement of the item from our storage location to the Buyer loading area"
                else:
                    fees[fee_type][
                        "description"
                    ] = "Covers the cost of precise handling and care in compliance with environmental regulations"
                    fees[fee_type][
                        "note"
                    ] = "The amount is listed as $0 on the official Copart page, but this may be an error as other sources typically indicate $15."
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


# Main execution
def scrape_copart_fees():
    """Main function to execute the scraping process."""
    url = "https://www.copart.com/content/us/en/member-fees-us-licensed-more"
    options = webdriver.ChromeOptions()
    # Keep headless mode disabled for debugging
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        # Maximize window to ensure focus (non-headless mode)
        driver.maximize_window()
        logger.info("Maximized browser window.")

        scraper = FeeScraper(url)
        soup = scraper.scrape_page(driver)
        fees_data = {
            "source": "copart",
            "payment_method": "secured",
            "fees": scraper.collect_fees(soup),
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        return fees_data

    finally:
        driver.quit()
