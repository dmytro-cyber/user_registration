import asyncio
import email
import imaplib
import json
import logging
import os
import re
import time
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()


# Configuration class for constants
class Config:
    BASE_URL = "https://app.dealercenter.net"
    AUTOCHECK_URL = f"{BASE_URL}/api-gateway/inventory/AutoCheck/RunAutoCheckReport"
    VALUATION_URL = f"{BASE_URL}/api-gateway/inventory/BookService/GetValuationValues"
    MARKET_DATA_URL = f"{BASE_URL}/api-gateway/inventory/MarketData/GetMarketPriceStatistics?mathching=0"
    TOKEN_VALIDATION_URL = f"{BASE_URL}/api-gateway/admin/userauth/public/validaterefreshtoken"
    LOGIN_URL = f"{BASE_URL}/apps/shell/reports/home"

    BASE_HEADERS = {
        "authority": "app.dealercenter.net",
        "Accept": "application/json",
        "Content-Type": "application/*+json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/apps/shell/inventory/vehicle/history-reports",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 OPR/119.0.0.0",
        "X-Xsrf-Token": "dd091ebc-65c0-413f-af29-5fa9abf2e612",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Opera";v="119"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "macOS",
        "Priority": "u=1, i",
        "Timezone": "Europe/Kiev",
        "Dc-Location": "ceaf9582-d242-4911-9b81-2da5fa48b8bb",
        "Dc-User": "loc=ceaf9582-d242-4911-9b81-2da5fa48b8bb;cache=74e3653a-3a14-43f8-a1c1-64102452b408;type=Self;",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
    }

    BROWSER_ARGS = {
        "headless": True,
        "args": [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--allow-insecure-localhost",
            "--ignore-certificate-errors",
        ],
    }
    CREDENTIALS = {}
    UPDATING_CREDENTIALS = False

    TIMEOUT = 10
    MAX_WAIT_VERIFICATION = 30
    POLL_INTERVAL = 4
    VIEWPORT = {"width": 1280, "height": 720}


# Separate Email Client
class EmailClient:
    def __init__(self, email: str, password: str, imap_server: str = "imap.gmail.com"):
        if not email or not password:
            raise ValueError("Email and password must be provided in .env (SMTP_USER and SMTP_PASSWORD)")
        self.email = email
        self.password = password
        self.imap_server = imap_server

    def get_verification_code(
        self, max_wait: int = Config.MAX_WAIT_VERIFICATION, poll_interval: int = Config.POLL_INTERVAL
    ) -> Optional[str]:
        """Poll the inbox for the verification code with a maximum wait time."""
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                mail = imaplib.IMAP4_SSL(self.imap_server)
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
                            code = match.group()
                            logging.info(f"Verification code found: {code}")
                            mail.logout()
                            return code
                mail.logout()
            except imaplib.IMAP4.error as e:
                logging.error(f"IMAP login failed: {str(e)}")
                mail.logout()
                raise
            except Exception as e:
                logging.error(f"Error checking email: {str(e)}")
                mail.logout()
            time.sleep(poll_interval)
        logging.error("Failed to retrieve verification code within the timeout period.")
        return None


class DealerCenterScraper:
    def __init__(
        self,
        vin: str,
        vehicle_name: str = None,
        engine: str = None,
        year: int = None,
        make: str = None,
        model: str = None,
        odometer: int = None,
        transmission: str = None,
    ):
        self.vin = vin
        self.vehicle_name = vehicle_name
        self.engine = engine
        self.year = year
        self.make = make
        self.model = model
        self.odometer = odometer
        self.transmission = transmission
        self.proxy_host = os.getenv("PROXY_HOST")
        self.proxy_port = os.getenv("PROXY_PORT")
        self.credentials_file = "credentials.json"
        self.dc_username = os.getenv("DC_USERNAME")
        self.dc_password = os.getenv("DC_PASSWORD")
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        if not smtp_user or not smtp_password:
            raise ValueError("SMTP_USER and SMTP_PASSWORD must be set in .env file")
        self.email_client = EmailClient(smtp_user, smtp_password)
        self.cookies = []
        self.access_token = None
        self._load_credentials()

    def _load_credentials(self):
        """Load saved cookies and access token from file."""
        if Config.CREDENTIALS != {}:
            self.cookies = Config.CREDENTIALS.get("cookies", [])
            self.access_token = Config.CREDENTIALS.get("access_token")
            logging.info("Loaded saved credentials")

    def _save_credentials(self):
        """Save cookies and access token to file."""
        Config.CREDENTIALS = {"cookies": self.cookies, "access_token": self.access_token}
        logging.info("Saved credentials to Config.CREDENTIALS")

    def _get_headers(self):
        """Generate headers with dynamic Authorization and Cookie."""
        headers = Config.BASE_HEADERS.copy()
        headers["Authorization"] = f"Bearer {self.access_token}"
        headers["Cookie"] = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in self.cookies])
        return headers

    def _get_cookies_dict(self):
        """Generate cookies dictionary from stored cookies."""
        return {cookie["name"]: cookie["value"] for cookie in self.cookies}

    async def _perform_login(self):
        """Perform the full login process using Playwright."""
        start_time = time.time()
        async with async_playwright() as p:
            browser_args = Config.BROWSER_ARGS.copy()
            if self.proxy_host and self.proxy_port:
                browser_args["proxy"] = {"server": f"socks5://{self.proxy_host}:{self.proxy_port}"}
                logging.info(f"Configured SOCKS5 proxy: {self.proxy_host}:{self.proxy_port}")

            browser = await p.chromium.launch(**browser_args)
            context = await browser.new_context(
                user_agent=Config.BASE_HEADERS["User-Agent"],
                viewport=Config.VIEWPORT,
            )
            page = await context.new_page()

            try:
                await page.goto(Config.LOGIN_URL)
                logging.info("Navigated to DealerCenter login page")

                await page.wait_for_selector("#username")
                await page.fill("#username", self.dc_username)
                await page.fill("#password", self.dc_password)
                await page.click("#login")
                logging.info("Clicked login button")

                try:
                    await page.wait_for_selector(
                        "xpath=//span[contains(text(), 'Email Verification Code')]/parent::a",
                        timeout=10000,
                    )
                    await page.click("xpath=//span[contains(text(), 'Email Verification Code')]/parent::a")
                    logging.info("Clicked Email Verification Code link")
                except:
                    try:
                        await page.wait_for_selector("#WebMFAEmail", timeout=5000)
                        await page.click("#WebMFAEmail")
                        logging.info("Clicked fallback WebMFAEmail link")
                    except Exception as e:
                        logging.error(f"Failed to click Email Verification Code link: {str(e)}")
                        raise

                await asyncio.sleep(10)
                verification_code = self.email_client.get_verification_code()
                if not verification_code:
                    logging.error("Failed to retrieve verification code. Check SMTP credentials or email settings.")
                    raise Exception("Failed to retrieve verification code.")

                await page.wait_for_selector("#email-passcode-input")
                await page.fill("#email-passcode-input", verification_code)
                await page.click("#email-passcode-submit")
                logging.info("Submitted verification code")

                await asyncio.sleep(5)

                self.cookies = await context.cookies()
                self._save_credentials()

                await page.goto(Config.TOKEN_VALIDATION_URL)
                await page.wait_for_load_state("networkidle")

                content = await page.content()
                logging.info(content)
                match = re.search(r"<pre>({.*})</pre>", content)
                if not match:
                    logging.error("Failed to extract token from response")
                    raise Exception("Failed to extract token from response")
                json_data = json.loads(match.group(1))
                self.access_token = json_data.get("userAccessToken")
                if not self.access_token:
                    logging.error("No userAccessToken found in response")
                    raise Exception("No userAccessToken found in response")
                logging.info("Access token retrieved")
                self._save_credentials()

            finally:
                await browser.close()
        end_time = time.time()
        logging.info(f"Login completed in {end_time - start_time} seconds")

    async def authenticate_and_prepare_async(self) -> Tuple[Optional[httpx.Proxy], dict, dict, dict]:
        """Authenticate and prepare credentials, falling back to login if needed."""
        start_time = time.time()
        proxy = None
        if self.proxy_host and self.proxy_port:
            proxy = httpx.Proxy(f"socks5://{self.proxy_host}:{self.proxy_port}")

        payload = {
            "auctionVehicleId": None,
            "deviceType": None,
            "format": 1,
            "inventoryId": None,
            "language": 1,
            "reportOwner": 1,
            "reportType": 1,
            "userAgent": None,
            "vin": self.vin,
        }
        headers = self._get_headers()
        cookies_dict = self._get_cookies_dict()

        if self.cookies and self.access_token:
            async with httpx.AsyncClient(proxy=proxy) as client:
                try:
                    response = await client.post(
                        Config.AUTOCHECK_URL,
                        headers=headers,
                        cookies=cookies_dict,
                        json=payload,
                        timeout=Config.TIMEOUT,
                    )
                    response.raise_for_status()
                    logging.info("API call succeeded with saved credentials")
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403):
                        logging.info("Saved credentials invalid, performing full login")
                        if not Config.UPDATING_CREDENTIALS:
                            Config.UPDATING_CREDENTIALS = True
                            await self._perform_login()
                            Config.UPDATING_CREDENTIALS = False
                        else:
                            while True:
                                await asyncio.sleep(5)
                                if Config.UPDATING_CREDENTIALS:
                                    break
                    else:
                        raise
                except Exception as e:
                    logging.error(f"API call failed: {str(e)}")
                    if not Config.UPDATING_CREDENTIALS:
                        Config.UPDATING_CREDENTIALS = True
                        await self._perform_login()
                        Config.UPDATING_CREDENTIALS = False
                    else:
                        while True:
                            await asyncio.sleep(5)
                            if Config.UPDATING_CREDENTIALS:
                                break
        else:
            logging.info("No saved credentials, performing full login")
            if not Config.UPDATING_CREDENTIALS:
                Config.UPDATING_CREDENTIALS = True
                await self._perform_login()
                Config.UPDATING_CREDENTIALS = False
            else:
                while True:
                    await asyncio.sleep(5)
                    if Config.UPDATING_CREDENTIALS:
                        break

        end_time = time.time()
        logging.info(f"Authentication prepared in {end_time - start_time} seconds")
        return proxy, headers, cookies_dict, payload

    async def get_market_data(
        self, proxy: Optional[httpx.Proxy], headers: dict, cookies_dict: dict, initial_payload: dict
    ) -> dict:
        """Retrieve market data including AutoCheck report, JD valuation, and market price statistics."""
        start_time = time.time()
        response = await httpx.AsyncClient(proxy=proxy).post(
            Config.AUTOCHECK_URL,
            headers=headers,
            cookies=cookies_dict,
            json=initial_payload,
            timeout=Config.TIMEOUT,
        )
        response.raise_for_status()
        response_json = response.json()

        html_data = response_json.get("htmlResponseData")
        if not html_data:
            logging.error("htmlResponseData key not found in response")
            raise Exception("htmlResponseData key not found in response")

        with open("response.html", "w", encoding="utf-8") as f:
            f.write(html_data)
        logging.info("HTML saved to response.html")

        soup = BeautifulSoup(html_data, "html.parser")

        owners_value = None
        try:
            owners_element = soup.select_one("span.box-title-owners > span")
            owners_value = int(owners_element.text) if owners_element else 1
        except:
            logging.warning("Failed to extract owners, defaulting to 1")
            owners_value = 1

        odometer_value = None
        try:
            odometer_element = soup.select_one("p:contains('Last reported odometer:') span.font-weight-bold")
            odometer_value = int(odometer_element.text.replace(",", "")) if odometer_element else self.odometer
        except:
            logging.error("Failed to extract odometer value")
            odometer_value = self.odometer

        accidents_value = 0
        try:
            tables = soup.find_all("table", class_="table table-striped")
            for table in tables:
                if "Damage Type" in table.get_text():
                    rows = table.find_all("tr")
                    damage_rows = [row for row in rows if len(row.find_all("td")) >= 3]
                    accidents_value = len(damage_rows)
                    logging.info(f"✅ Found {accidents_value} accident records.")
                    break
            else:
                logging.warning("⚠️ No damage table with 'Damage Type' found.")
        except Exception as e:
            logging.warning(f"❌ Failed to extract accidents: {str(e)}, defaulting to 0")

        payload_jd = {
            "method": 1,
            "odometer": self.odometer,
            "vehicleType": 1,
            "vin": self.vin,
            "isTitleBrandCommercial": False,
            "hasExistingBBBBooked": False,
            "hasExistingNadaBooked": False,
            "vehicleBuilds": [
                {
                    "bookPeriod": None,
                    "region": "NC",
                    "bookType": 2,
                    "modelId": "some_model_id",
                    "manheim": None,
                },
                {"bookType": 4},
            ],
        }

        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.post(
                Config.VALUATION_URL,
                headers=headers,
                cookies=cookies_dict,
                json=payload_jd,
                timeout=Config.TIMEOUT,
            )
            response.raise_for_status()
            response_json = response.json()
            jd = None
            manheim = None
            try:
                jd = int(float(response_json.get("nada", {}).get("retailBook")))
                manheim = int(float(response_json.get("manheim", {}).get("adjustedRetailAverage")))
            except Exception:
                logging.error("Failed to extract valuation data")

        market_data_url = Config.MARKET_DATA_URL
        payload_market_data = {
            "vehicleInfo": {
                "entityID": "00000000-0000-0000-0000-000000000000",
                "entityTypeID": 3,
                "vin": self.vin,
                "stockNumber": "",
                "year": self.year,
                "make": self.make,
                "model": self.model,
                "odometer": odometer_value or self.odometer,
                "transmission": self.transmission,
                "vehiclePrice": 0,
                "advertisingPrice": 0,
                "askingPrice": 0,
                "specialPrice": 0,
                "specialPriceStartDate": None,
                "specialPriceEndDate": None,
                "price": 0,
                "totalCost": 0,
                "certified": None,
            },
            "filters": {
                "bodyStyles": [],
                "driveTrains": [],
                "engines": [],
                "equipments": [],
                "fuelTypes": [],
                "geoCoordinate": None,
                "isActive": 1,
                "isCertified": None,
                "longitude": 0,
                "latitude": 0,
                "modelAggregate": [self.model],
                "odometerMax": (odometer_value or self.odometer) + 10000 if odometer_value or self.odometer else None,
                "odometerMin": (odometer_value or self.odometer) - 10000 if odometer_value or self.odometer else None,
                "packages": [],
                "radiusInMiles": 1000,
                "transmissions": [],
                "yearAdjusment": 0,
                "years": [self.year],
                "zip": "27834",
            },
            "maxDigitalPriceLockType": None,
        }
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.post(
                market_data_url,
                headers=headers,
                cookies=cookies_dict,
                json=payload_market_data,
                timeout=Config.TIMEOUT,
            )
            response.raise_for_status()
            response_json = response.json()
            d_max = None
            try:
                d_max = int(float(response_json.get("priceAvg")))
            except Exception:
                logging.error("Failed to extract priceAvg, defaulting to 0")

        result = {
            "owners": owners_value,
            "mileage": odometer_value,
            "accident_count": accidents_value,
            "html_data": html_data,
            "jd": jd,
            "manheim": manheim,
            "d_max": d_max,
        }
        end_time = time.time()
        logging.info(f"Market data collected in {end_time - start_time} seconds")
        return result

    async def get_history_only_async(self):
        """Collect only vehicle history data."""
        start_time = time.time()
        proxy, headers, cookies_dict, payload = await self.authenticate_and_prepare_async()
        response = await httpx.AsyncClient(proxy=proxy).post(
            Config.AUTOCHECK_URL,
            headers=headers,
            cookies=cookies_dict,
            json=payload,
            timeout=Config.TIMEOUT,
        )
        response.raise_for_status()
        response_json = response.json()

        html_data = response_json.get("htmlResponseData")
        if not html_data:
            logging.error("htmlResponseData key not found in response")
            raise Exception("htmlResponseData key not found in response")

        # with open("response.html", "w", encoding="utf-8") as f:
        #     f.write(html_data)
        # logging.info("HTML saved to response.html")

        soup = BeautifulSoup(html_data, "html.parser")

        owners_value = None
        try:
            owners_element = soup.select_one("span.box-title-owners > span")
            owners_value = int(owners_element.text) if owners_element else 1
        except:
            logging.warning("Failed to extract owners, defaulting to 1")
            owners_value = 1

        odometer_value = None
        try:
            odometer_element = soup.select_one("p:contains('Last reported odometer:') span.font-weight-bold")
            odometer_value = int(odometer_element.text.replace(",", "")) if odometer_element else self.odometer
        except:
            logging.error("Failed to extract odometer value")
            odometer_value = self.odometer

        accidents_value = 0
        try:
            tables = soup.find_all("table", class_="table table-striped")
            for table in tables:
                if "Damage Type" in table.get_text():
                    rows = table.find_all("tr")
                    damage_rows = [row for row in rows if len(row.find_all("td")) >= 3]
                    accidents_value = len(damage_rows)
                    logging.info(f"✅ Found {accidents_value} accident records.")
                    break
            else:
                logging.warning("⚠️ No damage table with 'Damage Type' found.")
        except Exception as e:
            logging.warning(f"❌ Failed to extract accidents: {str(e)}, defaulting to 0")

        result = {
            "owners": owners_value,
            "mileage": odometer_value,
            "accident_count": accidents_value,
            "html_data": html_data,
            "jd": None,
            "manheim": None,
            "d_max": None,
        }
        end_time = time.time()
        logging.info(f"History data collected in {end_time - start_time} seconds")
        return result

    async def get_history_and_market_data_async(self):
        """Collect vehicle history and market data."""
        start_time = time.time()
        proxy, headers, cookies_dict, payload = await self.authenticate_and_prepare_async()
        result = await self.get_market_data(proxy, headers, cookies_dict, payload)
        end_time = time.time()
        logging.info(f"History & Market data collected in {end_time - start_time} seconds")
        return result


if __name__ == "__main__":
    vin = "2HKRM4H59GH672591"
    name = "2016 Honda CR-V"
    engine = "4-Cyl, i-VTEC, 2.4 Liter"
    year = 2016
    make = "Honda"
    model = "CR-V"
    odometer = 100000
    transmission = "Automatic"
    dc = DealerCenterScraper(
        vin=vin,
        vehicle_name=name,
        engine=engine,
        year=year,
        make=make,
        model=model,
        odometer=odometer,
        transmission=transmission,
    )
    result = asyncio.run(dc.get_history_and_market_data_async())
    for k, v in result.items():
        if v is not None and len(str(v)) < 100:
            print(f"{k}: {v}")
        if k == "html_data":
            print(f"{k}: {len(str(v))}")
