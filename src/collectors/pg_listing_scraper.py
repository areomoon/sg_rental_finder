"""
PropertyGuru Single Listing Scraper

Scrapes a single PropertyGuru listing page using Playwright.

Connection strategy (tried in order):
1. CDP — connect to the user's REAL Chrome browser on localhost:9222
         (real Chrome has Cloudflare cookies → bypasses CF challenge)
2. Headful Playwright — visible browser, waits up to 30 s for CF to resolve

Extraction priority for each field:
1. JSON-LD structured data  (most reliable — PG embeds schema.org data)
2. Next.js __NEXT_DATA__    (JavaScript page data object)
3. Meta og: tags            (og:title, og:image, og:description)
4. DOM selectors            (multiple fallback selectors per field)

To enable CDP mode, launch Chrome first (run once, then keep it open):
  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
      --remote-debugging-port=9222 --no-first-run --no-default-browser-check \\
      --user-data-dir=/tmp/chrome_debug &

Or use the helper:  python run.py --start-chrome-debug
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional


# Port where we try to find an existing Chrome instance
_CDP_PORT = 9222
# How long to wait for a Cloudflare challenge to auto-resolve (seconds)
_CF_WAIT_SECS = 35


class PGListingScraper:
    """Scrape a single PropertyGuru listing page using Playwright."""

    def __init__(self, headless: bool = False, cdp_port: int = _CDP_PORT):
        """
        headless=False  → visible browser (bypasses Cloudflare better).
        cdp_port        → port for existing Chrome CDP connection (0 = skip CDP).
        """
        self.headless = headless
        self.cdp_port = cdp_port
        self._playwright = None
        self._browser = None
        self._context = None
        self._via_cdp = False  # True when connected to user's real Chrome

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()

        # ── Try CDP first (real Chrome → CF cookies already present) ──────────
        if self.cdp_port:
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(
                    f"http://localhost:{self.cdp_port}",
                    timeout=3000,
                )
                # Use the first existing context/window instead of a fresh profile
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = self._browser.new_context()
                self._via_cdp = True
                print(f"[PGScraper] Connected to existing Chrome via CDP :{self.cdp_port}")
            except Exception as e:
                print(f"[PGScraper] CDP not available ({e}); launching headful browser.")
                self._browser = None
                self._via_cdp = False

        # ── Fall back to launching a fresh headful browser ────────────────────
        if not self._via_cdp:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
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

    # ── Public ────────────────────────────────────────────────────────────────

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
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                # Timeout on domcontentloaded can happen with CF challenge; continue anyway
                print(f"[PGScraper] goto warning (continuing): {e}")

            # Wait for CF challenge to resolve or real content to appear
            self._wait_for_real_page(page)

            # Final check: if still on CF page, raise with instructions
            if self._is_cloudflare_page(page):
                raise RuntimeError(
                    "Cloudflare challenge is still active.\n"
                    "Fix: Launch Chrome with remote debugging so we can use your real session:\n"
                    "  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                    "--remote-debugging-port=9222 --no-first-run "
                    "--user-data-dir=/tmp/chrome_debug &\n"
                    "Then visit PropertyGuru once in that window, and retry."
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
            # Don't close the page when using CDP (it's the user's real window)
            if not self._via_cdp:
                page.close()

        return result

    # ── Cloudflare helpers ────────────────────────────────────────────────────

    def _is_cloudflare_page(self, page) -> bool:
        """Return True if the page is still a Cloudflare challenge."""
        try:
            title = page.title().lower()
            if any(kw in title for kw in ("just a moment", "請稍候", "moment", "checking")):
                return True
            content = page.content()
            if "cf-browser-verification" in content or "challenge-platform" in content:
                return True
        except Exception:
            pass
        return False

    def _wait_for_real_page(self, page) -> None:
        """
        Poll until the page is no longer a Cloudflare challenge OR timeout.
        Prints a message so the user knows what's happening.
        """
        deadline = time.time() + _CF_WAIT_SECS
        first_check = True
        while time.time() < deadline:
            if not self._is_cloudflare_page(page):
                # Also wait a bit for JS to hydrate
                page.wait_for_timeout(2000)
                return
            if first_check:
                if self._via_cdp:
                    print(
                        "[PGScraper] Cloudflare challenge detected in your Chrome — "
                        "it should auto-resolve. Waiting up to 35 s..."
                    )
                else:
                    print(
                        "[PGScraper] Cloudflare challenge detected. "
                        "Waiting up to 35 s for auto-resolve. "
                        "If the browser opened, you may complete the challenge manually."
                    )
                first_check = False
            time.sleep(2)

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

        # Address — PG uses spatialCoverage.address or address directly
        if not result["address"]:
            addr = (
                data.get("spatialCoverage", {}).get("address")
                or data.get("address")
                or {}
            )
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

            # PG's current structure: pageProps.pageData.data.listingData
            # Fallback to older key paths for compatibility
            page_data = page_props.get("pageData", {}).get("data", {})
            listing = (
                page_props.get("listing")
                or page_props.get("listingData")
                or page_props.get("listingDetail")
                or page_data.get("listing")
                or page_data.get("listingDetail")
                or page_data.get("listingData")
                or {}
            )
            if not listing:
                return

            if not result["title"]:
                result["title"] = (
                    listing.get("listingName")
                    or listing.get("name")
                    or listing.get("title")
                    or ""
                )
            if result["price_sgd"] is None:
                price = _parse_price(
                    str(listing.get("price") or listing.get("askingPrice") or "")
                )
                if price:
                    result["price_sgd"] = price
            if not result["address"]:
                result["address"] = (
                    listing.get("address")
                    or listing.get("formattedAddress")
                    or listing.get("streetName")
                    or ""
                )
            if result["bedrooms"] is None:
                beds = listing.get("bedrooms") or listing.get("bedroom") or listing.get("noOfBedrooms")
                if beds is not None:
                    try:
                        result["bedrooms"] = int(beds)
                    except (ValueError, TypeError):
                        pass
            if result["size_sqft"] is None:
                size = (
                    listing.get("landArea")
                    or listing.get("floorArea")
                    or listing.get("size")
                    or listing.get("builtUpArea")
                )
                if size:
                    try:
                        sqft = float(str(size).replace(",", ""))
                        if sqft > 0:
                            result["size_sqft"] = sqft
                    except (ValueError, TypeError):
                        pass
            if not result["property_type"]:
                result["property_type"] = (
                    listing.get("propertyType")
                    or listing.get("category")
                    or listing.get("subtype")
                    or ""
                )
            if result["built_year"] is None:
                yr = listing.get("builtYear") or listing.get("completionYear")
                if yr:
                    try:
                        result["built_year"] = int(yr)
                    except (ValueError, TypeError):
                        pass
            # Agent info from Next.js data
            agent = listing.get("agentDetail") or listing.get("agent") or {}
            if isinstance(agent, dict):
                if not result["agent_name"]:
                    result["agent_name"] = agent.get("name") or agent.get("agentName") or ""
                if not result["agent_phone"]:
                    phone = agent.get("phone") or agent.get("mobile") or agent.get("contactNumber") or ""
                    if phone:
                        result["agent_phone"] = str(phone)
        except Exception:
            pass

    def _extract_meta_tags(self, page, result: dict) -> None:
        """Extract from HTML meta tags."""
        try:
            if not result["title"]:
                el = page.query_selector('meta[property="og:title"]')
                if el:
                    raw = el.get_attribute("content") or ""
                    # Strip " | PropertyGuru Singapore" suffix
                    result["title"] = re.sub(r'\s*\|\s*PropertyGuru.*$', '', raw).strip()

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
                '[class*="ListingName"]',
                '[class*="listing-name"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text and len(text) > 3 and "propertyguru" not in text.lower():
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
                '[class*="listing-price"]',
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
                '[class*="location"]',
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
                '[class*="listing-facts"] li, '
                '[class*="KeyInfo"] li, '
                '[class*="key-info"] li'
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

        # Agent name — also try extracting from page title (format: "..., by AGENT, ID")
        if not result["agent_name"]:
            try:
                title_text = page.title()
                m = re.search(r',\s*by\s+(.+?),\s*\d{6,}', title_text)
                if m:
                    result["agent_name"] = m.group(1).strip()
            except Exception:
                pass

        if not result["agent_name"]:
            for sel in [
                '[data-automation-id="agent-name"]',
                '[class*="agent-name"]',
                '[class*="agentName"]',
                '[class*="agent__name"]',
                '.agent-name',
                '[class*="AgentName"]',
                '[class*="agent-info"] [class*="name"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        result["agent_name"] = text
                        break

        # Agent phone — try WhatsApp link first (most reliable), then tel: links
        if not result["agent_phone"]:
            # WhatsApp: href like https://wa.me/6591234567 or https://api.whatsapp.com/send?phone=...
            for sel in [
                'a[href*="wa.me"]',
                'a[href*="whatsapp.com/send"]',
                'a[href*="whatsapp"]',
                '[class*="whatsapp"] a',
                'button[class*="whatsapp"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    href = el.get_attribute("href") or ""
                    phone = _extract_phone_from_whatsapp_url(href)
                    if phone:
                        result["agent_phone"] = phone
                        break

        if not result["agent_phone"]:
            # tel: links
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

        # Availability — dedicated selectors
        if not result["availability"]:
            for sel in [
                '[class*="availability"]',
                '[class*="Availability"]',
                '[data-automation-id="availability"]',
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        result["availability"] = text
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
        """Close browser and stop Playwright. In CDP mode, only close the page."""
        # Don't close the browser if we connected via CDP — it's the user's real Chrome!
        if self._via_cdp:
            try:
                if self._playwright:
                    self._playwright.stop()
            except Exception:
                pass
            return

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


def _extract_phone_from_whatsapp_url(href: str) -> Optional[str]:
    """
    Extract phone number from WhatsApp URL.
    Handles:
      https://wa.me/6591234567
      https://api.whatsapp.com/send?phone=6591234567
    Returns formatted as +65XXXXXXXX or raw SG number.
    """
    if not href:
        return None
    # wa.me/PHONE
    m = re.search(r'wa\.me/(\d+)', href)
    if m:
        return f"+{m.group(1)}"
    # whatsapp.com/send?phone=PHONE
    m = re.search(r'[?&]phone=(\d+)', href)
    if m:
        return f"+{m.group(1)}"
    return None
