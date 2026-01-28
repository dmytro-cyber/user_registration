import asyncio
import email
import imaplib
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ------------------------------------------------------------
# ENV / LOGGING
# ------------------------------------------------------------

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ------------------------------------------------------------
# CONFIG
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

    TIMEOUT = 100
    VIEWPORT = {"width": 1280, "height": 720}

    CREDENTIALS: Dict[str, Any] = {}

# ------------------------------------------------------------
# EXCEPTION
# ------------------------------------------------------------

class AuthRefreshedError(RuntimeError):
    pass

# ------------------------------------------------------------
# GLOBAL AUTH
# ------------------------------------------------------------

class GlobalAuth:
    lock = asyncio.Lock()
    ready = asyncio.Event()
    cookies: list = []
    access_token: Optional[str] = None

GlobalAuth.ready.clear()

# ------------------------------------------------------------
# EMAIL CLIENT
# ------------------------------------------------------------

class EmailClient:

    def __init__(self, email_addr: str, password: str):
        self.email = email_addr
        self.password = password

    def _extract_body(self, msg) -> str:
        # Multipart email
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()

                if content_type in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    text = payload.decode(errors="ignore")

                    # If HTML -> convert to text
                    if content_type == "text/html":
                        soup = BeautifulSoup(text, "html.parser")
                        return soup.get_text(" ", strip=True)

                    return text

        # Not multipart
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(errors="ignore")

        return ""

    def get_verification_code(self, timeout=60, poll=4) -> Optional[str]:
        start = time.time()

        while time.time() - start < timeout:
            mail = None
            try:
                mail = imaplib.IMAP4_SSL("imap.gmail.com")
                mail.login(self.email, self.password)
                mail.select("INBOX")

                status, data = mail.search(
                    None,
                    '(UNSEEN FROM "do-not-reply@dealercenter.net")'
                )

                if status == "OK" and data[0]:
                    msg_id = data[0].split()[-1]
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])

                    body = self._extract_body(msg)

                    match = re.search(r"\b(\d{6})\b", body)
                    if match:
                        return match.group(1)

            except Exception as e:
                print("EMAIL ERROR:", e)

            finally:
                try:
                    if mail:
                        mail.logout()
                except Exception:
                    pass

            time.sleep(poll)

        return None

# ------------------------------------------------------------
# SCRAPER
# ------------------------------------------------------------

class DealerCenterScraper:

    # -------- class-level single-flight --------
    _auth_lock = asyncio.Lock()
    _auth_ready = asyncio.Event()
    _shared_cookies: list = []
    _shared_token: Optional[str] = None
    _auth_ready.clear()

    # -------------------------------------------

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

        self.email_client = EmailClient(
            os.getenv("SMTP_USER"),
            os.getenv("SMTP_PASSWORD"),
        )

        self._load_credentials()

    # --------------------------------------------------------

    def _load_credentials(self):
        if Config.CREDENTIALS:
            DealerCenterScraper._shared_cookies = Config.CREDENTIALS.get("cookies", [])
            DealerCenterScraper._shared_token = Config.CREDENTIALS.get("access_token")

    def _save_credentials(self):
        Config.CREDENTIALS = {
            "cookies": DealerCenterScraper._shared_cookies,
            "access_token": DealerCenterScraper._shared_token,
        }

    # --------------------------------------------------------

    def _headers(self) -> dict:

        h = Config.BASE_HEADERS.copy()

        if DealerCenterScraper._shared_token:
            h["Authorization"] = f"Bearer {DealerCenterScraper._shared_token}"

        if DealerCenterScraper._shared_cookies:
            h["Cookie"] = "; ".join(
                f"{c['name']}={c['value']}"
                for c in DealerCenterScraper._shared_cookies
            )

        return h

    def _cookies_dict(self) -> dict:
        return {
            c["name"]: c["value"]
            for c in DealerCenterScraper._shared_cookies
        }

    # --------------------------------------------------------
    # SINGLE FLIGHT LOGIN
    # --------------------------------------------------------

    async def _ensure_logged_in_singleflight(self, force=False):

        if DealerCenterScraper._shared_token and not force:
            return

        if DealerCenterScraper._auth_lock.locked():
            await DealerCenterScraper._auth_ready.wait()
            return

        async with DealerCenterScraper._auth_lock:
            DealerCenterScraper._auth_ready.clear()
            try:
                await self._login_with_retries()
            finally:
                DealerCenterScraper._auth_ready.set()

    async def _login_with_retries(self):

        for i in range(5):
            try:
                await self._perform_login()
                return
            except Exception as e:
                wait = 300 + random.randint(1, 60)
                logging.warning(f"Login failed ({i+1}/5): {e}. Retry in {wait}s")
                await asyncio.sleep(wait)

        raise RuntimeError("Login retries exceeded")

    async def _perform_login(self):

        async with async_playwright() as p:
            browser = await p.chromium.launch(**Config.BROWSER_ARGS)
            context = await browser.new_context(
                user_agent=Config.BASE_HEADERS["User-Agent"],
                viewport=Config.VIEWPORT,
            )
            page = await context.new_page()

            try:
                await page.goto(Config.LOGIN_URL)

                await page.wait_for_selector("#username", state="visible")
                await page.fill("#username", self.dc_username)

                await page.wait_for_selector("#password", state="visible")
                await page.fill("#password", self.dc_password)

                await page.get_by_role("button", name="Continue").click(force=True)

                await page.wait_for_timeout(3000)

                if await page.locator(
                    "text=You have exceeded the amount of emails"
                ).count():
                    raise RuntimeError("MFA rate limited")

                await page.wait_for_selector("#code", timeout=20000)

                code = self.email_client.get_verification_code()
                if not code:
                    raise RuntimeError("MFA code not received")

                await page.fill("#code", code)
                await page.get_by_role("button", name="Continue").click(force=True)

                await page.wait_for_load_state("networkidle")

                DealerCenterScraper._shared_cookies = await context.cookies()
                self._save_credentials()

                await page.goto(Config.TOKEN_VALIDATION_URL)
                await page.wait_for_load_state("networkidle")

                html = await page.content()
                m = re.search(r"<pre>({.*})</pre>", html)
                data = json.loads(m.group(1))

                DealerCenterScraper._shared_token = data["userAccessToken"]
                self._save_credentials()

                logging.info("Login successful")

            finally:
                await browser.close()

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    async def _post(self, url: str, payload: dict) -> dict:

        await self._ensure_logged_in_singleflight()

        async with httpx.AsyncClient(timeout=Config.TIMEOUT) as client:
            r = await client.post(
                url,
                headers=self._headers(),
                cookies=self._cookies_dict(),
                json=payload,
            )

            if r.status_code in (401, 403):
                DealerCenterScraper._shared_cookies = []
                DealerCenterScraper._shared_token = None
                self._save_credentials()
                DealerCenterScraper._auth_ready.clear()
                raise AuthRefreshedError("Auth expired")

            r.raise_for_status()
            return r.json()

    # --------------------------------------------------------
    # PARSE
    # --------------------------------------------------------

    def _parse_history(self, html: str) -> dict:

        soup = BeautifulSoup(html, "html.parser")

        try:
            owners = int(
                soup.select_one("span.box-title-owners span").text
            )
        except Exception:
            owners = 1

        accidents = 0
        try:
            tables = soup.find_all("table", class_="table table-striped")
            for t in tables:
                if "Damage Type" in t.text:
                    accidents = len(t.find_all("tr")) - 1
                    break
        except Exception:
            pass

        return {
            "owners": owners,
            "mileage": self.odometer,
            "accident_count": accidents,
        }

    # --------------------------------------------------------
    # API HELPERS
    # --------------------------------------------------------

    async def _fetch_valuation(self) -> Tuple[Optional[int], Optional[int]]:

        payload = {
            "method": 1,
            "vin": self.vin,
            "odometer": self.odometer,
            "vehicleType": 1,
        }

        data = await self._post(Config.VALUATION_URL, payload)

        try:
            jd = int(float(data["nada"]["retailBook"]))
            manheim = int(float(data["manheim"]["adjustedRetailAverage"]))
            return jd, manheim
        except Exception:
            return None, None

    async def _fetch_market_stats(self, mileage) -> Optional[int]:

        payload = {
            "vehicleInfo": {
                "vin": self.vin,
                "year": self.year,
                "make": self.make,
                "model": self.model,
                "odometer": mileage,
                "transmission": self.transmission,
            },
            "filters": {
                "modelAggregate": [self.model],
                "years": [self.year],
                "radiusInMiles": 1000,
            },
        }

        data = await self._post(Config.MARKET_DATA_URL, payload)

        try:
            return int(float(data["priceAvg"]))
        except Exception:
            return None

    # --------------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------------

    async def get_history_only_async(self):

        response = await self._post(
            Config.AUTOCHECK_URL,
            {"vin": self.vin}
        )

        html = response.get("htmlResponseData")
        parsed = self._parse_history(html)

        return {
            "owners": parsed["owners"],
            "mileage": parsed["mileage"],
            "accident_count": parsed["accident_count"],
            "html_data": html,
            "jd": None,
            "manheim": None,
            "d_max": None,
        }

    async def get_history_and_market_data_async(self):

        response = await self._post(
            Config.AUTOCHECK_URL,
            {"vin": self.vin}
        )

        html = response.get("htmlResponseData")
        parsed = self._parse_history(html)

        jd, manheim = await self._fetch_valuation()
        d_max = await self._fetch_market_stats(parsed["mileage"])

        return {
            "owners": parsed["owners"],
            "mileage": parsed["mileage"],
            "accident_count": parsed["accident_count"],
            "html_data": html,
            "jd": jd,
            "manheim": manheim,
            "d_max": d_max,
        }

# ------------------------------------------------------------
# MANUAL RUN
# ------------------------------------------------------------

if __name__ == "__main__":

    async def main():

        dc = DealerCenterScraper(
            vin="2HKRM4H59GH672591",
            vehicle_name="2016 Honda CR-V",
            engine="4-Cyl, i-VTEC, 2.4 Liter",
            year=2016,
            make="Honda",
            model="CR-V",
            odometer=100000,
            transmission="Automatic",
        )

        try:
            result = await dc.get_history_and_market_data_async()
        except AuthRefreshedError:
            result = await dc.get_history_and_market_data_async()

        for k, v in result.items():
            if k == "html_data":
                print("html_data length:", len(v))
            else:
                print(k, ":", v)

    asyncio.run(main())