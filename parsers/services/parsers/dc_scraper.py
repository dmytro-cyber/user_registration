# ============================================================
# DealerCenter Scraper
# Global single-flight login + MFA retry + real API calls
# ============================================================

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
# ENV & LOGGING
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
        "Accept": "application/json",
        "Content-Type": "application/*+json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/apps/shell/inventory/vehicle/history-reports",
        "User-Agent": "Mozilla/5.0 Chrome/134.0.0.0",
    }

    TIMEOUT = 90

    VIEWPORT = {"width": 1280, "height": 720}

    BROWSER_ARGS = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }

# ------------------------------------------------------------
# EXCEPTION
# ------------------------------------------------------------

class AuthRefreshedError(RuntimeError):
    pass

# ------------------------------------------------------------
# GLOBAL LOGIN MANAGER
# ------------------------------------------------------------

class GlobalLoginManager:
    lock = asyncio.Lock()
    ready = asyncio.Event()

    cookies: list = []
    access_token: Optional[str] = None

    ready.clear()

# ------------------------------------------------------------
# EMAIL CLIENT
# ------------------------------------------------------------

class EmailClient:

    def __init__(self, email_addr: str, password: str):
        self.email = email_addr
        self.password = password

    def get_verification_code(self, timeout=60, poll=4) -> Optional[str]:

        start = time.time()

        while time.time() - start < timeout:
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

                    body = msg.get_payload(decode=True).decode(errors="ignore")
                    match = re.search(r"\b(\d{6})\b", body)

                    if match:
                        return match.group(1)

            except Exception:
                pass

            time.sleep(poll)

        return None

# ------------------------------------------------------------
# SCRAPER
# ------------------------------------------------------------

class DealerCenterScraper:

    def __init__(
        self,
        vin: str,
        vehicle_name: str,
        engine: str,
        year: int = None,
        make: str = None,
        model: str = None,
        odometer: int = None,
        transmission: str = None,
    ):
            # scraper = DealerCenterScraper(
            #     vin=car_vin,
            #     vehicle_name=car_name,
            #     engine=car_engine,
            #     make=car_make,
            #     model=car_model,
            #     year=car_year,
            #     transmission=car_transmison,
            #     odometer=car_mileage,
            # )
        self.vin = vin
        self.year = year
        self.make = make
        self.model = model
        self.odometer = odometer
        self.transmission = transmission
        self.vehicle_name = vehicle_name
        self.engine = engine

        self.dc_user = os.getenv("DC_USERNAME")
        self.dc_pass = os.getenv("DC_PASSWORD")

        self.email_client = EmailClient(
            os.getenv("SMTP_USER"),
            os.getenv("SMTP_PASSWORD"),
        )

    # --------------------------------------------------------
    # HEADERS
    # --------------------------------------------------------

    def _headers(self) -> dict:

        h = Config.BASE_HEADERS.copy()

        if GlobalLoginManager.access_token:
            h["Authorization"] = f"Bearer {GlobalLoginManager.access_token}"

        if GlobalLoginManager.cookies:
            h["Cookie"] = "; ".join(
                f"{c['name']}={c['value']}"
                for c in GlobalLoginManager.cookies
            )

        return h

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------

    async def _ensure_logged_in(self):

        if GlobalLoginManager.access_token:
            return

        if GlobalLoginManager.lock.locked():
            await GlobalLoginManager.ready.wait()
            return

        async with GlobalLoginManager.lock:
            GlobalLoginManager.ready.clear()
            try:
                await self._login_with_retries()
            finally:
                GlobalLoginManager.ready.set()

    async def _login_with_retries(self, attempts=5):

        for i in range(attempts):
            try:
                await self._perform_login()
                return
            except Exception as e:
                wait = 300 + random.randint(1, 60)
                logging.warning(f"Login attempt {i+1} failed: {e}. Wait {wait}s")
                await asyncio.sleep(wait)

        raise RuntimeError("Login failed after retries")

    async def _perform_login(self):

        async with async_playwright() as p:
            browser = await p.chromium.launch(**Config.BROWSER_ARGS)
            ctx = await browser.new_context(viewport=Config.VIEWPORT)
            page = await ctx.new_page()

            await page.goto(Config.LOGIN_URL)
            await page.wait_for_selector("#username", state="visible")
            await page.fill("#username", self.dc_user)

            await page.wait_for_selector("#password", state="visible")
            await page.fill("#password", self.dc_pass)
            await page.locator("button:has-text('Continue')").click(force=True)

            await asyncio.sleep(15)

            if await page.locator(
                "text=You have exceeded the amount of emails"
            ).count():
                raise RuntimeError("MFA rate limited")

            code = self.email_client.get_verification_code()

            if not code:
                raise RuntimeError("MFA code not received")

            await page.wait_for_selector("#code", state="visible")
            await page.fill("#code", code)
            await page.locator("button:has-text('Continue')").click(force=True)
            await asyncio.sleep(10)

            GlobalLoginManager.cookies = await ctx.cookies()

            await page.goto(Config.TOKEN_VALIDATION_URL)
            html = await page.content()

            m = re.search(r"<pre>(.*?)</pre>", html)
            data = json.loads(m.group(1))
            GlobalLoginManager.access_token = data["userAccessToken"]

            await browser.close()

            logging.info("Login successful")

    # --------------------------------------------------------
    # HTTP POST
    # --------------------------------------------------------

    async def _post(self, url: str, payload: dict) -> httpx.Response:

        await self._ensure_logged_in()

        async with httpx.AsyncClient(timeout=Config.TIMEOUT) as client:

            r = await client.post(url, headers=self._headers(), json=payload)

            if r.status_code in (401, 403):
                GlobalLoginManager.access_token = None
                raise AuthRefreshedError()

            r.raise_for_status()
            return r

    # --------------------------------------------------------
    # PARSING
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
            tables = soup.find_all("table")
            for t in tables:
                if "Damage Type" in t.text:
                    accidents = len(t.find_all("tr")) - 1
                    break
        except Exception:
            pass

        return {
            "owners": owners,
            "accident_count": accidents,
            "mileage": self.odometer,
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

        r = await self._post(Config.VALUATION_URL, payload)
        j = r.json()

        try:
            jd = int(float(j["nada"]["retailBook"]))
            manheim = int(float(j["manheim"]["adjustedRetailAverage"]))
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

        r = await self._post(Config.MARKET_DATA_URL, payload)
        j = r.json()

        try:
            return int(float(j["priceAvg"]))
        except Exception:
            return None

    # --------------------------------------------------------
    # PUBLIC API (UNCHANGED)
    # --------------------------------------------------------

    async def get_history_only_async(self):

        html = (await self._post(
            Config.AUTOCHECK_URL,
            {"vin": self.vin}
        )).json()["htmlResponseData"]

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

        html = (await self._post(
            Config.AUTOCHECK_URL,
            {"vin": self.vin}
        )).json()["htmlResponseData"]

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
            year=2016,
            make="Honda",
            model="CR-V",
            odometer=100000,
            transmission="Automatic",
        )

        result = await dc.get_history_and_market_data_async()

        for k, v in result.items():
            if k == "html_data":
                print("html_data length:", len(v))
            else:
                print(k, ":", v)

    asyncio.run(main())
