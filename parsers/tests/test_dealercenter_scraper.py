import asyncio
import httpx
import pytest
from services.parsers.dc_scraper import DealerCenterScraper, AuthRefreshedError

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

def fake_autocheck_html(owners: int = 2, odo: int = 123_456, damage_rows: int = 2) -> str:
    rows_html = "".join("<tr><td>a</td><td>b</td><td>Damage Type</td></tr>" for _ in range(damage_rows))
    return f"""
    <html>
      <body>
        <span class="box-title-owners"><span>{owners}</span></span>
        <p>Last reported odometer: <span class="font-weight-bold">{odo:,}</span></p>
        <table class="table table-striped">
          <thead><tr><th>Damage Type</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </body>
    </html>
    """

class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload
    def json(self):
        return self._payload

pytestmark = pytest.mark.anyio

async def test_get_history_and_market_data_async_happy_path(monkeypatch):
    dc = DealerCenterScraper(vin="TESTVIN1234567890", year=2016, make="Honda", model="CR-V", odometer=100000)
    dc.cookies = [{"name": "sid", "value": "abc"}]
    dc.access_token = "token"
    html = fake_autocheck_html(owners=3, odo=135_000, damage_rows=2)
    async def fake_auth_post(url: str, payload: dict):
        return _Resp({"htmlResponseData": html})
    async def fake_fetch_valuation():
        return 19000, 21000
    async def fake_fetch_market_stats(odometer_value):
        assert odometer_value == 135_000
        return 17500
    monkeypatch.setattr(dc, "_auth_post", fake_auth_post, raising=True)
    monkeypatch.setattr(dc, "_fetch_valuation", fake_fetch_valuation, raising=True)
    monkeypatch.setattr(dc, "_fetch_market_stats", fake_fetch_market_stats, raising=True)
    result = await dc.get_history_and_market_data_async()
    assert result["owners"] == 3
    assert result["mileage"] == 135_000
    assert result["accident_count"] == 2
    assert isinstance(result["html_data"], str) and "<html" in result["html_data"]
    assert result["jd"] == 19000
    assert result["manheim"] == 21000
    assert result["d_max"] == 17500

async def test_get_history_and_market_data_async_raises_on_401_and_forces_login(monkeypatch):
    dc = DealerCenterScraper(vin="TESTVIN401")
    dc.cookies = [{"name": "sid", "value": "abc"}]
    dc.access_token = "token"
    forced = []
    async def fake_ensure(force: bool = False):
        forced.append(force)
    async def fake_post(self, url, headers=None, cookies=None, json=None):
        return httpx.Response(status_code=401, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post, raising=True)
    monkeypatch.setattr(dc, "_ensure_logged_in_singleflight", fake_ensure, raising=True)
    with pytest.raises(AuthRefreshedError):
        await dc.get_history_and_market_data_async()
    assert forced == [True]
    assert dc.cookies == []
    assert dc.access_token is None

async def test__ensure_logged_in_singleflight_allows_only_one_login(monkeypatch):
    dc = DealerCenterScraper(vin="VIN")
    dc.cookies = []
    dc.access_token = None
    dc._login_ready.clear()
    calls = {"perform_login": 0}
    async def fake_perform_login():
        calls["perform_login"] += 1
        await asyncio.sleep(0.05)
        dc.cookies = [{"name": "sid", "value": "x"}]
        dc.access_token = "tkn"
    monkeypatch.setattr(dc, "_perform_login", fake_perform_login, raising=True)
    await asyncio.gather(
        dc._ensure_logged_in_singleflight(),
        dc._ensure_logged_in_singleflight(),
        dc._ensure_logged_in_singleflight(),
        dc._ensure_logged_in_singleflight(),
    )
    assert calls["perform_login"] == 1
    assert dc._login_ready.is_set()
    assert dc.cookies and dc.access_token

async def test__ensure_logged_in_singleflight_force_ignores_ready(monkeypatch):
    dc = DealerCenterScraper(vin="VIN")
    dc.cookies = [{"name": "sid", "value": "x"}]
    dc.access_token = "tkn"
    dc._login_ready.set()
    calls = {"perform_login": 0}
    async def fake_perform_login():
        calls["perform_login"] += 1
        await asyncio.sleep(0)
    monkeypatch.setattr(dc, "_perform_login", fake_perform_login, raising=True)
    await dc._ensure_logged_in_singleflight(force=True)
    assert calls["perform_login"] == 1
    assert dc._login_ready.is_set()
