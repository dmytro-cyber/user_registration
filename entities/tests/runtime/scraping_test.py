import os
import shutil
from io import BytesIO

import pytest

pytestmark = pytest.mark.scraping_runtime


@pytest.mark.asyncio
async def test_playwright_chromium_can_render_without_external_network():
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content("<title>Entities scraping runtime</title><p>Ready</p>")
            assert await page.title() == "Entities scraping runtime"
        finally:
            await browser.close()


def test_selenium_chromium_and_driver_can_render():
    if not os.getenv("CHROMEDRIVER_PATH") or not os.getenv("CHROME_BINARY"):
        pytest.skip("CHROME_BINARY and CHROMEDRIVER_PATH are provided by the scraping image")
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.binary_location = os.environ["CHROME_BINARY"]
    for flag in ("--headless", "--no-sandbox", "--disable-dev-shm-usage"):
        options.add_argument(flag)
    driver = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER_PATH"]), options=options)
    try:
        driver.get("data:text/html,<title>Entities Chromium</title>")
        assert driver.title == "Entities Chromium"
    finally:
        driver.quit()


def test_cairo_and_tesseract_can_read_rendered_digits():
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is provided by the scraping image")
    import pytesseract
    from cairosvg import svg2png
    from PIL import Image

    content = b"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="160">
    <rect width="100%" height="100%" fill="white"/>
    <text x="25" y="115" font-family="sans-serif" font-size="100" fill="black">12345</text></svg>"""
    image = Image.open(BytesIO(svg2png(bytestring=content)))
    text = pytesseract.image_to_string(image, config="--psm 7 -c tessedit_char_whitelist=0123456789")
    assert text.strip() == "12345"
