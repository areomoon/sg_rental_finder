"""
Deduplication — remove duplicate listings across sources.

Strategy:
  1. URL exact match → keep higher-scored one
  2. Title + price similarity → keep first seen
  3. Development name + price (±5%) → likely same unit
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from ..collectors.base import BaseListing


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _prices_close(a: float, b: float, threshold: float = 0.05) -> bool:
    """Return True if prices are within threshold% of each other."""
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= threshold


def deduplicate(
    listings: list[BaseListing],
    title_sim_threshold: float = 0.85,
    url_only: bool = False,
) -> list[BaseListing]:
    """
    Deduplicate listings, preferring Gmail sources over scraper sources.

    Gmail-sourced listings are considered more reliable:
      gmail_propertyguru > gmail_99co > scraper_pg > scraper_99co
    """
    source_priority = {
        "gmail_propertyguru": 4,
        "gmail_99co": 3,
        "PropertyGuru (Scraper)": 2,
        "99.co (Scraper)": 1,
        "Gmail Alerts": 3,
    }

    def source_rank(listing: BaseListing) -> int:
        return source_priority.get(listing.source, 0)

    # Step 1: URL dedup — keep highest-priority source
    by_url: dict[str, BaseListing] = {}
    for listing in listings:
        url = listing.url.rstrip("/").lower().split("?")[0]
        if url not in by_url or source_rank(listing) > source_rank(by_url[url]):
            by_url[url] = listing

    unique = list(by_url.values())

    if url_only:
        return unique

    # Step 2: Title similarity + price proximity dedup
    kept: list[BaseListing] = []
    for candidate in unique:
        is_duplicate = False
        for existing in kept:
            sim = _title_similarity(candidate.title, existing.title)
            if sim >= title_sim_threshold and _prices_close(candidate.price_sgd, existing.price_sgd):
                # Duplicate — keep the higher-priority source
                if source_rank(candidate) > source_rank(existing):
                    kept.remove(existing)
                    kept.append(candidate)
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(candidate)

    return kept
