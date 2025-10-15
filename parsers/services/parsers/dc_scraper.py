import asyncio
import email
import imaplib
import json
import logging
import os
import re
import time
from typing import Optional, Tuple, Dict, Any

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ------------------------------------------------------------
# Logging / env
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()


# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
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
        "Timezone": "America/Los_Angeles",  # California, USA
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

    # In-memory credentials cache for the current process
    CREDENTIALS: Dict[str, Any] = {}

    TIMEOUT = 100
    MAX_WAIT_VERIFICATION = 30
    POLL_INTERVAL = 4
    VIEWPORT = {"width": 1280, "height": 720}


# ------------------------------------------------------------
# Special exception: login was performed, ask caller to retry
# ------------------------------------------------------------
class AuthRefreshedError(RuntimeError):
    """Signals the caller that credentials were refreshed and the call must be retried."""
    pass


# ------------------------------------------------------------
# Email client
# ------------------------------------------------------------
class EmailClient:
    def __init__(self, email_addr: str, password: str, imap_server: str = "imap.gmail.com"):
        if not email_addr or not password:
            raise ValueError("Email and password must be provided in .env (SMTP_USER and SMTP_PASSWORD)")
        self.email = email_addr
        self.password = password
        self.imap_server = imap_server

    def get_verification_code(
        self, max_wait: int = Config.MAX_WAIT_VERIFICATION, poll_interval: int = Config.POLL_INTERVAL
    ) -> Optional[str]:
        """
        Poll the inbox for a DealerCenter code email and extract a 6-digit code.
        """
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                mail = imaplib.IMAP4_SSL(self.imap_server)
                mail.login(self.email, self.password)
                mail.select("inbox")
                status, messages = mail.search(None, '(FROM "do-not-reply@dealercenter.net")')
                email_ids = messages[0].split()
                if not email_ids:
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


# ------------------------------------------------------------
# Scraper
# ------------------------------------------------------------
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

        self.dc_username = os.getenv("DC_USERNAME")
        self.dc_password = os.getenv("DC_PASSWORD")
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        if not smtp_user or not smtp_password:
            raise ValueError("SMTP_USER and SMTP_PASSWORD must be set in .env file")
        self.email_client = EmailClient(smtp_user, smtp_password)

        self.cookies: list = []
        self.access_token: Optional[str] = None
        self._load_credentials()

        # Single-flight primitives for login
        self._login_lock = asyncio.Lock()
        self._login_ready = asyncio.Event()
        self._login_ready.set() if (self.cookies and self.access_token) else self._login_ready.clear()

    # ----------------------- creds helpers -----------------------

    def _load_credentials(self):
        """Load cached cookies and token from the process-wide cache."""
        if Config.CREDENTIALS:
            self.cookies = Config.CREDENTIALS.get("cookies", []) or []
            self.access_token = Config.CREDENTIALS.get("access_token")
            logging.info("Loaded saved credentials")

    def _save_credentials(self):
        """Save cookies and token to the process-wide cache."""
        Config.CREDENTIALS = {"cookies": self.cookies, "access_token": self.access_token}
        logging.info("Saved credentials to Config.CREDENTIALS")

    def _headers(self) -> dict:
        """Build request headers with Authorization and Cookie if available."""
        h = Config.BASE_HEADERS.copy()
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in (self.cookies or [])])
        if cookie_header:
            h["Cookie"] = cookie_header
        return h

    def _cookies_dict(self) -> dict:
        """Return cookies as a simple dict for httpx."""
        return {c["name"]: c["value"] for c in (self.cookies or [])}

    # ----------------------- login flow --------------------------

    async def _ensure_logged_in_singleflight(self, force: bool = False):
        """
        Ensure the scraper is logged in. If another coroutine is logging in, wait for it.
        If `force=True`, always perform login regardless of the current ready state.
        """
        if self._login_ready.is_set() and not force:
            return
        if self._login_lock.locked():
            await self._login_ready.wait()
            return
        async with self._login_lock:
            if self._login_ready.is_set() and not force:
                return
            self._login_ready.clear()
            try:
                await self._perform_login()
            finally:
                self._login_ready.set()

    async def _perform_login(self):
        """Perform a fresh login with Playwright, store cookies and access token."""
        start_time = time.time()
        async with async_playwright() as p:
            browser = await p.chromium.launch(**Config.BROWSER_ARGS)
            context = await browser.new_context(
                user_agent=Config.BASE_HEADERS["User-Agent"],
                viewport=Config.VIEWPORT,
            )
            page = await context.new_page()
            try:
                await page.goto(Config.LOGIN_URL)
                await page.wait_for_selector("#username")
                await page.fill("#username", self.dc_username)
                await page.fill("#password", self.dc_password)
                await page.click("#login")

                # Select email MFA option
                try:
                    await page.wait_for_selector(
                        "xpath=//span[contains(text(), 'Email Verification Code')]/parent::a",
                        timeout=10000,
                    )
                    await page.click("xpath=//span[contains(text(), 'Email Verification Code')]/parent::a")
                except:
                    await page.wait_for_selector("#WebMFAEmail", timeout=5000)
                    await page.click("#WebMFAEmail")

                # Wait for email delivery, then fetch code
                await asyncio.sleep(20)
                code = self.email_client.get_verification_code()
                if not code:
                    raise Exception("Failed to retrieve verification code.")

                await page.wait_for_selector("#email-passcode-input")
                await page.fill("#email-passcode-input", code)
                await page.click("#email-passcode-submit")
                await asyncio.sleep(20)

                # Save cookies
                self.cookies = await context.cookies()
                self._save_credentials()

                # Fetch token
                await page.goto(Config.TOKEN_VALIDATION_URL)
                await page.wait_for_load_state("networkidle")
                content = await page.content()
                m = re.search(r"<pre>({.*})</pre>", content)
                if not m:
                    raise Exception("Failed to extract token from response")
                json_data = json.loads(m.group(1))
                self.access_token = json_data.get("userAccessToken")
                if not self.access_token:
                    raise Exception("No userAccessToken found in response")
                self._save_credentials()
            finally:
                await browser.close()
        logging.info(f"Login completed in {time.time() - start_time:.2f}s")

    # ----------------------- HTTP call guard (no internal retry) --------------------------

    async def _auth_post(self, url: str, payload: dict) -> httpx.Response:
        """
        Single POST with current credentials.
        On 401/403: force a re-login (clear cached creds, reset ready flag), then raise AuthRefreshedError.
        Other HTTP errors: re-raise.
        """
        headers = self._headers()
        cookies = self._cookies_dict()
        async with httpx.AsyncClient(timeout=Config.TIMEOUT) as client:
            try:
                r = await client.post(url, headers=headers, cookies=cookies, json=payload)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    logging.info("401/403 received → forcing re-login (clearing cached credentials)")
                    # Invalidate cached credentials and reset ready flag
                    self.cookies = []
                    self.access_token = None
                    self._save_credentials()
                    self._login_ready.clear()
                    # Perform a fresh login (single-flight; forced)
                    await self._ensure_logged_in_singleflight(force=True)
                    # Ask the caller to retry the request with fresh creds
                    raise AuthRefreshedError("Credentials refreshed, please retry") from e
                raise

    # ----------------------- parsing & API helpers ---------------------

    @staticmethod
    def _parse_history(html_data: str, fallback_odometer: Optional[int]) -> dict:
        """
        Extract owners, odometer and accident count from the AutoCheck HTML.
        """
        soup = BeautifulSoup(html_data, "html.parser")

        # owners
        try:
            owners_element = soup.select_one("span.box-title-owners > span")
            owners_val = int(owners_element.text) if owners_element else 1
        except Exception:
            logging.warning("Failed to extract owners, defaulting to 1")
            owners_val = 1

        # odometer
        try:
            odo_el = soup.select_one("p:contains('Last reported odometer:') span.font-weight-bold")
            odo_val = int(odo_el.text.replace(",", "")) if odo_el else fallback_odometer
        except Exception:
            logging.error("Failed to extract odometer value")
            odo_val = fallback_odometer

        # accidents
        acc_cnt = 0
        try:
            tables = soup.find_all("table", class_="table table-striped")
            for table in tables:
                if "Damage Type" in table.get_text():
                    rows = table.find_all("tr")
                    damage_rows = [row for row in rows if len(row.find_all("td")) >= 3]
                    acc_cnt = len(damage_rows)
                    break
        except Exception as e:
            logging.warning(f"Failed to extract accidents: {e}, defaulting to 0")

        return {
            "owners": owners_val,
            "mileage": odo_val,
            "accident_count": acc_cnt,
        }

    async def _fetch_history_html(self) -> str:
        """Call AutoCheck endpoint and return the embedded HTML string."""
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
        resp = await self._auth_post(Config.AUTOCHECK_URL, payload)
        data = resp.json()
        html_data = data.get("htmlResponseData")
        if not html_data:
            raise RuntimeError("htmlResponseData key not found in response")
        return html_data

    async def _fetch_valuation(self) -> Tuple[Optional[int], Optional[int]]:
        """
        Request valuation numbers and extract NADA retail and Manheim adjusted retail average, if present.
        """
        payload_jd = {
            "method": 1,
            "odometer": self.odometer,
            "vehicleType": 1,
            "vin": self.vin,
            "isTitleBrandCommercial": False,
            "hasExistingBBBBooked": False,
            "hasExistingNadaBooked": False,
            "vehicleBuilds": [
                {"bookPeriod": None, "region": "NC", "bookType": 2, "modelId": "some_model_id", "manheim": None},
                {"bookType": 4},
            ],
        }
        async with httpx.AsyncClient(timeout=Config.TIMEOUT) as client:
            r = await client.post(
                Config.VALUATION_URL,
                headers=self._headers(),
                cookies=self._cookies_dict(),
                json=payload_jd,
            )
            r.raise_for_status()
            j = r.json()
            jd = manheim = None
            try:
                jd = int(float(j.get("nada", {}).get("retailBook")))
                manheim = int(float(j.get("manheim", {}).get("adjustedRetailAverage")))
            except Exception:
                logging.warning("Valuation fields missing")
            return jd, manheim

    async def _fetch_market_stats(self, odometer_value: Optional[int]) -> Optional[int]:
        """
        Request market price statistics and return priceAvg if available.
        """
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
                "odometerMax": (odometer_value or self.odometer) + 10000 if (odometer_value or self.odometer) else None,
                "odometerMin": (odometer_value or self.odometer) - 10000 if (odometer_value or self.odometer) else None,
                "packages": [],
                "radiusInMiles": 1000,
                "transmissions": [],
                "yearAdjusment": 0,
                "years": [self.year],
                "zip": "27834",
            },
            "maxDigitalPriceLockType": None,
        }
        async with httpx.AsyncClient(timeout=Config.TIMEOUT) as client:
            r = await client.post(
                Config.MARKET_DATA_URL,
                headers=self._headers(),
                cookies=self._cookies_dict(),
                json=payload_market_data,
            )
            r.raise_for_status()
            j = r.json()
            try:
                return int(float(j.get("priceAvg")))
            except Exception:
                logging.warning("priceAvg missing")
                return None

    # ----------------------- public API --------------------------

    async def get_history_only_async(self):
        """
        Collect only vehicle history (AutoCheck HTML parsing).
        IMPORTANT: First step hits AUTOCHECK endpoint. On 401/403 we force a fresh login and
        raise AuthRefreshedError so the caller can retry with refreshed credentials.
        """
        # Fast-path if credentials are already present; otherwise login
        if not (self.cookies and self.access_token):
            await self._ensure_logged_in_singleflight()

        start_t = time.time()
        html_data = await self._fetch_history_html()  # may raise AuthRefreshedError
        parsed = self._parse_history(html_data, self.odometer)

        result = {
            "owners": parsed["owners"],
            "mileage": parsed["mileage"],
            "accident_count": parsed["accident_count"],
            "html_data": html_data,
            "jd": None,
            "manheim": None,
            "d_max": None,
        }
        logging.info(f"History data collected in {time.time() - start_t:.2f}s")
        return result

    async def get_history_and_market_data_async(self):
        """
        Collect history + valuations + market statistics.
        Starts from history; on 401/403 we force a login and bubble up AuthRefreshedError for an external retry.
        """
        if not (self.cookies and self.access_token):
            await self._ensure_logged_in_singleflight()

        start_t = time.time()
        # 1) history
        html_data = await self._fetch_history_html()  # may raise AuthRefreshedError
        parsed = self._parse_history(html_data, self.odometer)

        # 2) valuations
        jd, manheim = await self._fetch_valuation()

        # 3) market stats
        d_max = await self._fetch_market_stats(parsed["mileage"])

        result = {
            "owners": parsed["owners"],
            "mileage": parsed["mileage"],
            "accident_count": parsed["accident_count"],
            "html_data": html_data,
            "jd": jd,
            "manheim": manheim,
            "d_max": d_max,
        }
        logging.info(f"History & Market data collected in {time.time() - start_t:.2f}s")
        return result


# ------------------------------------------------------------
# Local manual run
# ------------------------------------------------------------
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

    try:
        result = asyncio.run(dc.get_history_and_market_data_async())
        for k, v in result.items():
            if v is not None and (not isinstance(v, str) or len(v) < 100):
                print(f"{k}: {v}")
            if k == "html_data":
                print(f"{k}: {len(str(v))}")
    except AuthRefreshedError:
        logging.info("Retrying after auth refresh...")
        result = asyncio.run(dc.get_history_and_market_data_async())
        for k, v in result.items():
            if v is not None and (not isinstance(v, str) or len(v) < 100):
                print(f"{k}: {v}")
            if k == "html_data":
                print(f"{k}: {len(str(v))}")
