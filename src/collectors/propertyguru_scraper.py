"""
PropertyGuru Playwright Scraper — FALLBACK collector.

Only activated when Gmail alerts return < 10 listings.
Uses playwright-stealth to reduce bot detection risk.

Rate limits:
  - Max 50 listings per run
  - Min 30s delay between page loads
  - Max 1 search per day per source
  - If Cloudflare blocks → log warning, return []
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .base import BaseListing, BaseCollector

# PropertyGuru rental search base URL
PG_BASE_URL = "https://www.propertyguru.com.sg/property-for-rent"

# D01, D02, D06, D07, D09, D10, D11 — central districts near City Hall
TARGET_DISTRICTS = ["D01", "D02", "D06", "D07", "D09", "D10", "D11", "D12"]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


class PropertyGuruScraper(BaseCollector):
    """
    Playwright-based fallback scraper for PropertyGuru.

    Respects rate limits: max 50 listings per run, min 30s delay.
    Falls back gracefully if Cloudflare blocks the request.
    """

    source_name = "PropertyGuru (Scraper)"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.max_listings: int = self.config.get("max_listings_per_run", 50)
        self.min_delay: int = self.config.get("min_delay_seconds", 30)
        self.max_pages: int = self.config.get("max_pages", 3)
        self.headless: bool = self.config.get("headless", True)

    def collect(self) -> list[BaseListing]:
        """Scrape PropertyGuru rental listings using Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[PropertyGuru Scraper] playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        try:
            from playwright_stealth import stealth_sync
            has_stealth = True
        except ImportError:
            has_stealth = False
            print("[PropertyGuru Scraper] playwright-stealth not installed (stealth mode disabled)")

        listings: list[BaseListing] = []
        search_url = self._build_search_url(page=1)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800},
                locale="en-SG",
                timezone_id="Asia/Singapore",
            )
            page = context.new_page()

            if has_stealth:
                stealth_sync(page)

            for page_num in range(1, self.max_pages + 1):
                if len(listings) >= self.max_listings:
                    break

                url = self._build_search_url(page=page_num)
                print(f"[PropertyGuru Scraper] Fetching page {page_num}: {url}")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)

                    # Check for Cloudflare block
                    if self._is_blocked(page):
                        print("[PropertyGuru Scraper] Cloudflare block detected — stopping scraper, falling back to Gmail-only mode")
                        break

                    # Wait for listing cards to load
                    page.wait_for_selector('[data-listing-id]', timeout=10000)
                    html = page.content()
                    page_listings = self._parse_listings_page(html)
                    listings.extend(page_listings)
                    print(f"[PropertyGuru Scraper] Page {page_num}: found {len(page_listings)} listings")

                    if page_num < self.max_pages:
                        delay = self.min_delay + random.randint(0, 15)
                        print(f"[PropertyGuru Scraper] Waiting {delay}s before next page...")
                        time.sleep(delay)

                except Exception as e:
                    print(f"[PropertyGuru Scraper] Page {page_num} error: {e}")
                    break

            browser.close()

        return listings[: self.max_listings]

    def _build_search_url(self, page: int = 1) -> str:
        """Build PropertyGuru search URL with filters."""
        params = {
            "listing_type": "rent",
            "property_type_code[]": ["CONDO", "APT", "SAPT"],
            "minprice": 1500,
            "maxprice": 3800,
            "beds[]": [1, 2],
            "freetext": "city hall",  # Broad central area search
        }
        if page > 1:
            return f"{PG_BASE_URL}/{page}?{urlencode(params, doseq=True)}"
        return f"{PG_BASE_URL}?{urlencode(params, doseq=True)}"

    def _is_blocked(self, page) -> bool:
        """Check if the page is showing a Cloudflare challenge."""
        title = page.title().lower()
        content = page.content().lower()
        return (
            "just a moment" in title
            or "cloudflare" in content
            or "checking your browser" in content
            or "challenge-platform" in content
        )

    def _parse_listings_page(self, html: str) -> list[BaseListing]:
        """Parse PropertyGuru listing cards from page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings: list[BaseListing] = []

        for card in soup.select('[data-listing-id]'):
            try:
                listing = self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _parse_card(self, card) -> Optional[BaseListing]:
        """Parse a single PropertyGuru listing card."""
        listing_id = card.get("data-listing-id", "")

        # Title / development name
        title_el = card.select_one("h3, .listing-description .title, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # URL
        link_el = card.select_one("a[href*='/property/']")
        url = ""
        if link_el:
            href = link_el.get("href", "")
            url = f"https://www.propertyguru.com.sg{href}" if href.startswith("/") else href
        if not url:
            url = f"https://www.propertyguru.com.sg/listing/{listing_id}"

        # Price
        price_el = card.select_one("[class*='price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_sgd = self._parse_price(price_text)
        if price_sgd <= 0:
            return None

        # Address
        addr_el = card.select_one("[class*='address'], [class*='location']")
        address = addr_el.get_text(strip=True) if addr_el else ""

        # Bedrooms
        bed_el = card.select_one("[class*='bedroom'], [data-beds]")
        bedrooms = None
        if bed_el:
            bed_text = bed_el.get("data-beds") or bed_el.get_text(strip=True)
            try:
                bedrooms = int(re.search(r'\d+', bed_text).group())
            except Exception:
                pass

        # Sqft
        sqft_el = card.select_one("[class*='sqft'], [class*='floor-area']")
        sqft = None
        if sqft_el:
            sqft_text = sqft_el.get_text(strip=True)
            sqft = self._parse_sqft(sqft_text)

        # Photo count
        photo_count = len(card.select("img[src*='cdn']"))

        # Furnishing
        furnishing = ""
        for tag in card.select("[class*='tag'], [class*='badge']"):
            text = tag.get_text(strip=True).lower()
            if "fully" in text:
                furnishing = "Fully Furnished"
            elif "partial" in text:
                furnishing = "Partially Furnished"
            elif "unfurnished" in text:
                furnishing = "Unfurnished"

        # Thumbnail
        img_el = card.select_one("img[src*='cdn'], img[data-src*='cdn']")
        thumbnail = ""
        if img_el:
            thumbnail = img_el.get("src") or img_el.get("data-src") or ""

        return BaseListing(
            title=title,
            url=url,
            source=self.source_name,
            price_sgd=price_sgd,
            date_fetched=datetime.now(timezone.utc),
            address=address,
            bedrooms=bedrooms,
            sqft=sqft,
            furnishing=furnishing,
            property_type="Condo",
            listing_id=listing_id,
            photo_count=photo_count,
            thumbnail_url=thumbnail,
        )

    @staticmethod
    def _parse_price(text: str) -> float:
        """Extract numeric price from text like 'S$3,500/mo'."""
        import re
        cleaned = re.sub(r"[^\d]", "", text.split("/")[0].split("per")[0])
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_sqft(text: str) -> Optional[float]:
        """Extract sqft from text like '700 sqft' or '65 sqm'."""
        import re
        match = re.search(r"(\d[\d,]*)\s*(sqft|sq ft|sqm|sq m)", text.lower())
        if not match:
            return None
        num = float(match.group(1).replace(",", ""))
        if "sqm" in match.group(2) or "sq m" in match.group(2):
            num = round(num * 10.764, 0)  # convert sqm to sqft
        return num


import re  # noqa: E402 — needed in static methods above
