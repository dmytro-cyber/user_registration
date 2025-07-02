import json
import os
import re
import time
import email
import imaplib
import base64
from typing import List, Optional
from io import BytesIO
from PIL import Image
from email.header import decode_header
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()


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


class GmailClient:
    def __init__(self):
        load_dotenv()
        self.email = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASSWORD")
        self.imap_server = "imap.gmail.com"

    def get_verification_code(self, max_wait=30, poll_interval=4):
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


class DealerCenterScraper:
    def __init__(
        self,
        vin,
        vehicle_name: str = None,
        engine: str = None,
    ):
        self.vin = vin
        self.vehicle_name = vehicle_name
        self.engine = engine
        # self.proxy_host = os.getenv("PROXY_HOST")
        # self.proxy_port = os.getenv("PROXY_PORT")
        self.cookies_file = "cookies.json"
        self.driver = self._init_driver()
        self.wait = WebDriverWait(self.driver, 15)  # Reduced default timeout to 10 seconds
        self.driver_closed = False  # Flag to track if the driver has been closed

    def _init_driver(self):
        """Initialize the Chrome driver with specified options."""
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-insecure-localhost")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-breakpad")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-infobars")

        chrome_options.binary_location = "/usr/bin/google-chrome"

        service = Service()
        try:
            driver = uc.Chrome(service=service, options=chrome_options)
            logging.info("Chrome driver initialized.")
            return driver
        except Exception as e:
            logging.error(f"Failed to initialize Chrome driver: {str(e)}")
            raise

    def _save_cookies(self):
        """Save current session cookies to a file."""
        cookies = self.driver.get_cookies()
        with open(self.cookies_file, "w") as f:
            json.dump(cookies, f)
        logging.info("Cookies saved to file.")

    def _load_cookies(self):
        """Load session cookies from a file if it exists and validate them."""
        self.driver.get("https://app.dealercenter.net/apps/shell/reports/home")
        if not os.path.exists(self.cookies_file):
            logging.info("Cookies file does not exist.")
            return False

        try:
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            with open(self.cookies_file, "r") as f:
                cookies = json.load(f)
                for cookie in cookies:
                    if "domain" in cookie and "dealercenter.net" in cookie["domain"]:
                        try:
                            self.driver.add_cookie(cookie)
                        except Exception as e:
                            logging.error(f"Failed to add cookie {cookie.get('name')}: {str(e)}")
                            return False
            logging.info("Cookies loaded from file.")
            return True
        except Exception as e:
            logging.error(f"Error loading cookies: {str(e)}")
            return False

    def _clear_cookies_file(self):
        """Delete the cookies file if it exists."""
        if os.path.exists(self.cookies_file):
            os.remove(self.cookies_file)
            logging.info("Cookies file deleted.")

    def check_session(self):
        """Check if the current session is active."""
        try:
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'Inventory')]")))
            logging.info("Session is active.")
            return True
        except TimeoutException:
            logging.info("Session is not active.")
            return False

    def login(self):
        """Perform login only if the session is not active."""
        # Try to load cookies and check the session
        cookies_loaded = self._load_cookies()
        if cookies_loaded and self.check_session():
            logging.info("Login not required, session is active.")
            return

        # If cookies are invalid or loading failed, delete the file and perform login
        logging.info("Cookies are invalid or session is not active. Clearing cookies and performing login...")
        self._clear_cookies_file()

        # Логуємо значення змінних
        dc_username = os.getenv("DC_USERNAME")
        dc_password = os.getenv("DC_PASSWORD")
        logging.info(f"DC_USERNAME: {dc_username}")
        logging.info(f"DC_PASSWORD: {dc_password}")

        # Перевірка, чи змінні не порожні
        if not dc_username or not dc_password:
            logging.error("DC_USERNAME or DC_PASSWORD is not set in .env file")
            raise ValueError("DC_USERNAME or DC_PASSWORD is not set in .env file")

        # Очікуємо поле для логіну
        try:
            username_field = self.wait.until(EC.presence_of_element_located((By.ID, "username")))
            password_field = self.driver.find_element(By.ID, "password")
            login_button = self.driver.find_element(By.ID, "login")
            logging.info("Login form elements found")
        except Exception as e:
            logging.error(f"Failed to find login form elements: {str(e)}")
            raise

        # Вводимо логін і пароль
        username_field.send_keys(dc_username)
        password_field.send_keys(dc_password)
        login_button.click()
        logging.info("Clicked login button")

        # Очікуємо появу кнопки для запиту коду верифікації
        try:
            self._click_if_exists(
                "//span[contains(text(), 'Email Verification Code')]/parent::a", fallback_id="WebMFAEmail"
            )
            logging.info("Clicked Email Verification Code link")
        except Exception as e:
            logging.error(f"Failed to click Email Verification Code link: {str(e)}")
            raise

        # Added 5-second delay before fetching the verification code
        time.sleep(5)
        gmail = GmailClient()
        verification_code = gmail.get_verification_code(max_wait=30, poll_interval=4)
        if not verification_code:
            logging.error("Failed to retrieve verification code.")
            raise Exception("Failed to retrieve verification code.")

        # Очікуємо поле для введення коду верифікації
        try:
            verification_code_field = self.wait.until(EC.presence_of_element_located((By.ID, "email-passcode-input")))
            submit_button = self.driver.find_element(By.ID, "email-passcode-submit")
            logging.info("Verification code input field found")
        except Exception as e:
            logging.error(f"Failed to find verification code input field: {str(e)}")
            raise

        # Вводимо код верифікації
        verification_code_field.send_keys(verification_code)
        submit_button.click()
        logging.info("Submitted verification code")

        # Зберігаємо куки
        self._save_cookies()

    def _click_if_exists(self, xpath, fallback_id=None):
        """Click an element if it exists, with an optional fallback ID."""
        try:
            button = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            button.click()
        except:
            if fallback_id:
                try:
                    self.driver.find_element(By.ID, fallback_id).click()
                except:
                    pass

    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By

    def run_history_report(self):
        """Run a vehicle history report and extract owners, odometer, and accidents data."""
        time.sleep(4)
        drawer_element = None
        max_retries = 3

        for attempt in range(max_retries):
            try:
                drawer_element = self.wait.until(EC.presence_of_element_located((By.XPATH, "//kendo-drawer")))
                logging.info(f"Drawer element found on attempt {attempt + 1}")
                break
            except TimeoutException:
                logging.warning(f"Attempt {attempt + 1}/{max_retries} failed to find drawer element. Reloading page...")
                self.driver.refresh()
                time.sleep(2)

        if drawer_element is None:
            logging.error("Drawer element not found after retries.")
            raise RuntimeError("Drawer element not found after retries.")

        try:
            ActionChains(self.driver).move_to_element(drawer_element).perform()
            time.sleep(1)

            inventory_element = self.wait.until(
                EC.visibility_of_element_located((By.XPATH, "//li[@aria-label='Inventory']"))
            )
            ActionChains(self.driver).move_to_element(inventory_element).perform()
            time.sleep(0.5)
            inventory_element.click()
        except Exception as e:
            logging.error(f"Failed to interact with Inventory menu: {str(e)}")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logging.info("Page source saved to page_source.html")
            raise

        self._click_if_exists("//button[.//span[contains(text(), 'Run History Report')]]")
        time.sleep(4)

        self.wait.until(
            EC.presence_of_all_elements_located((By.XPATH, "//input[contains(@class, 'k-input-inner')]"))
        )[-1].send_keys(self.vin)

        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[.//span[contains(text(), 'Run')]]]"))
        ).click()

        iframe = self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "autocheck-content")))
        iframe_position = self.driver.execute_script("""
            var iframe = arguments[0];
            var rect = iframe.getBoundingClientRect();
            return {
                top: rect.top,
                left: rect.left,
                width: rect.width,
                height: rect.height
            };
        """, iframe)

        self.driver.switch_to.frame(iframe)

        try:
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'box-title-owners')]")))
        except TimeoutException:
            logging.warning("Timeout waiting for iframe content to load.")

        # Парсимо власників
        try:
            owners_element = self.driver.find_element(By.XPATH, "//span[@class='box-title-owners']/span")
            owners_value = int(owners_element.text) or 1
        except Exception as e:
            logging.warning(f"Failed to parse owners element: {str(e)}")
            try:
                image_element = self.driver.find_element(By.XPATH, "//img[@src='https://www.autocheck.com/reportservice/report/fullReport/img/owner-icon-1.svg']")
                if image_element.is_displayed():
                    owners_value = 1
                    logging.info("Found owner-icon-1.svg, setting owners_value to 1.")
                else:
                    owners_value = 1
                    logging.warning("owner-icon-1.svg found but not displayed, setting owners_value to 1.")
            except NoSuchElementException:
                owners_value = 0
                logging.warning("owner-icon-1.svg not found, setting owners_value to 1 by default.")

        # Парсимо одометр
        odometer_text = self.driver.find_element(
            By.XPATH, "//p[contains(., 'Last reported odometer:')]/span[@class='font-weight-bold'][1]"
        ).text.replace(",", "")
        odometer_value = int(odometer_text)

        # Парсимо кількість аварій
        accidents_value = len(self.driver.find_elements(By.XPATH, "//table[@class='table table-striped']/tbody/tr")) or 0

        # Скриншот
        screenshot_base64 = None
        try:
            total_height = self.driver.execute_script("""
                return Math.max(
                    document.body.scrollHeight,
                    document.body.offsetHeight,
                    document.documentElement.clientHeight,
                    document.documentElement.scrollHeight,
                    document.documentElement.offsetHeight
                );
            """)
            viewport_height = self.driver.execute_script("return window.innerHeight")
            viewport_width = self.driver.execute_script("return window.innerWidth")

            if total_height == 0 or viewport_height == 0 or viewport_width == 0:
                raise ValueError("Invalid dimensions for screenshot.")

            iframe_width = iframe_position["width"]
            iframe_height = iframe_position["height"]
            iframe_left = iframe_position["left"]
            iframe_top = iframe_position["top"]

            screenshots = []
            scroll_position = 0
            first_loop = True

            while scroll_position < total_height:
                self.driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                if first_loop:
                    time.sleep(1.5)
                    first_loop = False

                screenshot = self.driver.get_screenshot_as_png()
                screenshot_img = Image.open(BytesIO(screenshot))
                overlap = 220
                next_position = scroll_position + viewport_height - overlap

                crop_box = (
                    max(0, iframe_left),
                    max(0, iframe_top),
                    min(viewport_width, iframe_left + iframe_width),
                    min(viewport_height, iframe_top + iframe_height),
                )
                cropped_img = screenshot_img.crop(crop_box)
                screenshots.append(cropped_img)

                if next_position >= total_height:
                    break
                scroll_position = next_position

            if screenshots:
                full_screenshot = Image.new("RGB", (int(iframe_width), total_height))
                offset = 0
                for screenshot_img in screenshots:
                    if screenshot_img.width != iframe_width:
                        screenshot_img = screenshot_img.resize((int(iframe_width), screenshot_img.height), Image.Resampling.LANCZOS)
                    full_screenshot.paste(screenshot_img, (0, offset))
                    offset += screenshot_img.height

                width, height = full_screenshot.size
                crop_box = (0, 100, width, height)
                full_screenshot = full_screenshot.crop(crop_box)

                buffered = BytesIO()
                full_screenshot.save(buffered, format="PNG")
                screenshot_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                logging.info("Full iframe screenshot captured and cropped.")
            else:
                raise ValueError("No screenshots captured.")
        except Exception as e:
            logging.error(f"Screenshot capture failed: {str(e)}")

        self.driver.switch_to.default_content()
        return owners_value, odometer_value, accidents_value, screenshot_base64

    def get_market_data(self, odometer_value):
        """Retrieve market data including retail value, market price, year, make, model, drivetrain, fuel type, and body style for the vehicle."""
        try:
            # Очікуємо наявності батьківського елемента меню
            drawer_element = self.wait.until(EC.presence_of_element_located((By.XPATH, "//kendo-drawer")))
            # Наводимо мишу на батьківський елемент для розгортання меню
            ActionChains(self.driver).move_to_element(drawer_element).perform()
            time.sleep(1)  # Затримка для розгортання меню

            # Очікуємо, поки елемент "Inventory" стане видимим
            inventory_element = self.wait.until(
                EC.visibility_of_element_located((By.XPATH, "//li[@aria-label='Inventory']"))
            )
            # Наводимо мишу на елемент "Inventory"
            ActionChains(self.driver).move_to_element(inventory_element).perform()
            time.sleep(0.5)  # Коротка затримка для стабільності

            # Клікаємо на елемент "Inventory"
            inventory_element.click()
        except Exception as e:
            logging.error(f"Failed to interact with Inventory menu: {str(e)}")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logging.info("Page source saved to page_source.html")
            raise

        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), 'Appraise New Vehicle')]]"))
        ).click()
        time.sleep(5)
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
                option_elements = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'car-wrap')]//h6")
                options = [elem.text.strip() for elem in option_elements if elem.text.strip()]
                options.append("automatic")

                if not options:
                    logging.info("No options found, proceeding with 'Next' button.")
                    break

                logging.info(f"Found options: {options}")

                reference = self.vehicle_name if self.vehicle_name else ""
                if self.engine:
                    reference += f" {self.engine}"

                best_option = OptionSelector.select_best_match(options, reference)
                logging.info(f"Selected best option: {best_option}")

                try:
                    best_option_element = self.driver.find_element(
                        By.XPATH,
                        f"//div[contains(@class, 'car-wrap')]//h6[contains(text(), '{best_option}')]"
                    )
                except NoSuchElementException:
                    logging.warning(f"Best match '{best_option}' not found, selecting the first available option.")
                    best_option_element = option_elements[0]

                best_option_element.click()

                self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Next')]"))).click()
                attempts += 1

            except TimeoutException:
                logging.info("No more option selection windows found, proceeding.")
                break

        # Додатковий етап: обробка чекбокса
        try:
            # Очікуємо наявності чекбокса
            checkbox_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//input[@type='checkbox' and contains(@class, 'k-checkbox')]"))
            )
            # Перевіряємо, чи чекбокс видимий і клікабельний
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable(checkbox_element))
            # Клікаємо по чекбоксу, якщо він ще не позначений
            if not checkbox_element.is_selected():
                checkbox_element.click()
                logging.info("Checkbox selected.")
            else:
                logging.info("Checkbox is already selected.")

            # Очікуємо і натискаємо кнопку "Next" після чекбокса
            next_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Next')]"))
            )
            next_button.click()
            logging.info("Clicked 'Next' after checkbox.")
            time.sleep(2)  # Затримка для завантаження наступної сторінки

        except TimeoutException:
            logging.info("No checkbox or 'Next' button found after car options, proceeding.")
        except Exception as e:
            logging.error(f"Failed to handle checkbox or 'Next' button: {str(e)}")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logging.info("Page source saved to page_source.html")

        time.sleep(2)
        odometer_input = self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-numerictextbox[formcontrolname='odometer'] input"))
        )
        odometer_input.send_keys(str(odometer_value))
        odometer_input.click()
        time.sleep(5)

        # Дочекатися зникнення оверлея перед кліком на "Books"
        WebDriverWait(self.driver, 10).until_not(EC.presence_of_element_located((By.CLASS_NAME, "k-overlay")))
        try:
            self.wait.until(EC.invisibility_of_element_located((By.TAG_NAME, "dc-ui-shared-loader")))
            logging.info("Loader disappeared after switching to iframe.")
        except TimeoutException:
            logging.warning("Loader did not disappear after switching to iframe, proceeding anyway.")

        # self.wait.until(
        #     EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))
        # ).click()
        # time.sleep(3)
        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))
        ).click()

        # Дочекатися зникнення оверлея перед кліком на "J.D. Power"
        WebDriverWait(self.driver, 10).until_not(EC.presence_of_element_located((By.CLASS_NAME, "k-overlay")))

        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'J.D. Power')]")
            )
        ).click()
        time.sleep(1)
        try:
            self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'J.D. Power')]")
                )
            ).click()
        except:
            logging.info("No J.D. Power option selected, proceeding.")
        logging.info("Clicked 'J.D. Power' button.")
        retail_value = self.wait.until(
            EC.presence_of_element_located((By.XPATH, "//kendo-numerictextbox[@formcontrolname='RetailBook']//input"))
        ).get_attribute("aria-valuenow")

        try:
            WebDriverWait(self.driver, 10).until_not(EC.presence_of_element_located((By.CLASS_NAME, "k-overlay")))
        except:
            logging.info("k-overlay elements not found, proceeding.")

        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Manheim')]")
            )
        ).click()
        time.sleep(1)
        try:
            logging.info("Clicked 'Manheim' button.")
            time.sleep(1)
            manheim = (
                self.wait.until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            "//div[contains(@class, 'center')]//label[contains(text(), 'Based on Advertised Retail Price')]/following-sibling::label[contains(@class, 'fw-bold') and contains(@class, 'fs-px-18')]",
                        )
                    )
                )
                .text.strip()
                .replace("$", "")
                .replace(",", "")
            ) or None
        except:
            manheim = None

        # # Дочекатися зникнення оверлея перед кліком на "Market Data"
        # WebDriverWait(self.driver, 10).until_not(EC.presence_of_element_located((By.CLASS_NAME, "k-overlay")))

        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Market Data')]")
            )
        ).click()
        logging.info("Clicked 'Market Data' button.")
        time.sleep(0.5)

        short_wait = WebDriverWait(self.driver, 5)

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
            year_value = int(year_element.text.strip().split()[0])
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
            manheim,
            retail_value,
            price_value,
            year_value,
            make_value,
            model_value,
            drivetrain_value,
            fuel_value,
            body_style_value,
        )

    def close(self):
        """Close the browser driver safely."""
        if not self.driver_closed:
            try:
                # Close all browser windows
                for handle in self.driver.window_handles:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
                # Small delay to ensure browser processes terminate
                time.sleep(1)
                self.driver.quit()
                logging.info("Driver closed successfully.")
            except Exception as e:
                logging.error(f"Error closing driver: {str(e)}")
            finally:
                # Prevent __del__ from calling quit again
                self.driver.service = None
                self.driver.session_id = None
                self.driver_closed = True

    def scrape(self):
        """Run the full scraping process and return the results."""
        try:
            self.login()
            owners, odometer, accidents, screenshot_base64 = self.run_history_report()
            manheim, retail, price, year, make, model, drivetrain, fuel, body_style = self.get_market_data(odometer)
            self.close()
            results = {
                "owners": owners,
                "vehicle": f"{year} {make} {model}",
                "mileage": int(odometer),
                "accident_count": accidents,
                "retail": retail,
                "manheim": manheim,
                "price": price,
                "year": int(year),
                "make": make,
                "model": model,
                "drivetrain": drivetrain,
                "fuel": fuel,
                "body_style": body_style,
                "screenshot": screenshot_base64,
            }
            for k, v in results.items():
                if len(str(v)) < 100:
                    logging.info(f"{k}: {v}")
            return results
        finally:
            try:
                self.close()
            except:
                pass

    def scrape_only_history(self):
        """Run only the history report scraping process."""
        try:
            self.login()
            owners, odometer, accidents, screenshot_base64 = self.run_history_report()
            self.close()
            results = {
                "owners": owners,
                "vehicle": None,
                "mileage": int(odometer),
                "accident_count": accidents,
                "screenshot": screenshot_base64,
                "retail": None,
                "manheim": None,
                "price": None,
                "year": None,
                "make": None,
                "model":  None,
                "drivetrain": None,
                "fuel": None,
                "body_style": None,
            }
            for k, v in results.items():
                if len(str(v)) < 100:
                    logging.info(f"{k}: {v}")
            return 
        finally:
            try:
                self.close()
            except:
                pass


if __name__ == "__main__":
    vin = "1B7GL2AN81S127838"
    name = "2001 DODGE DAKOTA SLT/SPORT"
    engine = "4.7l v-8 235hp"
    dc = DealerCenterScraper(vin = vin, vehicle_name = name, engine = engine)
    result = dc.scrape()
    for k, v in result.items():
        if len(str(v)) < 100:
            print(f"{k}: {v}")