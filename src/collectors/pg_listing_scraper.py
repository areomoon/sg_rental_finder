"""
PropertyGuru Single Listing Scraper

Scrapes a single PropertyGuru listing page using Playwright in headful mode
(visible browser to better bypass Cloudflare).

Extraction priority for each field:
1. JSON-LD structured data  (most reliable — PG embeds schema.org data)
2. Next.js __NEXT_DATA__    (JavaScript page data object)
3. Meta og: tags            (og:title, og:image, og:description)
4. DOM selectors            (multiple fallback selectors per field)
"""
from __future__ import annotations

import json
import re
from typing import Optional


class PGListingScraper:
    """Scrape a single PropertyGuru listing page using Playwright."""

    def __init__(self, headless: bool = False):
        """headless=False uses visible browser (bypasses Cloudflare better)."""
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        return self

    def __exit__(self, *args):
        self.close()

    def scrape_listing(self, url: str) -> dict:
        """
        Navigate to PG listing URL, extract all info.

        Returns dict with:
          title, price_sgd, size_sqft, bedrooms, address, district,
          property_type, built_year, availability, agent_name, agent_phone,
          photo_url, photos, url
        """
        page = self._context.new_page()
        result: dict = {
            "url": url,
            "title": "",
            "price_sgd": None,
            "size_sqft": None,
            "bedrooms": None,
            "address": "",
            "district": "",
            "property_type": "",
            "built_year": None,
            "availability": "",
            "agent_name": "",
            "agent_phone": "",
            "photo_url": "",
            "photos": [],
        }

        try:
            print(f"[PGScraper] Navigating to {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for any of the key content selectors
            try:
                page.wait_for_selector(
                    "[data-automation-id='listing-price'], "
                    ".listing-price, h1, [class*='price']",
                    timeout=15000,
                )
            except Exception:
                pass  # Continue anyway — content may still be present

            # Let JS render
            page.wait_for_timeout(2000)

            # Check for Cloudflare block
            content = page.content()
            page_title = page.title().lower()
            if "just a moment" in page_title or "cf-browser-verification" in content.lower():
                raise RuntimeError(
                    "Cloudflare challenge detected. "
                    "The browser window opened — complete the challenge manually, "
                    "then retry in a few minutes."
                )

            # ── 1. JSON-LD (most reliable) ────────────────────────────────
            json_ld = self._extract_json_ld(page)
            if json_ld:
                self._parse_json_ld(json_ld, result)

            # ── 2. Next.js __NEXT_DATA__ ──────────────────────────────────
            self._extract_via_js(page, result)

            # ── 3. Meta og: tags ──────────────────────────────────────────
            self._extract_meta_tags(page, result)

            # ── 4. DOM selectors (fill remaining gaps) ────────────────────
            self._extract_from_dom(page, result)

            # ── Post-process: infer district from address ─────────────────
            if not result["district"] and result["address"]:
                m = re.search(r'\bD(\d{2})\b', result["address"], re.IGNORECASE)
                if m:
                    result["district"] = f"D{m.group(1).zfill(2)}"

        except RuntimeError:
            raise
        except Exception as e:
            print(f"[PGScraper] Error scraping {url}: {e}")
        finally:
            page.close()

        return result

    # ── Private: extraction methods ───────────────────────────────────────────

    def _extract_json_ld(self, page) -> Optional[dict]:
        """Extract first relevant JSON-LD block from the page."""
        RELEVANT_TYPES = {
            "Apartment", "ApartmentComplex", "RealEstateListing",
            "Residence", "House", "Place", "Product",
        }
        try:
            scripts = page.query_selector_all('script[type="application/ld+json"]')
            for script in scripts:
                try:
                    data = json.loads(script.inner_text())
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict) and item.get("@type") in RELEVANT_TYPES:
                            return item
                    # Return any dict if nothing matched type check
                    if items and isinstance(items[0], dict):
                        return items[0]
                except (json.JSONDecodeError, Exception):
                    continue
        except Exception:
            pass
        return None

    def _parse_json_ld(self, data: dict, result: dict) -> None:
        """Parse JSON-LD structured data into result."""
        if not result["title"]:
            result["title"] = data.get("name") or data.get("headline") or ""

        # Address
        if not result["address"]:
            addr = data.get("address", {})
            if isinstance(addr, dict):
                parts = [
                    addr.get("streetAddress", ""),
                    addr.get("addressLocality", ""),
                    addr.get("postalCode", ""),
                ]
                result["address"] = ", ".join(p for p in parts if p)
            elif isinstance(addr, str):
                result["address"] = addr

        # Price (from offers block)
        if result["price_sgd"] is None:
            offers = data.get("offers", {})
            if isinstance(offers, dict):
                price = _parse_price(str(offers.get("price", "")))
                if price:
                    result["price_sgd"] = price

        # Photo
        if not result["photo_url"]:
            image = data.get("image", "")
            if isinstance(image, list) and image:
                result["photo_url"] = image[0] if isinstance(image[0], str) else ""
                result["photos"] = [i for i in image if isinstance(i, str)]
            elif isinstance(image, str) and image:
                result["photo_url"] = image

        # Bedrooms from numberOfRooms
        if result["bedrooms"] is None:
            num = data.get("numberOfRooms")
            if num is not None:
                try:
                    result["bedrooms"] = int(num)
                except (ValueError, TypeError):
                    pass

        # Floor size
        if result["size_sqft"] is None:
            floor_size = data.get("floorSize", {})
            if isinstance(floor_size, dict):
                val = floor_size.get("value")
                unit = floor_size.get("unitCode", "FTK")
                if val:
                    sqft_val = float(val)
                    if unit in ("MTK", "m2", "SMT"):
                        sqft_val *= 10.764
                    result["size_sqft"] = round(sqft_val, 0)

        # Description fallback for bedrooms/sqft
        description = data.get("description", "")
        if description:
            if result["bedrooms"] is None:
                result["bedrooms"] = _parse_bedrooms(description)
            if result["size_sqft"] is None:
                result["size_sqft"] = _parse_sqft(description)

    def _extract_via_js(self, page, result: dict) -> None:
        """Extract from Next.js __NEXT_DATA__ window object."""
        try:
            raw = page.evaluate("""
                () => {
                    if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);
                    if (window.__NUXT__) return JSON.stringify(window.__NUXT__);
                    return null;
                }
            """)
            if not raw:
                return
            data = json.loads(raw)
            self._parse_nextjs_data(data, result)
        except Exception:
            pass

    def _parse_nextjs_data(self, data: dict, result: dict) -> None:
        """Parse Next.js __NEXT_DATA__ for listing fields."""
        try:
            props = data.get("props", {})
            page_props = props.get("pageProps", {})

            listing = (
                page_props.get("listing")
                or page_props.get("listingData")
                or page_props.get("data", {}).get("listing")
                or {}
            )
            if not listing:
                return

            if not result["title"]:
                result["title"] = listing.get("listingName") or listing.get("title") or ""
            if result["price_sgd"] is None:
                price = _parse_price(str(listing.get("price") or listing.get("askingPrice") or ""))
                if price:
                    result["price_sgd"] = price
            if not result["address"]:
                result["address"] = listing.get("address") or listing.get("formattedAddress") or ""
            if result["bedrooms"] is None:
                beds = listing.get("bedrooms") or listing.get("bedroom")
                if beds is not None:
                    try:
                        result["bedrooms"] = int(beds)
                    except (ValueError, TypeError):
                        pass
            if result["size_sqft"] is None:
                size = listing.get("landArea") or listing.get("floorArea") or listing.get("size")
                if size:
                    try:
                        sqft = float(str(size).replace(",", ""))
                        if sqft > 0:
                            result["size_sqft"] = sqft
                    except (ValueError, TypeError):
                        pass
            if not result["property_type"]:
                result["property_type"] = listing.get("propertyType") or listing.get("category") or ""
            if result["built_year"] is None:
                yr = listing.get("builtYear") or listing.get("completionYear")
                if yr:
                    try:
                        result["built_year"] = int(yr)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

    def _extract_meta_tags(self, page, result: dict) -> None:
        """Extract from HTML meta tags."""
        try:
            if not result["title"]:
                el = page.query_selector('meta[property="og:title"]')
                if el:
                    result["title"] = el.get_attribute("content") or ""

            if not result["photo_url"]:
                el = page.query_selector('meta[property="og:image"]')
                if el:
                    result["photo_url"] = el.get_attribute("content") or ""

            el = page.query_selector('meta[property="og:description"]')
            if el:
                desc = el.get_attribute("content") or ""
                if result["price_sgd"] is None:
                    result["price_sgd"] = _parse_price(desc)
                if result["size_sqft"] is None:
                    result["size_sqft"] = _parse_sqft(desc)
                if result["bedrooms"] is None:
                    result["bedrooms"] = _parse_bedrooms(desc)
        except Exception:
            pass

    def _extract_from_dom(self, page, result: dict) -> None:
        """Fill remaining gaps using DOM selectors."""

        # Title
        if not result["title"]:
            for sel in [
                "h1",
                '[data-automation-id="listing-title"]',
                '[class*="title__"]',
                ".listing-title",
                "h1.title",
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text and len(text) > 3:
                        result["title"] = text
                        break

        # Price
        if result["price_sgd"] is None:
            for sel in [
                '[data-automation-id="listing-price"]',
                '[class*="price__"]',
                ".listing-price",
                '[class*="Price"]',
                'span[class*="price"]',
                'div[class*="price"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    price = _parse_price(el.inner_text())
                    if price:
                        result["price_sgd"] = price
                        break

        # Address
        if not result["address"]:
            for sel in [
                '[data-automation-id="listing-address"]',
                '[itemprop="streetAddress"]',
                '[class*="address__"]',
                ".listing-address",
                '[class*="Address"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        result["address"] = text
                        break

        # Details table — scrape all key/value pairs at once
        try:
            items = page.query_selector_all(
                '[data-automation-id="listing-details"] li, '
                '[class*="listing-details"] li, '
                '[class*="info-table"] tr, '
                '[class*="InfoTable"] tr, '
                '[class*="details__"] li, '
                '[class*="listingFacts"] li, '
                '[class*="listing-facts"] li'
            )
            for item in items:
                raw = item.inner_text().strip()
                text = raw.lower()

                if result["bedrooms"] is None and re.search(r'bed', text):
                    result["bedrooms"] = _parse_bedrooms(text)

                if result["size_sqft"] is None and re.search(r'sq\s?f?t|sqm|floor', text):
                    result["size_sqft"] = _parse_sqft(text)

                if not result["property_type"] and re.search(r'condo|apartment|hdb|service', text):
                    for pt in ["Condominium", "Condo", "Apartment", "HDB", "Service Apartment", "Landed"]:
                        if pt.lower() in text:
                            result["property_type"] = pt
                            break

                if not result["availability"] and re.search(r'avail', text):
                    result["availability"] = raw

                if result["built_year"] is None and re.search(r'built|complet|year', text):
                    m = re.search(r'\b(19|20)\d{2}\b', raw)
                    if m:
                        result["built_year"] = int(m.group())

                if not result["district"] and "district" in text:
                    m = re.search(r'\bD?(\d{2})\b', raw, re.IGNORECASE)
                    if m:
                        result["district"] = f"D{m.group(1).zfill(2)}"
        except Exception:
            pass

        # Bedrooms — dedicated selectors
        if result["bedrooms"] is None:
            for sel in [
                '[data-automation-id="bedrooms"]',
                '[class*="bedroom"]',
                '[class*="Bedroom"]',
                'li[class*="bed"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    beds = _parse_bedrooms(el.inner_text())
                    if beds is not None:
                        result["bedrooms"] = beds
                        break

        # Size — dedicated selectors
        if result["size_sqft"] is None:
            for sel in [
                '[data-automation-id="area"]',
                '[class*="floor-size"]',
                '[class*="floorSize"]',
                '[class*="area"]',
                'li[class*="size"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    sqft = _parse_sqft(el.inner_text())
                    if sqft:
                        result["size_sqft"] = sqft
                        break

        # Agent name
        if not result["agent_name"]:
            for sel in [
                '[data-automation-id="agent-name"]',
                '[class*="agent-name"]',
                '[class*="agentName"]',
                '[class*="agent__name"]',
                '.agent-name',
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        result["agent_name"] = text
                        break

        # Agent phone
        if not result["agent_phone"]:
            for sel in [
                'a[href^="tel:"]',
                '[data-automation-id="agent-phone"]',
                '[class*="agent-phone"]',
                '[class*="agentPhone"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    href = el.get_attribute("href") or ""
                    text = href.replace("tel:", "").strip() or el.inner_text().strip()
                    if text:
                        result["agent_phone"] = text
                        break

        # Photos — gallery images
        if not result["photos"]:
            try:
                photo_els = page.query_selector_all(
                    '[class*="gallery"] img, '
                    '[class*="Gallery"] img, '
                    '[class*="photo"] img, '
                    '[class*="carousel"] img, '
                    '[class*="slider"] img, '
                    'picture source'
                )
                seen: set[str] = set()
                photos: list[str] = []
                for el in photo_els:
                    src = el.get_attribute("src") or el.get_attribute("srcset") or ""
                    if not src or "http" not in src:
                        continue
                    # Take first URL from srcset
                    if "," in src:
                        src = src.split(",")[0].split()[0]
                    # Skip placeholder / tiny images
                    if any(skip in src for skip in ["placeholder", "loading", "blur", "1x1", "pixel"]):
                        continue
                    if src not in seen:
                        seen.add(src)
                        photos.append(src)
                result["photos"] = photos[:20]
                if photos and not result["photo_url"]:
                    result["photo_url"] = photos[0]
            except Exception:
                pass

    def close(self):
        """Close browser and stop Playwright."""
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[float]:
    """Extract SGD monthly rent from text like 'S$3,500/mo', 'SGD3500'."""
    if not text:
        return None
    text = str(text)
    m = re.search(r'(?:S\$|SGD\s*|\$)\s*([\d,]+)', text, re.IGNORECASE)
    if not m:
        # Try bare number if text looks like just a price
        m = re.search(r'^([\d,]+)$', text.strip())
    if m:
        try:
            price = float(m.group(1).replace(",", ""))
            if 500 <= price <= 50000:  # Sanity check for SG rent range
                return price
        except ValueError:
            pass
    return None


def _parse_bedrooms(text: str) -> Optional[int]:
    """Extract bedroom count from text like '2 Bedrooms', '2BR', 'Studio'."""
    if not text:
        return None
    text = str(text).lower()
    if "studio" in text:
        return 0
    m = re.search(r'(\d+)\s*(?:bedroom|bed\b|br\b|-room)', text)
    if m:
        beds = int(m.group(1))
        if 0 <= beds <= 10:
            return beds
    return None


def _parse_sqft(text: str) -> Optional[float]:
    """Extract square footage from text. Converts sqm → sqft if needed."""
    if not text:
        return None
    text = str(text)
    # sq ft
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft)', text, re.IGNORECASE)
    if m:
        sqft = float(m.group(1).replace(",", ""))
        if 100 <= sqft <= 10000:
            return sqft
    # sq m
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*(?:sq\.?\s*m\b|sqm\b|m²)', text, re.IGNORECASE)
    if m:
        sqft = float(m.group(1).replace(",", "")) * 10.764
        if 100 <= sqft <= 10000:
            return round(sqft, 0)
    return None
