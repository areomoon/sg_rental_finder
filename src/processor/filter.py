"""
Listing filter — apply hard constraints and blacklist rules.

Hard constraints (any violation = exclude):
  - price_sgd > budget_max_sgd
  - bedrooms < bedroom_min OR bedrooms > bedroom_max
  - property_type not in allowed types
  - title/address contains blacklist keywords

Soft constraints (do not exclude, used by ranker):
  - sqft range
  - furnishing preference
"""
from __future__ import annotations

import re
from typing import Optional

from ..collectors.base import BaseListing

# Keywords that immediately disqualify a listing (HDB, room rental, etc.)
BLACKLIST_KEYWORDS = [
    r"\bHDB\b",
    r"\broom\s+rental\b",
    r"\bmaster\s+bedroom\b",
    r"\bcommon\s+room\b",
    r"\bsublet\b",
    r"\bshare\b",
    r"\broom\s+only\b",
    r"\bprivate\s+room\b",
]

BLACKLIST_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLACKLIST_KEYWORDS]

ALLOWED_PROPERTY_TYPES = {
    "condo", "condominium", "apartment", "apt",
    "service apartment", "serviced apartment", "",
}


def filter_listings(
    listings: list[BaseListing],
    budget_max_sgd: float = 3800,
    bedroom_min: int = 1,
    bedroom_max: int = 2,
    min_sqft: Optional[float] = None,
    max_sqft: Optional[float] = None,
) -> list[BaseListing]:
    """
    Apply hard filters to a list of listings.
    Returns only listings that pass ALL constraints.
    """
    filtered: list[BaseListing] = []
    for listing in listings:
        if _passes_all(listing, budget_max_sgd, bedroom_min, bedroom_max, min_sqft, max_sqft):
            filtered.append(listing)
    return filtered


def _passes_all(
    listing: BaseListing,
    budget_max_sgd: float,
    bedroom_min: int,
    bedroom_max: int,
    min_sqft: Optional[float],
    max_sqft: Optional[float],
) -> bool:
    """Return True if listing passes all hard filters."""

    # Price constraint
    if listing.price_sgd > budget_max_sgd:
        return False

    # Bedroom constraint (allow None — means unknown, don't exclude)
    if listing.bedrooms is not None:
        if listing.bedrooms < bedroom_min or listing.bedrooms > bedroom_max:
            return False

    # Property type constraint
    ptype = listing.property_type.lower().strip()
    if ptype and ptype not in ALLOWED_PROPERTY_TYPES:
        return False

    # Sqft constraints (skip if unknown)
    if listing.sqft is not None:
        if min_sqft and listing.sqft < min_sqft:
            return False
        if max_sqft and listing.sqft > max_sqft:
            return False

    # Blacklist keyword check
    check_text = f"{listing.title} {listing.address} {listing.development_name}"
    for pattern in BLACKLIST_PATTERNS:
        if pattern.search(check_text):
            return False

    return True
