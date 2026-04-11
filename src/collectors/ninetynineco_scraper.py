"""
99.co Playwright Scraper — FALLBACK collector.

Only activated when Gmail alerts return < 10 listings.
Mirrors the same stealth/rate-limit approach as PropertyGuruScraper.
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .base import BaseListing, BaseCollector

NINETY_BASE_URL = "https://www.99.co/singapore/rent"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]


class NinetyNineCoScraper(BaseCollector):
    """
    Playwright-based fallback scraper for 99.co.

    Rate limits: max 50 listings per run, min 30s delay between pages.
    """

    source_name = "99.co (Scraper)"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.max_listings: int = self.config.get("max_listings_per_run", 50)
        self.min_delay: int = self.config.get("min_delay_seconds", 30)
        self.max_pages: int = self.config.get("max_pages", 3)
        self.headless: bool = self.config.get("headless", True)

    def collect(self) -> list[BaseListing]:
        """Scrape 99.co rental listings using Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[99.co Scraper] playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        try:
            from playwright_stealth import stealth_sync
            has_stealth = True
        except ImportError:
            has_stealth = False

        listings: list[BaseListing] = []

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
                print(f"[99.co Scraper] Fetching page {page_num}: {url}")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)

                    if self._is_blocked(page):
                        print("[99.co Scraper] Bot detection triggered — stopping scraper")
                        break

                    page.wait_for_selector('[class*="listing"], [class*="ListingCard"]', timeout=10000)
                    html = page.content()
                    page_listings = self._parse_listings_page(html)
                    listings.extend(page_listings)
                    print(f"[99.co Scraper] Page {page_num}: found {len(page_listings)} listings")

                    if page_num < self.max_pages:
                        delay = self.min_delay + random.randint(0, 15)
                        print(f"[99.co Scraper] Waiting {delay}s before next page...")
                        time.sleep(delay)

                except Exception as e:
                    print(f"[99.co Scraper] Page {page_num} error: {e}")
                    break

            browser.close()

        return listings[: self.max_listings]

    def _build_search_url(self, page: int = 1) -> str:
        """Build 99.co search URL with filters for central SG condos."""
        params = {
            "property_type": "condo",
            "listing_type": "rent",
            "minprice": 1500,
            "maxprice": 3800,
            "bedrooms": "1,2",
            "location": "city-hall",
            "page_num": page,
        }
        return f"{NINETY_BASE_URL}?{urlencode(params)}"

    def _is_blocked(self, page) -> bool:
        """Check if 99.co is showing a bot challenge."""
        content = page.content().lower()
        title = page.title().lower()
        return (
            "cloudflare" in content
            or "just a moment" in title
            or "access denied" in title
            or "robot" in content
        )

    def _parse_listings_page(self, html: str) -> list[BaseListing]:
        """Parse 99.co listing cards from page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings: list[BaseListing] = []

        # 99.co uses various class patterns across redesigns
        selectors = [
            '[class*="ListingCard"]',
            '[class*="listing-card"]',
            '[data-id]',
        ]
        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _parse_card(self, card) -> Optional[BaseListing]:
        """Parse a single 99.co listing card."""
        listing_id = card.get("data-id", "")

        # Title
        title_el = card.select_one("h3, h2, [class*='title'], [class*='name']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # URL
        link_el = card.select_one("a[href]")
        url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                url = f"https://www.99.co{href}"
            elif href.startswith("http"):
                url = href
        if not url and listing_id:
            url = f"https://www.99.co/singapore/rent/{listing_id}"
        if not url:
            return None

        # Price
        price_el = card.select_one("[class*='price'], [class*='Price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_sgd = self._parse_price(price_text)
        if price_sgd <= 0:
            return None

        # Address
        addr_el = card.select_one("[class*='address'], [class*='location'], [class*='Address']")
        address = addr_el.get_text(strip=True) if addr_el else ""

        # Bedrooms
        bed_el = card.select_one("[class*='bedroom'], [class*='Bedroom']")
        bedrooms = None
        if bed_el:
            try:
                bedrooms = int(re.search(r'\d+', bed_el.get_text()).group())
            except Exception:
                pass

        # Sqft
        sqft_el = card.select_one("[class*='size'], [class*='sqft'], [class*='area']")
        sqft = None
        if sqft_el:
            sqft = self._parse_sqft(sqft_el.get_text(strip=True))

        # Furnishing
        furnishing = ""
        full_text = card.get_text(" ").lower()
        if "fully furnished" in full_text:
            furnishing = "Fully Furnished"
        elif "partial" in full_text:
            furnishing = "Partially Furnished"
        elif "unfurnished" in full_text:
            furnishing = "Unfurnished"

        # Thumbnail
        img_el = card.select_one("img")
        thumbnail = ""
        if img_el:
            thumbnail = img_el.get("src") or img_el.get("data-src") or ""

        photo_count = len(card.select("img"))

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
        cleaned = re.sub(r"[^\d]", "", text.split("/")[0].split("per")[0])
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_sqft(text: str) -> Optional[float]:
        match = re.search(r"(\d[\d,]*)\s*(sqft|sq ft|sqm|sq m)", text.lower())
        if not match:
            return None
        num = float(match.group(1).replace(",", ""))
        if "sqm" in match.group(2) or "sq m" in match.group(2):
            num = round(num * 10.764, 0)
        return num
