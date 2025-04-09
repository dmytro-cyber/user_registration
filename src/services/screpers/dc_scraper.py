import json
import os
import re
import time
import email
import imaplib
from email.header import decode_header
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

class GmailClient:
    def __init__(self):
        load_dotenv()
        self.email = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASSWORD")
        self.imap_server = "imap.gmail.com"

    def get_verification_code(self, max_wait=30, poll_interval=2):
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
    def __init__(self, vin):
        self.vin = vin
        self.proxy_host = os.getenv("PROXY_HOST")
        self.proxy_port = os.getenv("PROXY_PORT")
        self.cookies_file = "cookies.json"
        self.driver = self._init_driver()
        self.wait = WebDriverWait(self.driver, 10)  # Reduced default timeout to 10 seconds
        self.driver_closed = False  # Flag to track if the driver has been closed

    def _init_driver(self):
        """Initialize the Chrome driver with specified options."""
        chrome_options = Options()
        chrome_options.add_argument(f"--proxy-server=socks5://{self.proxy_host}:{self.proxy_port}")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--allow-insecure-localhost")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--headless")  # Enable headless mode
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-breakpad")
        
        chrome_options.binary_location = "/usr/bin/google-chrome"
        
        service = Service()
        driver = uc.Chrome(service=service, options=chrome_options)
        logging.info("Chrome driver initialized.")
        return driver

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
            screenshot_path = "/usr/src/fastapi/screenshots/login_form_error.png"
            self.driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
            raise

        # Вводимо логін і пароль
        username_field.send_keys(dc_username)
        password_field.send_keys(dc_password)
        login_button.click()
        logging.info("Clicked login button")

        # Очікуємо появу кнопки для запиту коду верифікації
        try:
            self._click_if_exists("//span[contains(text(), 'Email Verification Code')]/parent::a", fallback_id="WebMFAEmail")
            logging.info("Clicked Email Verification Code link")
        except Exception as e:
            logging.error(f"Failed to click Email Verification Code link: {str(e)}")
            screenshot_path = "/usr/src/fastapi/screenshots/email_verification_error.png"
            self.driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
            raise

        # Added 5-second delay before fetching the verification code
        time.sleep(5)
        gmail = GmailClient()
        verification_code = gmail.get_verification_code(max_wait=30, poll_interval=2)
        if not verification_code:
            logging.error("Failed to retrieve verification code.")
            screenshot_path = "/usr/src/fastapi/screenshots/verification_code_retrieval_error.png"
            self.driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
            raise Exception("Failed to retrieve verification code.")

        # Очікуємо поле для введення коду верифікації
        try:
            verification_code_field = self.wait.until(EC.presence_of_element_located((By.ID, "email-passcode-input")))
            submit_button = self.driver.find_element(By.ID, "email-passcode-submit")
            logging.info("Verification code input field found")
        except Exception as e:
            logging.error(f"Failed to find verification code input field: {str(e)}")
            screenshot_path = "/usr/src/fastapi/screenshots/verification_code_input_error.png"
            self.driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
            raise

        # Вводимо код верифікації
        verification_code_field.send_keys(verification_code)
        submit_button.click()
        logging.info("Submitted verification code")

        # Додаємо скріншот після натискання кнопки відправки коду
        screenshot_path = "/usr/src/fastapi/screenshots/after_verification_submit.png"
        self.driver.save_screenshot(screenshot_path)
        logging.info(f"Screenshot saved to {screenshot_path}")

        # Логування помилки під час верифікації
        try:
            logging.error("Error during email verification.")
            screenshot_path = "/usr/src/fastapi/screenshots/email_verification_error_log.png"
            self.driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
        except Exception as e:
            logging.error(f"Error during email verification logging: {str(e)}")
            raise

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

    def run_history_report(self):
        """Run a vehicle history report and extract owners, odometer, and accidents data."""
        time.sleep(2)  # Added delay to ensure the page is fully loaded
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Inventory')]"))).click()
        self._click_if_exists("//button[.//span[contains(text(), 'Run History Report')]]")
        # Added 2-second delay before searching for the VIN input field
        time.sleep(2)
        elements = self.wait.until(EC.presence_of_all_elements_located((By.XPATH, "//input[contains(@class, 'k-input-inner')]")))
        elements[-1].send_keys(self.vin)
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[.//span[contains(text(), 'Run')]]]"))).click()
        iframe = self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "autocheck-content")))
        self.driver.switch_to.frame(iframe)
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
        odometer_value = self.driver.find_element(
            By.XPATH,
            "//p[contains(., 'Last reported odometer:')]/span[@class='font-weight-bold'][1]"
        ).text.replace(",", "")
        accidents_value = len(self.driver.find_elements(By.XPATH, "//table[@class='table table-striped']/tbody/tr"))
        self.driver.switch_to.default_content()
        return owners_value, odometer_value, accidents_value

    def get_market_data(self, odometer_value):
        """Retrieve market data including retail value, market price, year, make, model, drivetrain, fuel type, and body style for the vehicle."""
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Inventory')]"))).click()
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), 'Appraise New Vehicle')]]"))).click()
        vin_input = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-textbox[formcontrolname='vin'] input")))
        vin_input.send_keys(self.vin)
        odometer_input = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-numerictextbox[formcontrolname='odometer'] input")))
        odometer_input.click()
        # self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Next')]")))
        self._click_if_exists("//button[contains(., 'Next')]")
        time.sleep(2)
        odometer_input = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "kendo-numerictextbox[formcontrolname='odometer'] input")))
        odometer_input.send_keys(str(odometer_value))
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))).click()
        # Added 2-second delay after the first "Books" click to allow page update
        time.sleep(3)
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Books')]"))).click()
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'J.D. Power')]"))).click()
        retail_value = self.wait.until(EC.presence_of_element_located((
            By.XPATH,
            "//kendo-numerictextbox[@formcontrolname='RetailBook']//input"
        ))).get_attribute("aria-valuenow")
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'k-link') and contains(text(), 'Market Data')]"))).click()
        time.sleep(0.5)

        # Create a shorter wait for the market data elements
        short_wait = WebDriverWait(self.driver, 5)  # Reduced timeout to 5 seconds

        # Extract price value
        try:
            price_tooltip_element = short_wait.until(EC.presence_of_element_located((
                By.XPATH,
                "//span[contains(@class, 'max-digital__risk-slider-bar-tooltip')]//span[contains(@class, 'font-weight-bold')]"
            )))
            price_value = price_tooltip_element.text.strip().replace('$', '').replace(',', '')
        except (TimeoutException, NoSuchElementException):
            price_value = None

        # Wait for the main container of the market data elements to ensure the page is loaded
        short_wait.until(EC.presence_of_element_located((
            By.XPATH,
            "//dc-ui-shared-ui-shared-multiselect[contains(@formcontrolname, 'year') or contains(@formcontrolname, 'make') or contains(@formcontrolname, 'model') or contains(@formcontrolname, 'driveTrain') or contains(@formcontrolname, 'fuel') or contains(@formcontrolname, 'bodyStyle')]"
        )))

        # Extract all market data elements in one go
        year_value = None
        make_value = None
        model_value = None
        drivetrain_value = None
        fuel_value = None
        body_style_value = None

        try:
            year_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='year']//div[starts-with(@id, 'tag-')]")
            year_value = int(year_element.text.strip().split()[0])
        except NoSuchElementException:
            pass

        try:
            make_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='make']//div[starts-with(@id, 'tag-')]")
            make_value = make_element.text.strip()
        except NoSuchElementException:
            pass

        try:
            model_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='model']//div[starts-with(@id, 'tag-')]")
            model_value = model_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        try:
            drivetrain_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='driveTrain']//div[starts-with(@id, 'tag-')]")
            drivetrain_value = drivetrain_element.text.strip()
        except NoSuchElementException:
            pass

        try:
            fuel_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='fuel']//div[starts-with(@id, 'tag-')]")
            fuel_value = fuel_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        try:
            body_style_element = self.driver.find_element(By.XPATH, "//dc-ui-shared-ui-shared-multiselect[@formcontrolname='bodyStyle']//div[starts-with(@id, 'tag-')]")
            body_style_value = body_style_element.text.strip().split()[0]
        except NoSuchElementException:
            pass

        return retail_value, price_value, year_value, make_value, model_value, drivetrain_value, fuel_value, body_style_value

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
        self.login()
        owners, odometer, accidents = self.run_history_report()
        retail, price, year, make, model, drivetrain, fuel, body_style = self.get_market_data(odometer)
        return {
            "owners": owners,
            "vehicle": f"{year} {make} {model}",
            "mileage": int(odometer),
            "accident_count": accidents,
            "retail": retail,
            "price": price,
            "year": int(year),
            "make": make,
            "model": model,
            "drivetrain": drivetrain,
            "fuel": fuel,
            "body_style": body_style
        }
