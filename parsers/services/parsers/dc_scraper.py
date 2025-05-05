# dealer_center_scraper.py
import email
import imaplib
import logging
import os
import re
import time
import json
from email.header import decode_header
from typing import List, Optional, Tuple
import base64
from PIL import Image
import io

from dotenv import load_dotenv
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class GmailClient:
    def __init__(self):
        load_dotenv()
        self.email = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASSWORD")
        self.imap_server = "imap.gmail.com"

    def get_verification_code(self, max_wait: int = 30, poll_interval: int = 2) -> Optional[str]:
        """Poll the inbox for the verification code with a maximum wait time."""
        start_time = time.time()
        while time.time() - start_time < max_wait:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            try:
                mail.login(self.email, self.password)
                mail.select("inbox")
                status, messages = mail.search(None, '(FROM "do-not-reply@dealercenter.net")')
                email_ids = messages[0].split()
                if not email_ids:
                    logging.info("No emails found from DealerCenter, retrying...")
                    mail.logout()
                    time.sleep(poll_interval)
                    continue
                latest_email_id = email_ids[-1]
                status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject, encoding = decode_header(msg["Subject"])[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding or "utf-8")
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode()
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode()
                        match = re.search(r"\b\d{6}\b", body)
                        if match:
                            mail.logout()
                            return match.group()
                mail.logout()
            except Exception as e:
                logging.error(f"Error checking email: {str(e)}")
                mail.logout()
            time.sleep(poll_interval)
        logging.error("Failed to retrieve verification code within the timeout period.")
        return None


class OptionSelector:
    @staticmethod
    def select_best_match(options: List[str], reference: str) -> Optional[str]:
        """
        Select the best matching option from a list based on the reference string.

        Args:
            options (List[str]): List of options to choose from (e.g., ["SLT Quad Cab", "Sport Quad Cab"]).
            reference (str): Reference string to compare against (e.g., vehicle_name or engine).

        Returns:
            str: The best matching option, or None if no options are provided.
        """
        if not options:
            return None

        def to_upper_set(s: str) -> set:
            """Convert a string to a set of uppercase characters, ignoring spaces and special characters."""
            return set(re.sub(r"[^a-zA-Z0-9]", "", s).upper())

        reference_set = to_upper_set(reference)
        differences = []

        for option in options:
            option_set = to_upper_set(option)
            diff = len(option_set.symmetric_difference(reference_set))
            differences.append((diff, option))

        differences.sort(key=lambda x: x[0])
        return differences[0][1]


class DealerCenterScraper:
    _use_proxy = None
    _cookies = None

    def __init__(self, vin: str, vehicle_name: str = None, engine: str = None, gmail_client: GmailClient = None):
        self.vin = vin
        self.vehicle_name = vehicle_name
        self.engine = engine
        self.gmail_client = gmail_client or GmailClient()
        self.proxy_host = os.getenv("PROXY_HOST")
        self.proxy_port = os.getenv("PROXY_PORT")
        self.driver = None
        self.driver_closed = False
        self.use_proxy = self._determine_proxy_usage()
        self.driver = self._init_driver()
        self.wait = WebDriverWait(self.driver, 60)

    def _setup_chrome_options(self, use_proxy: bool = False) -> Options:
        """Set up Chrome options with or without proxy based on the use_proxy flag."""
        chrome_options = Options()
        if use_proxy:
            chrome_options.add_argument(f"--proxy-server=socks5://{self.proxy_host}:{self.proxy_port}")
            logging.info("Using proxy for Chrome driver.")
        else:
            logging.info("Attempting connection without proxy.")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--allow-insecure-localhost")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-breakpad")
        chrome_options.binary_location = "/usr/bin/google-chrome"
        return chrome_options

    def _test_connection(self, driver, url: str = "https://app.dealercenter.net") -> bool:
        """Test if the connection to the target URL is successful by checking for the presence of the body tag."""
        try:
            driver.get(url)
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            logging.info(f"Successfully connected to {url}.")
            return True
        except TimeoutException:
            logging.warning(f"Failed to connect to {url}.")
            return False
        except Exception as e:
            logging.error(f"Error while testing connection to {url}: {str(e)}")
            return False

    def _determine_proxy_usage(self) -> bool:
        """Determine whether to use a proxy based on previous runs or by testing the connection."""
        if DealerCenterScraper._use_proxy is not None:
            logging.info(f"Using saved proxy setting: use_proxy={DealerCenterScraper._use_proxy}")
            return DealerCenterScraper._use_proxy

        chrome_options = self._setup_chrome_options(use_proxy=False)
        service = Service()
        driver = uc.Chrome(service=service, options=chrome_options)
        logging.info("Testing connection without proxy.")

        if self._test_connection(driver):
            logging.info("Connection without proxy successful. Saving decision.")
            driver.quit()
            DealerCenterScraper._use_proxy = False
            return False

        logging.info("Connection without proxy failed. Testing with proxy...")
        driver.quit()

        chrome_options = self._setup_chrome_options(use_proxy=True)
        driver = uc.Chrome(service=service, options=chrome_options)
        logging.info("Testing connection with proxy.")

        if self._test_connection(driver):
            logging.info("Connection with proxy successful. Saving decision.")
            driver.quit()
            DealerCenterScraper._use_proxy = True
            return True

        driver.quit()
        logging.error("Failed to connect with or without proxy. Aborting.")
        raise Exception("Unable to establish connection with or without proxy.")

    def _init_driver(self):
        """Initialize the Chrome driver with the determined proxy setting."""
        chrome_options = self._setup_chrome_options(use_proxy=self.use_proxy)
        service = Service()
        driver = uc.Chrome(service=service, options=chrome_options)
        logging.info("Chrome driver initialized with determined proxy setting.")
        return driver

    def _save_cookies(self) -> None:
        """Save current session cookies to a class variable."""
        DealerCenterScraper._cookies = self.driver.get_cookies()
        logging.info("Cookies saved to class variable.")

    def _load_cookies(self) -> bool:
        """Load session cookies from a class variable if they exist and validate them."""
        self.driver.get("https://app.dealercenter.net/apps/shell/reports/home")
        if DealerCenterScraper._cookies is None:
            logging.info("No cookies available in class variable.")
            return False

        try:
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            for cookie in DealerCenterScraper._cookies:
                if "domain" in cookie and "dealercenter.net" in cookie["domain"]:
                    try:
                        self.driver.add_cookie(cookie)
                    except Exception as e:
                        logging.error(f"Failed to add cookie {cookie.get('name')}: {str(e)}")
                        return False
            logging.info("Cookies loaded from class variable.")
            return True
        except Exception as e:
            logging.error(f"Error loading cookies: {str(e)}")
            return False

    def _clear_cookies(self) -> None:
        """Clear the cookies stored in the class variable."""
        DealerCenterScraper._cookies = None
        logging.info("Cookies cleared from class variable.")

    def _wait_for_loader_to_disappear(self, action_description: str) -> None:
        """
        Wait for the loader to disappear and log the result.

        Args:
            action_description (str): Description of the action being performed (e.g., "after clicking Inventory").

        Returns:
            None
        """
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info(f"Loader disappeared {action_description}.")
        except TimeoutException:
            logging.warning(f"Loader did not disappear {action_description}, proceeding anyway.")

    def check_session(self) -> bool:
        """Check if the current session is active."""
        try:
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'Inventory')]")))
            logging.info("Session is active.")
            return True
        except TimeoutException:
            logging.info("Session is not active.")
            return False

    def login(self) -> None:
        """Perform login only if the session is not active."""
        cookies_loaded = self._load_cookies()
        if cookies_loaded and self.check_session():
            logging.info("Login not required, session is active.")
            return

        logging.info("Cookies are invalid or session is not active. Clearing cookies and performing login...")
        self._clear_cookies()

        dc_username = os.getenv("DC_USERNAME")
        dc_password = os.getenv("DC_PASSWORD")
        logging.info(f"DC_USERNAME: {dc_username}, DC_PASSWORD: {dc_password}")

        if not dc_username or not dc_password:
            logging.error("DC_USERNAME or DC_PASSWORD is not set in .env file")
            raise ValueError("DC_USERNAME or DC_PASSWORD is not set in .env file")

        try:
            username_field = self.wait.until(EC.presence_of_element_located((By.ID, "username")))
            password_field = self.driver.find_element(By.ID, "password")
            login_button = self.driver.find_element(By.ID, "login")
            logging.info("Login form elements found")
        except Exception as e:
            logging.error(f"Failed to find login form elements: {str(e)}")
            raise

        username_field.send_keys(dc_username)
        password_field.send_keys(dc_password)
        login_button.click()
        logging.info("Clicked login button")

        try:
            self._click_if_exists(
                "//span[contains(text(), 'Email Verification Code')]/parent::a", fallback_id="WebMFAEmail"
            )
            logging.info("Clicked Email Verification Code link")
        except Exception as e:
            logging.error(f"Failed to click Email Verification Code link: {str(e)}")
            raise

        time.sleep(5)
        verification_code = self.gmail_client.get_verification_code(max_wait=30, poll_interval=2)
        if not verification_code:
            logging.error("Failed to retrieve verification code.")
            raise Exception("Failed to retrieve verification code.")

        try:
            verification_code_field = self.wait.until(EC.presence_of_element_located((By.ID, "email-passcode-input")))
            submit_button = self.driver.find_element(By.ID, "email-passcode-submit")
            logging.info("Verification code input field found")
        except Exception as e:
            logging.error(f"Failed to find verification code input field: {str(e)}")
            logging.info(self.driver.page_source)
            raise

        try:
            verification_code_field.send_keys(verification_code)
            submit_button.click()
            logging.info("Submitted verification code")
        except Exception as e:
            logging.error(f"Error during email verification: {str(e)}")
            raise

    def _click_if_exists(self, xpath: str, fallback_id: Optional[str] = None) -> None:
        """Click an element if it exists, with an optional fallback ID."""
        try:
            button = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            button.click()
        except Exception:
            if fallback_id:
                try:
                    self.driver.find_element(By.ID, fallback_id).click()
                except Exception:
                    pass

    def run_history_report(self):
        """Run a vehicle history report, extract owners, odometer, and accidents data, and capture a full-page screenshot."""
        # Save cookies after successful login
        self._save_cookies()
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared, proceeding with clicking Inventory.")
        except TimeoutException:
            logging.warning("Loader did not disappear within timeout, proceeding anyway.")
        
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Inventory')]"))).click()
        # Wait for loader to disappear after clicking Inventory
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after clicking Inventory.")
        except TimeoutException:
            logging.warning("Loader did not disappear after clicking Inventory, proceeding anyway.")

        self._click_if_exists("//button[.//span[contains(text(), 'Run History Report')]]")
        # Wait for loader to disappear after clicking Run History Report
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after clicking Run History Report.")
        except TimeoutException:
            logging.warning("Loader did not disappear after clicking Run History Report, proceeding anyway.")

        # Added 2-second delay before searching for the VIN input field
        time.sleep(5)
        elements = self.wait.until(
            EC.presence_of_all_elements_located((By.XPATH, "//input[contains(@class, 'k-input-inner')]"))
        )
        elements[-1].send_keys(self.vin)
        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[.//span[contains(text(), 'Run')]]]"))
        ).click()
        # Wait for loader to disappear after clicking Run
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after clicking Run.")
        except TimeoutException:
            logging.warning("Loader did not disappear after clicking Run, proceeding anyway.")

        iframe = self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "autocheck-content")))
        self.driver.switch_to.frame(iframe)
        # Wait for loader to disappear after switching to iframe
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after switching to iframe.")
        except TimeoutException:
            logging.warning("Loader did not disappear after switching to iframe, proceeding anyway.")

        # Збираємо дані
        owners_value = None
        try:
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'box-title-owners')]")))
            try:
                owners_element = self.driver.find_element(By.XPATH, "//span[@class='box-title-owners']/span")
                owners_value = int(owners_element.text)
            except:
                owners_value = 1
        except:
            pass

        try:
            odometer_value = self.driver.find_element(
                By.XPATH, "//p[contains(., 'Last reported odometer:')]/span[@class='font-weight-bold'][1]"
            ).text.replace(",", "")
        except:
            odometer_value = None

        accidents_value = len(self.driver.find_elements(By.XPATH, "//table[@class='table table-striped']/tbody/tr"))

        # Створюємо скріншот усієї сторінки
        total_height = self.driver.execute_script("return document.body.scrollHeight")
        viewport_height = self.driver.execute_script("return window.innerHeight")
        scroll_step = viewport_height

        # Збираємо скріншоти по частинах
        screenshots = []
        for i in range(0, total_height, scroll_step):
            self.driver.execute_script(f"window.scrollTo(0, {i});")
            time.sleep(0.5)  # Даємо час на рендеринг
            screenshot = self.driver.get_screenshot_as_png()
            screenshots.append(screenshot)

        # Склеюємо скріншоти
        images = [Image.open(io.BytesIO(screenshot)) for screenshot in screenshots]
        total_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)
        stitched_image = Image.new('RGB', (total_width, total_height))
        y_offset = 0
        for img in images:
            stitched_image.paste(img, (0, y_offset))
            y_offset += img.height

        # Конвертуємо склеєне зображення у байти
        screenshot_buffer = io.BytesIO()
        stitched_image.save(screenshot_buffer, format='PNG')
        full_screenshot = screenshot_buffer.getvalue()

        self.driver.switch_to.default_content()
        # Wait for loader to disappear after switching back to default content
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after switching back to default content.")
        except TimeoutException:
            logging.warning("Loader did not disappear after switching back to default content, proceeding anyway.")

        # Формуємо результат
        result = {
            "owners": owners_value,
            "odometer": odometer_value,
            "accidents": accidents_value
        }

        return result, full_screenshot

    def get_market_data(self, odometer_value: Optional[str]) -> Tuple[
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
    ]:
        """Retrieve market data including retail value, market price, year, make, model, drivetrain, fuel type, and body style for the vehicle."""
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Inventory')]"))).click()
        self._wait_for_loader_to_disappear("after clicking Inventory")

        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), 'Appraise New Vehicle')]"))
        ).click()
        self._wait_for_loader_to_disappear("after clicking Appraise New Vehicle")

        vin_input = self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-textbox[formcontrolname='vin'] input"))
        )
        vin_input.send_keys(self.vin)

        odometer_input = self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-numerictextbox[formcontrolname='odometer'] input"))
        )
        odometer_input.click()

        max_attempts = 5
        attempts = 0

        while attempts < max_attempts:
            try:
                self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'car-wrap')]")))
                option_elements = self.driver.find_elements(
                    By.XPATH, "//div[contains(@class, 'car-wrap')]//span[contains(@class, 'ng-star-inserted')]"
                )
                options = [elem.text.strip() for elem in option_elements if elem.text.strip()]

                if not options:
                    logging.info("No options found, proceeding with 'Next' button.")
                    break

                logging.info(f"Found options: {options}")

                reference = self.vehicle_name if self.vehicle_name else ""
                if self.engine:
                    reference += f" {self.engine}"

                best_option = OptionSelector.select_best_match(options, reference)
                logging.info(f"Selected best option: {best_option}")

                best_option_element = self.driver.find_element(
                    By.XPATH,
                    f"//div[contains(@class, 'car-wrap')]//span[contains(@class, 'ng-star-inserted') and contains(text(), '{best_option}')]",
                )
                best_option_element.click()

                self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Next')]"))).click()

                time.sleep(2)
                attempts += 1

            except TimeoutException:
                logging.info("No more option selection windows found, proceeding.")
                break

        odometer_input = self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-numerictextbox[formcontrolname='odometer'] input"))
        )
        if odometer_value:
            odometer_input.send_keys(str(odometer_value))
        else:
            odometer_input.send_keys("100000")

        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))
        ).click()
        time.sleep(3)
        self._wait_for_loader_to_disappear("after first Books click")

        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))
        ).click()
        self._wait_for_loader_to_disappear("after second Books click")

        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'J.D. Power')]")
            )
        ).click()
        self._wait_for_loader_to_disappear("after clicking J.D. Power")

        retail_value = self.wait.until(
            EC.presence_of_element_located((By.XPATH, "//kendo-numerictextbox[@formcontrolname='RetailBook']//input"))
        ).get_attribute("aria-valuenow")
        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Market Data')]")
            )
        ).click()
        self._wait_for_loader_to_disappear("after clicking Market Data")

        time.sleep(0.5)

        short_wait = WebDriverWait(self.driver, 10)

        try:
            price_tooltip_element = short_wait.until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//span[contains(@class, 'max-digital__risk-slider-bar-tooltip')]//span[contains(@class, 'font-weight-bold')]",
                    )
                )
            )
            price_value = price_tooltip_element.text.strip().replace("$", "").replace(",", "")
        except (TimeoutException, NoSuchElementException):
            price_value = None

        short_wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//dc-ui-shared-ui-shared-multiselect[contains(@formcontrolname, 'year') or contains(@formcontrolname, 'make') or contains(@formcontrolname, 'model') or contains(@formcontrolname, 'driveTrain') or contains(@formcontrolname, 'fuel') or contains(@formcontrolname, 'bodyStyle')]",
                )
            )
        )

        year_value = None
        make_value = None
        model_value = None
        drivetrain_value = None
        fuel_value = None
        body_style_value = None

        try:
            year_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='year']//div[starts-with(@id, 'tag-')]",
            )
            year_value = year_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        try:
            make_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='make']//div[starts-with(@id, 'tag-')]",
            )
            make_value = make_element.text.strip()
        except NoSuchElementException:
            pass

        try:
            model_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='model']//div[starts-with(@id, 'tag-')]",
            )
            model_value = model_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        try:
            drivetrain_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='driveTrain']//div[starts-with(@id, 'tag-')]",
            )
            drivetrain_value = drivetrain_element.text.strip()
        except NoSuchElementException:
            pass

        try:
            fuel_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='fuel']//div[starts-with(@id, 'tag-')]",
            )
            fuel_value = fuel_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        try:
            body_style_element = self.driver.find_element(
                By.XPATH,
                "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='bodyStyle']//div[starts-with(@id, 'tag-')]",
            )
            body_style_value = body_style_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        return (
            retail_value,
            price_value,
            year_value,
            make_value,
            model_value,
            drivetrain_value,
            fuel_value,
            body_style_value,
        )

    def close(self) -> None:
        """Close the browser driver safely."""
        if not self.driver_closed:
            try:
                for handle in self.driver.window_handles:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
                time.sleep(1)
                self.driver.quit()
                logging.info("Driver closed successfully.")
            except Exception as e:
                logging.error(f"Error closing driver: {str(e)}")
            finally:
                self.driver.service = None
                self.driver.session_id = None
                self.driver_closed = True

def scrape(self):
    """Run the full scraping process and return the results with a full-page screenshot as multipart/form-data compatible data."""
    self.login()
    
    # Отримуємо дані та скріншот із run_history_report
    history_data, screenshot = self.run_history_report()
    owners, odometer, accidents = history_data["owners"], history_data["odometer"], history_data["accidents"]
    
    # Отримуємо ринкові дані
    retail, price, year, make, model, drivetrain, fuel, body_style = self.get_market_data(odometer)
    
    # Перетворюємо odometer та year у числа
    try:
        odometer = int(odometer.replace(",", ""))
    except AttributeError:
        logging.error(f"Invalid odometer value: {odometer}. Defaulting to 0.")
        odometer = 0
    try:
        year = int(year.replace(",", ""))
    except AttributeError:
        logging.error(f"Invalid year value: {year}. Defaulting to 0.")
        year = 0

    # Формуємо JSON-частину даних
    result = {
        "owners": owners,
        "vehicle": f"{year} {make} {model}",
        "mileage": odometer,
        "accident_count": accidents,
        "retail": retail,
        "price": price,
        "year": year,
        "make": make,
        "model": model,
        "drivetrain": drivetrain,
        "fuel": fuel,
        "body_style": body_style,
    }

    # Повертаємо словник із двома частинами, готовими для multipart/form-data
    return {
        "data": json.dumps(result),  # JSON-рядок із даними
        "screenshot": screenshot     # Бінарні дані скріншота
    }
