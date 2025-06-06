import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import logging
import time
import httpx

# Configure logging with a specific format and level
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def fetch_page(url: str) -> str:
    """
    Asynchronously fetches the HTML content of a page using Playwright, with anti-detection measures.

    Args:
        url (str): The URL of the page to fetch.

    Returns:
        str: The fully rendered HTML content of the page.

    Raises:
        Exception: If an error occurs while fetching the page.
    """
    try:
        async with async_playwright() as p:
            # Launch browser in headless mode with anti-detection settings
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-setuid-sandbox",
                    "--ignore-certificate-errors",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            # Configure the context to mimic a real browser
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                locale="en-US",
                permissions=["geolocation"],
            )

            # Add script to hide WebDriver detection
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            """
            )

            # Create a new page
            page = await context.new_page()

            # Allow all network requests to avoid blocking
            await page.route("**/*", lambda route: route.continue_())

            # Navigate to the target page
            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Simulate user interaction
            logger.info("Simulating user interaction")
            await page.wait_for_timeout(1000)  # 1-second delay
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")  # Scroll to bottom
            await page.mouse.move(500, 500)  # Simulate mouse movement

            # Wait for the specific element
            logger.info("Waiting for span.bid-price element")
            element = await page.wait_for_selector("span.bid-price", timeout=10000)
            if element:
                logger.info("Element found, retrieving content")

            # Get the rendered HTML content
            html = await page.content()
            await browser.close()
            logger.info(f"Page {url} processing completed")
            return html
    except Exception as e:
        logger.error(f"Error fetching page {url}: {str(e)}")
        raise


async def parse_highlighted_value(html: str, selector: str = "span.bid-price") -> str:
    """
    Parses the HTML content and extracts the value based on the given selector.

    Args:
        html (str): The HTML content of the page.
        selector (str): The CSS selector to find the element (default: "span.bid-price").

    Returns:
        str: The extracted value, or None if not found.
    """
    # Parse HTML with BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # Find the first element matching the selector
    highlighted = soup.select_one(selector)
    if highlighted:
        # Extract and clean the text from the element
        value = highlighted.get_text(strip=True)
        logger.info(f"Value found: {value}")
        return value
    else:
        logger.warning(f"Value for selector '{selector}' not found")


async def process_url_copart(item_id: int, url: str):
    """
    Processes a single URL by fetching and parsing its content.

    Args:
        url (str): The URL of the page to process.

    Returns:
        tuple: (url, result) where result is the parsed value or None.
    """
    try:
        # Fetch the HTML content
        html = await fetch_page(url)
        # Parse the highlighted value
        result = await parse_highlighted_value(html)
        return item_id, result
    except Exception as e:
        logger.error(f"Error processing {url}: {str(e)}")
        return item_id, None


async def process_url_iaai(vehicle_id: int, vin: str):
    client = httpx.AsyncClient()
    result = await client.get(f"https://apicar/bid{vin}")
    return vehicle_id, result.json().get("current_bid", None)


async def get_current_bid(urls: list[dict]):
    """
    Main function to process multiple URLs concurrently.
    """

    # Create tasks for all URLs
    tasks_copart = [process_url_copart(url.get("id"), url.get("url")) for url in urls if "copart" in url.get("url")]
    tasks_iaai = [process_url_iaai(url.get("vin")) for url in urls if "iaai" in url.get("url")]
    # Run all tasks concurrently and wait for completion
    results = await asyncio.gather(*tasks_copart)
    results += await asyncio.gather(*tasks_iaai)

    return [{"id": item_id, "value": value} for item_id, value in results if item_id and value]
