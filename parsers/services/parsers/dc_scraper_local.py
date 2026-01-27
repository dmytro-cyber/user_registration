import asyncio
import email
import imaplib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

# ----------------------------- logging -----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ----------------------------- config -----------------------------
class Config:
    BASE_URL = "https://app.dealercenter.net"
    AUTOCHECK_URL = f"{BASE_URL}/api-gateway/inventory/AutoCheck/RunAutoCheckReport"
    VALUATION_URL = f"{BASE_URL}/api-gateway/inventory/BookService/GetValuationValues"
    MARKET_DATA_URL = f"{BASE_URL}/api-gateway/inventory/MarketData/GetMarketPriceStatistics?mathching=0"
    TOKEN_VALIDATION_URL = f"{BASE_URL}/api-gateway/admin/userauth/public/validaterefreshtoken"
    LOGIN_URL = f"{BASE_URL}/apps/shell/reports/home"

    TIMEOUT = 10
    MAX_WAIT_VERIFICATION = 30
    POLL_INTERVAL = 4
    VIEWPORT = {"width": 1280, "height": 720}

    BASE_HEADERS = {
        "authority": "app.dealercenter.net",
        "Accept": "application/json",
        "Content-Type": "application/*+json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/apps/shell/inventory/vehicle/history-reports",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="134", "Not:A-Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "macOS",
        "Priority": "u=1, i",
        "Timezone": "Europe/Kiev",
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


# ----------------------------- shared auth state (GLOBAL) -----------------------------
class AuthState:
    """
    Global single-flight login + shared credentials.
    This ensures only one concurrent login across ALL scraper instances in the process.
    """

    _login_lock = asyncio.Lock()
    _login_ready = asyncio.Event()

    _credentials_lock = asyncio.Lock()
    _credentials: Dict[str, Any] = {"cookies": [], "access_token": None, "saved_at": None}

    _credentials_file = "credentials.json"
    _loaded_from_file = False

    @classmethod
    async def load_from_file_once(cls) -> None:
        """Load credentials from disk once per process (best-effort)."""
        if cls._loaded_from_file:
            return
        cls._loaded_from_file = True
        if not os.path.exists(cls._credentials_file):
            return
        try:
            with open(cls._credentials_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            async with cls._credentials_lock:
                cls._credentials["cookies"] = data.get("cookies", []) or []
                cls._credentials["access_token"] = data.get("access_token")
                cls._credentials["saved_at"] = data.get("saved_at")
            if cls._credentials["cookies"] and cls._credentials["access_token"]:
                cls._login_ready.set()
            logger.info("Loaded credentials from credentials.json")
        except Exception as e:
            logger.warning(f"Failed to load credentials.json: {e}")

    @classmethod
    async def save_to_file(cls) -> None:
        """Persist credentials to disk (best-effort)."""
        async with cls._credentials_lock:
            payload = dict(cls._credentials)
        try:
            with open(cls._credentials_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("Saved credentials to credentials.json")
        except Exception as e:
            logger.warning(f"Failed to save credentials.json: {e}")

    @classmethod
    async def set_credentials(cls, cookies: list, access_token: str) -> None:
        async with cls._credentials_lock:
            cls._credentials["cookies"] = cookies or []
            cls._credentials["access_token"] = access_token
            cls._credentials["saved_at"] = int(time.time())
        cls._login_ready.set()
        await cls.save_to_file()

    @classmethod
    async def get_credentials(cls) -> Tuple[list, Optional[str]]:
        async with cls._credentials_lock:
            return (cls._credentials.get("cookies", []) or [], cls._credentials.get("access_token"))

    @classmethod
    async def clear_ready(cls) -> None:
        """Mark credentials as not ready (e.g., after 401/403)."""
        cls._login_ready.clear()

    @classmethod
    def ready(cls) -> bool:
        return cls._login_ready.is_set()


# ----------------------------- email client -----------------------------
class EmailClient:
    def __init__(self, email_addr: str, password: str, imap_server: str = "imap.gmail.com"):
        if not email_addr or not password:
            raise ValueError("Email and password must be provided in .env (SMTP_USER and SMTP_PASSWORD)")
        self.email = email_addr
        self.password = password
        self.imap_server = imap_server

    def _fetch_code_sync(
        self, max_wait: int = Config.MAX_WAIT_VERIFICATION, poll_interval: int = Config.POLL_INTERVAL
    ) -> Optional[str]:
        """
        Blocking IMAP polling. Intended to run in a background thread via asyncio.to_thread().
        """
        start_time = time.time()
        last_seen_id = None

        while time.time() - start_time < max_wait:
            mail = None
            try:
                mail = imaplib.IMAP4_SSL(self.imap_server)
                mail.login(self.email, self.password)
                mail.select("inbox")

                status, messages = mail.search(None, '(FROM "do-not-reply@dealercenter.net")')
                if status != "OK":
                    logger.info("IMAP search returned non-OK, retrying...")
                    time.sleep(poll_interval)
                    continue

                email_ids = messages[0].split()
                if not email_ids:
                    logger.info("No emails found from DealerCenter, retrying...")
                    time.sleep(poll_interval)
                    continue

                latest_email_id = email_ids[-1]
                if last_seen_id is not None and latest_email_id == last_seen_id:
                    time.sleep(poll_interval)
                    continue
                last_seen_id = latest_email_id

                status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
                if status != "OK":
                    time.sleep(poll_interval)
                    continue

                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    msg = email.message_from_bytes(response_part[1])

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            if ctype == "text/plain":
                                body = part.get_payload(decode=True).decode(errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors="ignore")

                    match = re.search(r"\b\d{6}\b", body)
                    if match:
                        code = match.group()
                        logger.info(f"Verification code found: {code}")
                        return code

            except imaplib.IMAP4.error as e:
                logger.error(f"IMAP login failed: {e}")
                raise
            except Exception as e:
                logger.error(f"Error checking email: {e}")
            finally:
                try:
                    if mail is not None:
                        mail.logout()
                except Exception:
                    pass

            time.sleep(poll_interval)

        logger.error("Failed to retrieve verification code within the timeout period.")
        return None

    async def get_verification_code(self) -> Optional[str]:
        """Async wrapper around blocking IMAP polling."""
        return await asyncio.to_thread(self._fetch_code_sync)


# ----------------------------- helpers -----------------------------
def _dedupe_reason_append(existing: Optional[str], reason: str) -> str:
    """Append 'reason;' to existing reasons string without duplicates."""
    reason = reason.strip()
    if not reason.endswith(";"):
        reason += ";"
    if not existing:
        return reason
    if reason in existing:
        return existing
    return existing + reason


def _extract_pre_json(html: str) -> dict:
    """Extract JSON from <pre>...</pre> page content."""
    m = re.search(r"<pre>\s*({.*})\s*</pre>", html, flags=re.DOTALL)
    if not m:
        raise RuntimeError("Failed to extract <pre>{json}</pre> from token page")
    return json.loads(m.group(1))


def _safe_int(text: str) -> Optional[int]:
    try:
        return int(text.replace(",", "").strip())
    except Exception:
        return None


def _parse_owners(soup: BeautifulSoup) -> int:
    owners_element = soup.select_one("span.box-title-owners > span")
    val = _safe_int(owners_element.text) if owners_element else None
    return val if val is not None else 1


def _parse_last_reported_odometer(soup: BeautifulSoup) -> Optional[int]:
    """
    Avoid :contains() (not supported by BeautifulSoup CSS selectors).
    We search text nodes and then find the nearest bold/span with number.
    """
    target = None
    for p in soup.find_all("p"):
        if p.get_text(" ", strip=True).lower().startswith("last reported odometer:"):
            target = p
            break
    if not target:
        return None

    # Try common patterns
    bold = target.find("span", class_=re.compile(r"font-weight-bold"))
    if bold and bold.get_text(strip=True):
        return _safe_int(bold.get_text(strip=True))

    # Fallback: any number in the paragraph
    m = re.search(r"([\d,]+)", target.get_text(" ", strip=True))
    return _safe_int(m.group(1)) if m else None


def _parse_accident_count(soup: BeautifulSoup) -> int:
    try:
        tables = soup.find_all("table", class_=re.compile(r"\btable\b"))
        for table in tables:
            if "Damage Type" in table.get_text():
                rows = table.find_all("tr")
                damage_rows = [row for row in rows if len(row.find_all("td")) >= 3]
                return len(damage_rows)
    except Exception:
        pass
    return 0


# ----------------------------- scraper -----------------------------
@dataclass
class ScrapeResult:
    owners: int
    mileage: Optional[int]
    accident_count: int
    html_data: str
    jd: Optional[int]
    manheim: Optional[int]
    d_max: Optional[int]


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

        self.dc_username = os.getenv("DC_USERNAME")
        self.dc_password = os.getenv("DC_PASSWORD")
        if not self.dc_username or not self.dc_password:
            raise ValueError("DC_USERNAME and DC_PASSWORD must be set in .env file")

        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        if not smtp_user or not smtp_password:
            raise ValueError("SMTP_USER and SMTP_PASSWORD must be set in .env file")
        self.email_client = EmailClient(smtp_user, smtp_password)

    async def _get_proxy(self) -> Optional[httpx.Proxy]:
        if self.proxy_host and self.proxy_port:
            return httpx.Proxy(f"socks5://{self.proxy_host}:{self.proxy_port}")
        return None

    async def _get_headers_and_cookies(self) -> Tuple[dict, dict]:
        cookies, access_token = await AuthState.get_credentials()
        headers = Config.BASE_HEADERS.copy()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies]) if cookies else ""
        if cookie_header:
            headers["Cookie"] = cookie_header
        cookies_dict = {c["name"]: c["value"] for c in cookies} if cookies else {}
        return headers, cookies_dict

    async def _try_autocheck(self, client: httpx.AsyncClient, headers: dict, cookies_dict: dict, payload: dict) -> None:
        r = await client.post(Config.AUTOCHECK_URL, headers=headers, cookies=cookies_dict, json=payload)
        if r.status_code in (401, 403):
            raise httpx.HTTPStatusError("Unauthorized", request=r.request, response=r)
        r.raise_for_status()

    async def _perform_login(self) -> None:
        """Perform the full login process using Playwright and update global credentials."""
        start_time = time.time()
        proxy_server = None
        if self.proxy_host and self.proxy_port:
            proxy_server = f"socks5://{self.proxy_host}:{self.proxy_port}"
            logger.info(f"Configured SOCKS5 proxy: {self.proxy_host}:{self.proxy_port}")

        async with async_playwright() as p:
            browser_args = dict(Config.BROWSER_ARGS)
            if proxy_server:
                browser_args["proxy"] = {"server": proxy_server}

            browser = await p.chromium.launch(**browser_args)
            context = await browser.new_context(user_agent=Config.BASE_HEADERS["User-Agent"], viewport=Config.VIEWPORT)
            page = await context.new_page()

            try:
                await page.goto(Config.LOGIN_URL, wait_until="domcontentloaded")
                logger.info("Navigated to DealerCenter login page")

                await page.wait_for_selector("#username", timeout=20000)
                await page.fill("#username", self.dc_username)
                await page.fill("#password", self.dc_password)
                await page.click("#login")
                logger.info("Clicked login button")

                # Try to select Email verification method (two known variants)
                try:
                    await page.wait_for_selector(
                        "xpath=//span[contains(text(), 'Email Verification Code')]/parent::a",
                        timeout=10000,
                    )
                    await page.click("xpath=//span[contains(text(), 'Email Verification Code')]/parent::a")
                    logger.info("Clicked Email Verification Code link")
                except Exception:
                    await page.wait_for_selector("#WebMFAEmail", timeout=10000)
                    await page.click("#WebMFAEmail")
                    logger.info("Clicked fallback WebMFAEmail link")

                # Wait a bit for email to arrive
                # Give page time to stabilize
                await asyncio.sleep(3)
                
                # Check throttling message
                try:
                    warning = await page.query_selector(
                        "p:has-text('You have exceeded the amount of emails')"
                    )
                    if warning:
                        logger.warning(
                            "DealerCenter email limit reached. Sleeping for 5 minutes before retry..."
                        )
                        await asyncio.sleep(300)   # 5 minutes
                        await page.reload()
                        await asyncio.sleep(3)
                except Exception:
                    pass
                
                verification_code = await self.email_client.get_verification_code()
                
                if not verification_code:
                    raise RuntimeError("Failed to retrieve verification code")
                

                await page.wait_for_selector("#email-passcode-input", timeout=20000)
                await page.fill("#email-passcode-input", verification_code)
                await page.click("#email-passcode-submit")
                logger.info("Submitted verification code")

                # Let session settle
                await page.wait_for_load_state("networkidle", timeout=30000)

                # Save cookies
                cookies = await context.cookies()

                # Retrieve token (token endpoint returns HTML with <pre>{json}</pre>)
                await page.goto(Config.TOKEN_VALIDATION_URL, wait_until="networkidle")
                content = await page.content()
                json_data = _extract_pre_json(content)
                access_token = json_data.get("userAccessToken")
                if not access_token:
                    raise RuntimeError("No userAccessToken found in response")

                await AuthState.set_credentials(cookies=cookies, access_token=access_token)
                logger.info("Access token retrieved and stored globally")

            finally:
                await browser.close()

        logger.info(f"Login completed in {time.time() - start_time:.2f}s")

    async def _ensure_logged_in_global_singleflight(self) -> None:
        """
        Global single-flight login:
        - If ready: return
        - If another coroutine is logging in: wait
        - Else: perform login once and wake everyone
        """
        await AuthState.load_from_file_once()

        if AuthState.ready():
            return

        if AuthState._login_lock.locked():
            await AuthState._login_ready.wait()
            return

        async with AuthState._login_lock:
            # Double check under lock
            await AuthState.load_from_file_once()
            if AuthState.ready():
                return

            AuthState._login_ready.clear()
            try:
                await self._perform_login()
            finally:
                # Always release waiters
                AuthState._login_ready.set()

    async def authenticate_and_prepare_async(self) -> Tuple[Optional[httpx.Proxy], dict, dict, dict]:
        """Prepare proxy, headers, cookies, and payload. Re-login globally on 401/403."""
        proxy = await self._get_proxy()

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

        await self._ensure_logged_in_global_singleflight()
        headers, cookies_dict = await self._get_headers_and_cookies()

        async with httpx.AsyncClient(proxy=proxy, timeout=Config.TIMEOUT) as client:
            try:
                await self._try_autocheck(client, headers, cookies_dict, payload)
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    logger.info("Got 401/403. Forcing global re-login single-flight...")
                    await AuthState.clear_ready()
                    await self._ensure_logged_in_global_singleflight()
                    headers, cookies_dict = await self._get_headers_and_cookies()
                    await self._try_autocheck(client, headers, cookies_dict, payload)
                else:
                    raise

        return proxy, headers, cookies_dict, payload

    async def _fetch_autocheck_html(
        self, proxy: Optional[httpx.Proxy], headers: dict, cookies_dict: dict, payload: dict
    ) -> str:
        async with httpx.AsyncClient(proxy=proxy, timeout=Config.TIMEOUT) as client:
            response = await client.post(Config.AUTOCHECK_URL, headers=headers, cookies=cookies_dict, json=payload)
            response.raise_for_status()
            data = response.json()

        html_data = data.get("htmlResponseData")
        if not html_data:
            raise RuntimeError("htmlResponseData key not found in response")
        return html_data

    async def get_market_data(
        self, proxy: Optional[httpx.Proxy], headers: dict, cookies_dict: dict, initial_payload: dict
    ) -> ScrapeResult:
        """Retrieve market data including AutoCheck report, JD valuation, and market price statistics."""
        start_time = time.time()

        html_data = await self._fetch_autocheck_html(proxy, headers, cookies_dict, initial_payload)

        # Optional: keep last html for debugging
        try:
            with open("response.html", "w", encoding="utf-8") as f:
                f.write(html_data)
        except Exception:
            pass

        soup = BeautifulSoup(html_data, "html.parser")

        owners_value = _parse_owners(soup)
        odometer_value = _parse_last_reported_odometer(soup) or self.odometer
        accidents_value = _parse_accident_count(soup)
        logger.info(f"Parsed: owners={owners_value}, odometer={odometer_value}, accidents={accidents_value}")

        # --- valuation ---
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

        jd = None
        manheim = None
        async with httpx.AsyncClient(proxy=proxy, timeout=Config.TIMEOUT) as client:
            response = await client.post(Config.VALUATION_URL, headers=headers, cookies=cookies_dict, json=payload_jd)
            response.raise_for_status()
            resp = response.json()
            try:
                jd_raw = resp.get("nada", {}).get("retailBook")
                man_raw = resp.get("manheim", {}).get("adjustedRetailAverage")
                jd = int(float(jd_raw)) if jd_raw is not None else None
                manheim = int(float(man_raw)) if man_raw is not None else None
            except Exception:
                logger.warning("Failed to extract valuation data")

        # --- market price statistics ---
        payload_market_data = {
            "vehicleInfo": {
                "entityID": "00000000-0000-0000-0000-000000000000",
                "entityTypeID": 3,
                "vin": self.vin,
                "stockNumber": "",
                "year": self.year,
                "make": self.make,
                "model": self.model,
                "odometer": odometer_value,
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
                "modelAggregate": [self.model] if self.model else [],
                "odometerMax": (odometer_value + 10000) if odometer_value is not None else None,
                "odometerMin": (odometer_value - 10000) if odometer_value is not None else None,
                "packages": [],
                "radiusInMiles": 1000,
                "transmissions": [],
                "yearAdjusment": 0,
                "years": [self.year] if self.year else [],
                "zip": "27834",
            },
            "maxDigitalPriceLockType": None,
        }

        d_max = None
        async with httpx.AsyncClient(proxy=proxy, timeout=Config.TIMEOUT) as client:
            response = await client.post(
                Config.MARKET_DATA_URL, headers=headers, cookies=cookies_dict, json=payload_market_data
            )
            response.raise_for_status()
            resp = response.json()
            try:
                price_avg = resp.get("priceAvg")
                d_max = int(float(price_avg)) if price_avg is not None else None
            except Exception:
                logger.warning("Failed to extract priceAvg")

        logger.info(f"Market data collected in {time.time() - start_time:.2f}s")
        return ScrapeResult(
            owners=owners_value,
            mileage=odometer_value,
            accident_count=accidents_value,
            html_data=html_data,
            jd=jd,
            manheim=manheim,
            d_max=d_max,
        )

    async def get_history_only_async(self) -> ScrapeResult:
        """Collect only vehicle history data."""
        start_time = time.time()
        proxy, headers, cookies_dict, payload = await self.authenticate_and_prepare_async()

        html_data = await self._fetch_autocheck_html(proxy, headers, cookies_dict, payload)
        soup = BeautifulSoup(html_data, "html.parser")

        owners_value = _parse_owners(soup)
        odometer_value = _parse_last_reported_odometer(soup) or self.odometer
        accidents_value = _parse_accident_count(soup)

        logger.info(f"History data collected in {time.time() - start_time:.2f}s")
        return ScrapeResult(
            owners=owners_value,
            mileage=odometer_value,
            accident_count=accidents_value,
            html_data=html_data,
            jd=None,
            manheim=None,
            d_max=None,
        )

    async def get_history_and_market_data_async(self) -> ScrapeResult:
        """Collect vehicle history and market data."""
        proxy, headers, cookies_dict, payload = await self.authenticate_and_prepare_async()
        return await self.get_market_data(proxy, headers, cookies_dict, payload)


# ----------------------------- demo -----------------------------
async def main():
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
    result = await dc.get_history_and_market_data_async()

    # Pretty-print small fields
    for k, v in result.__dict__.items():
        if v is None:
            continue
        if k == "html_data":
            print(f"{k}: {len(str(v))}")
        else:
            s = str(v)
            print(f"{k}: {s if len(s) < 120 else (s[:120] + '...')}")  # keep console clean


if __name__ == "__main__":
    asyncio.run(main())
