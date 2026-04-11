"""
Email HTML parser for PropertyGuru and 99.co email alerts.

PropertyGuru alert emails contain listing cards with:
  - Title / development name
  - Price
  - Address / district
  - Bedrooms, bathrooms
  - sqft (sometimes)
  - Photo
  - Link to listing

99.co alert emails have a similar card-based structure.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from ..collectors.base import BaseListing


def parse_propertyguru_email(html_body: str) -> list[BaseListing]:
    """
    Parse PropertyGuru alert email HTML into BaseListing objects.

    PropertyGuru emails contain listing cards inside <table> structures.
    Each card typically has:
      - An <a> link with href to the listing
      - Price text containing "S$X,XXX/mo"
      - Address / district text
      - Bedroom / bathroom icons with numbers
      - Optional sqft info
    """
    soup = BeautifulSoup(html_body, "html.parser")
    listings: list[BaseListing] = []

    # PropertyGuru listing cards are typically anchored by listing URLs
    listing_links = soup.find_all("a", href=re.compile(r"propertyguru\.com\.sg/listing/"))

    seen_urls: set[str] = set()

    for link in listing_links:
        try:
            url = link.get("href", "").strip()
            # Clean tracking parameters
            url = url.split("?")[0] if "?" in url else url
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Walk up the DOM to find the card container
            container = _find_card_container(link)
            if not container:
                container = link

            card_text = container.get_text(" ", strip=True)

            # Price
            price_sgd = _extract_price(card_text)
            if price_sgd <= 0:
                continue

            # Title — look for prominent text near the link
            title = _extract_title_pg(container, link)
            if not title:
                continue

            # Address
            address = _extract_address(card_text)

            # Bedrooms
            bedrooms = _extract_bedrooms(card_text)

            # Sqft
            sqft = _extract_sqft(card_text)

            # Furnishing
            furnishing = _extract_furnishing(card_text)

            # Property type
            property_type = _extract_property_type(card_text)

            # Photo
            img = container.find("img")
            thumbnail = img.get("src", "") if img else ""

            listings.append(BaseListing(
                title=title,
                url=url,
                source="gmail_propertyguru",
                price_sgd=price_sgd,
                date_fetched=datetime.now(timezone.utc),
                address=address,
                bedrooms=bedrooms,
                sqft=sqft,
                furnishing=furnishing,
                property_type=property_type or "Condo",
                thumbnail_url=thumbnail,
            ))
        except Exception:
            continue

    return listings


def parse_99co_email(html_body: str) -> list[BaseListing]:
    """
    Parse 99.co alert email HTML into BaseListing objects.

    99.co emails have listing cards with links to 99.co listing pages.
    """
    soup = BeautifulSoup(html_body, "html.parser")
    listings: list[BaseListing] = []

    listing_links = soup.find_all("a", href=re.compile(r"99\.co/singapore/(rent|property)/"))
    seen_urls: set[str] = set()

    for link in listing_links:
        try:
            url = link.get("href", "").strip().split("?")[0]
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            container = _find_card_container(link) or link
            card_text = container.get_text(" ", strip=True)

            price_sgd = _extract_price(card_text)
            if price_sgd <= 0:
                continue

            # Title
            title_el = container.find(["h2", "h3", "h4", "strong", "b"])
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            address = _extract_address(card_text)
            bedrooms = _extract_bedrooms(card_text)
            sqft = _extract_sqft(card_text)
            furnishing = _extract_furnishing(card_text)

            img = container.find("img")
            thumbnail = img.get("src", "") if img else ""

            listings.append(BaseListing(
                title=title,
                url=url,
                source="gmail_99co",
                price_sgd=price_sgd,
                date_fetched=datetime.now(timezone.utc),
                address=address,
                bedrooms=bedrooms,
                sqft=sqft,
                furnishing=furnishing,
                property_type="Condo",
                thumbnail_url=thumbnail,
            ))
        except Exception:
            continue

    return listings


# ── Private helpers ───────────────────────────────────────────────────────────

def _find_card_container(element, max_depth: int = 6):
    """Walk up the DOM tree to find a card-like container (<td> or <div>)."""
    node = element
    for _ in range(max_depth):
        parent = node.parent
        if parent is None:
            break
        tag = getattr(parent, "name", "")
        if tag in ("td", "div", "article", "li", "section"):
            # Check if this looks like a card (has enough text / links)
            if len(parent.get_text(strip=True)) > 50:
                return parent
        node = parent
    return None


def _extract_price(text: str) -> float:
    """Extract monthly rental price from text."""
    # Patterns: "S$3,500/mo", "$3500 per month", "SGD 3,500"
    patterns = [
        r"S?\$\s*([\d,]+)\s*/\s*mo",
        r"S?\$\s*([\d,]+)\s*per\s*month",
        r"SGD\s*([\d,]+)",
        r"S?\$\s*([\d,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                price = float(match.group(1).replace(",", ""))
                if 500 < price < 20000:  # Sanity check
                    return price
            except ValueError:
                continue
    return 0.0


def _extract_title_pg(container, link) -> str:
    """Extract listing title from PropertyGuru card."""
    # Try heading tags first
    for tag in ("h2", "h3", "h4", "strong"):
        el = container.find(tag)
        if el and len(el.get_text(strip=True)) > 5:
            return el.get_text(strip=True)

    # Try link text
    link_text = link.get_text(strip=True)
    if link_text and len(link_text) > 5:
        return link_text

    return ""


def _extract_address(text: str) -> str:
    """Extract address from card text using common SG patterns."""
    # Look for "D0X" district codes
    district_match = re.search(r"District\s+(\d{1,2})", text, re.IGNORECASE)
    if district_match:
        return f"District {district_match.group(1)}, Singapore"

    # Look for Singapore postal codes
    postal_match = re.search(r"Singapore\s+(\d{6})", text)
    if postal_match:
        return f"Singapore {postal_match.group(1)}"

    # Look for MRT station names
    mrt_match = re.search(r"near\s+([A-Z][a-zA-Z\s]+)\s+MRT", text)
    if mrt_match:
        return f"Near {mrt_match.group(1)} MRT"

    return ""


def _extract_bedrooms(text: str) -> Optional[int]:
    """Extract bedroom count from card text."""
    patterns = [
        r"(\d+)\s*bed",
        r"(\d+)\s*BR",
        r"(\d+)\s*Bed",
        r"(\d+)\s*room",
        r"Studio",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if "Studio" in pattern:
                return 1
            try:
                n = int(match.group(1))
                if 1 <= n <= 6:
                    return n
            except (ValueError, IndexError):
                continue
    return None


def _extract_sqft(text: str) -> Optional[float]:
    """Extract floor area from card text."""
    match = re.search(r"([\d,]+)\s*(sqft|sq\.?\s*ft|sqm|sq\.?\s*m)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        num = float(match.group(1).replace(",", ""))
        unit = match.group(2).lower().replace(" ", "")
        if "sqm" in unit or "sqm" == unit:
            num = round(num * 10.764, 0)
        if 100 < num < 10000:  # Sanity check
            return num
    except ValueError:
        pass
    return None


def _extract_furnishing(text: str) -> str:
    """Extract furnishing status from card text."""
    text_lower = text.lower()
    if "fully furnished" in text_lower:
        return "Fully Furnished"
    if "partial" in text_lower and "furnish" in text_lower:
        return "Partially Furnished"
    if "unfurnished" in text_lower or "un-furnished" in text_lower:
        return "Unfurnished"
    return ""


def _extract_property_type(text: str) -> str:
    """Extract property type from card text."""
    text_lower = text.lower()
    if "service apartment" in text_lower or "serviced apartment" in text_lower:
        return "Service Apartment"
    if "condo" in text_lower or "condominium" in text_lower:
        return "Condo"
    if "apartment" in text_lower or "apt" in text_lower:
        return "Apartment"
    return ""
