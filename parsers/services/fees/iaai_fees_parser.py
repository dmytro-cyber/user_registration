import json
from datetime import datetime
import logging
from playwright.sync_api import sync_playwright
import requests
from io import BytesIO
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
import re
import os
import time
import random
import pyautogui
from cairosvg import svg2png
from twocaptcha import TwoCaptcha

# Set up logging for debugging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Path to Tesseract executable (adjust if necessary)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# 2Captcha API key (replace with your key)
TWO_CAPTCHA_API_KEY = "your_2captcha_api_key_here"


class IAABuyerFeeScraper:
    """Class to scrape buyer fees from IAA website using Playwright with detailed logging."""

    def __init__(self, url):
        """Initialize with the target URL."""
        self.url = url
        self.page = None
        self.browser = None
        self.context = None
        self.solver = TwoCaptcha(TWO_CAPTCHA_API_KEY)

    def simulate_human_behavior(self):
        """Simulate human-like behavior with random delays, mouse movements, and scroll."""
        delay = random.uniform(1, 5)
        time.sleep(delay)
        screen_width, screen_height = pyautogui.size()
        pyautogui.moveTo(
            random.randint(0, screen_width), random.randint(0, screen_height), duration=random.uniform(0.5, 1.5)
        )
        pyautogui.scroll(random.randint(-200, 200))
        logger.info(f"Simulated human behavior: delayed for {delay:.2f} seconds, moved mouse, and scrolled.")

    def detect_hcaptcha(self, max_attempts=3, delay_between_attempts=5000):
        """Detect hCaptcha with multiple attempts."""
        for attempt in range(max_attempts):
            logger.info(f"Attempt {attempt + 1}/{max_attempts} to detect hCaptcha...")

            # Simulate human behavior to trigger CAPTCHA
            self.simulate_human_behavior()

            # Save screenshot and HTML for debugging
            screenshot_path = f"captcha_screenshot_attempt_{attempt + 1}.png"
            html_path = f"captcha_debug_html_attempt_{attempt + 1}.html"
            self.page.screenshot(path=screenshot_path)
            logger.info(f"Saved screenshot as {screenshot_path} for debugging.")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self.page.content())
            logger.info(f"Saved HTML as {html_path} for debugging.")

            # Extended selectors for hCaptcha detection
            captcha_detected = self.page.evaluate(
                """() => {
                    const selectors = [
                        'iframe[src*="hcaptcha.com"]',
                        'div.h-captcha',
                        'div[data-hcaptcha-widget-id]',
                        'div[data-sitekey]',
                        'script[src*="hcaptcha.com"]',
                        'textarea[name="h-captcha-response"]',
                        'div[data-hcaptcha-container]'
                    ];
                    for (const selector of selectors) {
                        const element = document.querySelector(selector);
                        if (element) {
                            console.log(`Found hCaptcha element: ${selector}`);
                            return { found: true, element: selector };
                        }
                    }
                    return { found: false };
                }"""
            )

            if captcha_detected["found"]:
                logger.info(f"hCaptcha detected on attempt {attempt + 1}: {captcha_detected['element']}")
                return True

            logger.info(
                f"No hCaptcha detected on attempt {attempt + 1}. Waiting {delay_between_attempts/1000} seconds before next attempt."
            )
            self.page.wait_for_timeout(delay_between_attempts)

        logger.warning("No hCaptcha detected after all attempts.")
        return False

    def solve_hcaptcha(self):
        """Solve hCaptcha using 2Captcha service."""
        try:
            # Get sitekey from the page
            sitekey = self.page.evaluate(
                """() => {
                    const element = document.querySelector('div[data-sitekey]');
                    return element ? element.getAttribute('data-sitekey') : null;
                }"""
            )
            if not sitekey:
                logger.error("Could not find hCaptcha sitekey.")
                return None

            logger.info(f"Found hCaptcha sitekey: {sitekey}")

            # Send request to 2Captcha
            result = self.solver.hcaptcha(sitekey=sitekey, url=self.url)

            if result and "code" in result:
                captcha_response = result["code"]
                logger.info("hCaptcha solved successfully via 2Captcha.")
                return captcha_response
            else:
                logger.error("Failed to solve hCaptcha via 2Captcha.")
                return None
        except Exception as e:
            logger.error(f"Error solving hCaptcha: {str(e)}")
            return None

    def handle_hcaptcha(self):
        """Detect and handle hCaptcha if present."""
        if self.detect_hcaptcha(max_attempts=3, delay_between_attempts=5000):
            logger.warning("hCaptcha detected. Attempting to solve with 2Captcha.")
            captcha_response = self.solve_hcaptcha()
            if captcha_response:
                # Insert the token into the hCaptcha field
                self.page.evaluate(
                    f"""(captchaResponse) => {{
                        const textarea = document.querySelector('textarea[name="h-captcha-response"]');
                        if (textarea) {{
                            textarea.value = captchaResponse;
                        }}
                        const callback = window.grecaptcha && window.grecaptcha.execute;
                        if (callback) {{
                            callback();
                        }}
                    }}""",
                    captcha_response,
                )
                logger.info("Inserted hCaptcha response and triggered callback.")
                self.simulate_human_behavior()
                # Wait for the page to update after passing CAPTCHA
                self.page.wait_for_timeout(5000)
                # Save cookies after passing CAPTCHA
                cookies = self.page.context.cookies()
                with open("cookies.json", "w") as f:
                    json.dump(cookies, f)
                logger.info(f"Cookies updated and saved after CAPTCHA. Total cookies: {len(cookies)}")
            else:
                logger.error("Could not solve hCaptcha with 2Captcha. Falling back to manual resolution.")
                logger.warning("Please solve the CAPTCHA manually and press Enter to continue.")
                input("Press Enter after solving CAPTCHA...")
                self.simulate_human_behavior()
        else:
            logger.info("No hCaptcha detected after extended checks. Proceeding with scraping.")

    def scrape_page(self):
        """Scrape the page using Playwright and return the parsed HTML."""
        cookies_file = "cookies.json"
        cookies = []
        if os.path.exists(cookies_file):
            with open(cookies_file, "r") as f:
                cookies = json.load(f)
            logger.info(f"Loaded {len(cookies)} cookies from {cookies_file}.")

        playwright = sync_playwright().__enter__()
        self.browser = playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Connection": "keep-alive",
            },
        )
        if cookies:
            self.context.add_cookies(cookies)
        self.page = self.context.new_page()
        logger.info("Browser launched with custom settings.")

        try:
            response = self.page.goto(self.url, timeout=60000, wait_until="domcontentloaded")
            logger.info(f"Page navigation response status: {response.status}")
            if response.status != 200:
                logger.error(f"Failed to load page: HTTP {response.status}")
                raise Exception(f"HTTP {response.status} error")
            logger.info("Page loaded: %s", self.url)
            self.simulate_human_behavior()

            # Handle hCaptcha
            self.handle_hcaptcha()

            cookies = self.page.context.cookies()
            with open(cookies_file, "w") as f:
                json.dump(cookies, f)
            logger.info(f"Cookies saved to {cookies_file}. Total cookies: {len(cookies)}")

            self.page.wait_for_timeout(15000)
            logger.info("Waited 15 seconds for JavaScript execution.")

            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            logger.info("Scrolled to the bottom of the page.")
            self.page.wait_for_timeout(2000)
            logger.info("Waited 2 seconds for lazy-loaded content.")
            self.page.evaluate("window.scrollTo(0, 0)")
            self.page.wait_for_timeout(2000)
            logger.info("Scrolled back to top and waited 2 seconds.")

            self.page.wait_for_selector("img", timeout=20000)
            logger.info("Images loaded on page.")

            content = self.page.content()
            soup = BeautifulSoup(content, "html.parser")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(soup.prettify())
            logger.info("Debug HTML saved as debug_page.html")
            return soup
        except Exception as e:
            logger.error("Failed to load page: %s", str(e))
            raise

    def download_image(self, img_url):
        """Download an image and save it as SVG if applicable."""
        self.simulate_human_behavior()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        logger.info(f"Attempting to download image from URL: {img_url}")
        response = requests.get(img_url, headers=headers)
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "").lower()
            if "svg" in content_type or img_url.lower().endswith(".svg"):
                logger.info("Detected SVG image, saving as temp.svg.")
                with open("temp.svg", "wb") as f:
                    f.write(response.content)
                return "temp.svg"
            else:
                img = Image.open(BytesIO(response.content))
                img.save("debug_image.png")
                logger.info("Image downloaded and saved as debug_image.png")
                logger.info(f"Image dimensions: {img.size}")
                return img
        else:
            logger.error(f"Failed to download image: {img_url}, Status code: {response.status_code}")
            return None

    def parse_svg_table(self, svg_filename):
        """Convert SVG file to PNG and parse table using OCR."""
        if not os.path.exists(svg_filename):
            logger.error(f"SVG file {svg_filename} not found.")
            return {}

        logger.info(f"Processing SVG file: {svg_filename}")

        try:
            with open(svg_filename, "rb") as svg_file:
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

    def collect_fees(self):
        """Collect all fees from the page with detailed logging."""
        fees = {}

        logger.info("Searching for Standard Volume Buyer Fees section.")
        try:
            standard_volume_section = self.page.locator("h2:has-text('Standard Volume Buyer Fees')").first
            standard_volume_section.wait_for(timeout=10000)
            img_element = standard_volume_section.locator("xpath=following::img").first
            img_url = img_element.get_attribute("src")
            logger.info(f"Image tag found with src: {img_url}")
            if not img_url.startswith("http"):
                img_url = "https://www.iaai.com" + img_url
                logger.info(f"Adjusted image URL to absolute: {img_url}")
            result = self.download_image(img_url)
            if isinstance(result, str):
                standard_volume_fees = self.parse_svg_table(result)
                if standard_volume_fees:
                    fees["standard_volume_buyer_fees"] = {
                        "fees": standard_volume_fees,
                        "currency": "USD",
                        "description": "Applies to buyers with a business license, 24 or fewer units purchased, or total vehicle sales less than $75,000 in the past 12 months, OR have 5 or more bidder accounts.",
                    }
                    logger.info(f"Parsed Standard Volume Buyer Fees: {standard_volume_fees}")
                else:
                    logger.warning("No fees parsed from Standard Volume Buyer Fees image.")
            else:
                logger.warning("Standard Volume Buyer Fees image not in SVG format.")
        except Exception as e:
            logger.warning(f"Standard Volume Buyer Fees section not found: {str(e)}")

        logger.info("Searching for High Volume Buyer Fees section.")
        try:
            high_volume_section = self.page.locator("h2:has-text('High Volume Buyer Fees')").first
            high_volume_section.wait_for(timeout=10000)
            img_element = high_volume_section.locator("xpath=following::img").first
            img_url = img_element.get_attribute("src")
            logger.info(f"Image tag found with src: {img_url}")
            if not img_url.startswith("http"):
                img_url = "https://www.iaai.com" + img_url
                logger.info(f"Adjusted image URL to absolute: {img_url}")
            result = self.download_image(img_url)
            if isinstance(result, str):
                high_volume_fees = self.parse_svg_table(result)
                if high_volume_fees:
                    fees["high_volume_buyer_fees"] = {
                        "fees": high_volume_fees,
                        "currency": "USD",
                        "description": "Applies to buyers with a business license on file, 25+ units purchased AND a total sale price of $75,000+ in the past 12 months AND have fewer than 5 bidder accounts.",
                    }
                    logger.info(f"Parsed High Volume Buyer Fees: {high_volume_fees}")
                else:
                    logger.warning("No fees parsed from High Volume Buyer Fees image.")
            else:
                logger.warning("High Volume Buyer Fees image not in SVG format.")
        except Exception as e:
            logger.warning(f"High Volume Buyer Fees section not found: {str(e)}")

        logger.info("Searching for Internet Bid Fee and Proxy Bid Fee section.")
        try:
            internet_bid_section = self.page.locator("h2:has-text('Internet Bid Fee and Proxy Bid Fee')").first
            internet_bid_section.wait_for(timeout=10000)
            img_element = internet_bid_section.locator("xpath=following::img").first
            img_url = img_element.get_attribute("src")
            logger.info(f"Image tag found with src: {img_url}")
            if not img_url.startswith("http"):
                img_url = "https://www.iaai.com" + img_url
                logger.info(f"Adjusted image URL to absolute: {img_url}")
            result = self.download_image(img_url)
            if isinstance(result, str):
                live_online_fees = self.parse_svg_table(result)
                if live_online_fees:
                    fees["live_online_bid_fee"] = {
                        "fees": live_online_fees,
                        "currency": "USD",
                        "description": "Applies if vehicle is awarded due to successful internet bid (Live Online Bid Fee).",
                    }
                    logger.info(f"Parsed Live Online Bid Fee: {live_online_fees}")
                else:
                    logger.warning("No fees parsed from Internet Bid Fee image.")
            else:
                logger.warning("Internet Bid Fee image not in SVG format.")
        except Exception as e:
            logger.warning(f"Internet Bid Fee and Proxy Bid Fee section not found: {str(e)}")

        soup = BeautifulSoup(self.page.content(), "html.parser")
        logger.info("Searching for fixed fees (Service Fee, etc.).")
        fee_text = soup.find(string=re.compile("Service Fee:"))
        if fee_text:
            fees["service_fee"] = {
                "amount": 95.0,
                "currency": "USD",
                "description": "Per unit for vehicle handling, including vehicle pull out and loading",
            }
            fees["environmental_fee"] = {
                "amount": 15.0,
                "currency": "USD",
                "description": "Per unit for handling and care in accordance with environmental regulations",
            }
            fees["title_handling_fee"] = {"amount": 20.0, "currency": "USD", "description": "Applied to all purchases"}
            logger.info("Parsed fixed fees: Service Fee, Environmental Fee, Title Handling Fee")
        else:
            logger.warning("Fixed fees (Service Fee) not found in HTML.")

        return fees

    def close(self):
        """Close the browser and context."""
        if self.browser:
            self.browser.close()
        logger.info("Browser closed.")


def scrape_iaai_fees():
    """Main function to execute the scraping process."""
    url = "https://www.iaai.com/marketing/standard-iaa-licensed-buyer-fees"
    scraper = IAABuyerFeeScraper(url)
    try:
        scraper.scrape_page()
        fees_data = {
            "source": "iaai",
            "payment_method": "standard",
            "fees": scraper.collect_fees(),
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        return fees_data
    finally:
        scraper.close()
