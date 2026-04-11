"""
Listing Ranker — score each listing on a 0–100 scale.

Scoring breakdown:
  - Price/sqft vs market median:      25 pts  (cheaper/sqft = better)
  - Commute to Funan (City Hall):     30 pts  (shorter = better)
  - Commute to Raffles Place:         15 pts  (shorter = better)
  - Days on market (freshness):       10 pts  (newer = better)
  - Photo count:                       5 pts  (more = more legit)
  - MRT walkability:                  15 pts  (within 10 min walk = best)

Total: 100 pts
"""
from __future__ import annotations

from typing import Optional

from ..collectors.base import BaseListing

# Market reference (S$/sqft/month, central SG condos 2025/2026)
MARKET_MEDIAN_PSF = 6.5   # S$6.50/sqft/month
GOOD_DEAL_PSF = 5.5
EXPENSIVE_PSF = 8.0


def rank_listings(
    listings: list[BaseListing],
    weights: Optional[dict] = None,
) -> list[BaseListing]:
    """
    Score and sort listings.

    weights: optional override dict with keys:
      price_per_sqft, commute_funan, commute_raffles,
      days_on_market, photo_count, mrt_distance
    """
    w = {
        "price_per_sqft": 25,
        "commute_funan": 30,
        "commute_raffles": 15,
        "days_on_market": 10,
        "photo_count": 5,
        "mrt_distance": 15,
    }
    if weights:
        w.update(weights)

    for listing in listings:
        score, breakdown = _score_listing(listing, w)
        listing.score = round(score, 1)
        listing.score_breakdown = breakdown

    return sorted(listings, key=lambda x: x.score, reverse=True)


def _score_listing(listing: BaseListing, w: dict) -> tuple[float, dict]:
    """Compute weighted score and return (total_score, breakdown_dict)."""
    breakdown: dict[str, float] = {}

    # ── 1. Price/sqft score (25 pts) ─────────────────────────────────────
    if listing.price_per_sqft and listing.price_per_sqft > 0:
        psf = listing.price_per_sqft
        if psf <= GOOD_DEAL_PSF:
            pts = w["price_per_sqft"]
        elif psf <= MARKET_MEDIAN_PSF:
            # Linear scale from 100% at GOOD_DEAL to 60% at MEDIAN
            ratio = (MARKET_MEDIAN_PSF - psf) / (MARKET_MEDIAN_PSF - GOOD_DEAL_PSF)
            pts = w["price_per_sqft"] * (0.6 + 0.4 * ratio)
        elif psf <= EXPENSIVE_PSF:
            # Linear scale from 60% at MEDIAN to 10% at EXPENSIVE
            ratio = (EXPENSIVE_PSF - psf) / (EXPENSIVE_PSF - MARKET_MEDIAN_PSF)
            pts = w["price_per_sqft"] * (0.1 + 0.5 * ratio)
        else:
            pts = 0
    else:
        # No sqft data — use raw price as proxy
        price = listing.price_sgd
        if price <= 2500:
            pts = w["price_per_sqft"] * 1.0
        elif price <= 3000:
            pts = w["price_per_sqft"] * 0.75
        elif price <= 3500:
            pts = w["price_per_sqft"] * 0.5
        elif price <= 3800:
            pts = w["price_per_sqft"] * 0.25
        else:
            pts = 0
    breakdown["price_per_sqft"] = round(pts, 1)

    # ── 2. Commute to Funan (30 pts) ─────────────────────────────────────
    if listing.commute_funan_min is not None:
        mins = listing.commute_funan_min
        if mins <= 5:
            pts = w["commute_funan"]
        elif mins <= 10:
            pts = w["commute_funan"] * 0.9
        elif mins <= 15:
            pts = w["commute_funan"] * 0.75
        elif mins <= 20:
            pts = w["commute_funan"] * 0.55
        elif mins <= 30:
            pts = w["commute_funan"] * 0.3
        else:
            pts = 0
    else:
        pts = w["commute_funan"] * 0.3  # Unknown = assume average
    breakdown["commute_funan"] = round(pts, 1)

    # ── 3. Commute to Raffles Place (15 pts) ─────────────────────────────
    if listing.commute_raffles_min is not None:
        mins = listing.commute_raffles_min
        if mins <= 5:
            pts = w["commute_raffles"]
        elif mins <= 10:
            pts = w["commute_raffles"] * 0.9
        elif mins <= 15:
            pts = w["commute_raffles"] * 0.75
        elif mins <= 20:
            pts = w["commute_raffles"] * 0.55
        elif mins <= 30:
            pts = w["commute_raffles"] * 0.3
        else:
            pts = 0
    else:
        pts = w["commute_raffles"] * 0.3
    breakdown["commute_raffles"] = round(pts, 1)

    # ── 4. Days on market (10 pts) ────────────────────────────────────────
    if listing.days_on_market is not None:
        dom = listing.days_on_market
        if dom <= 3:
            pts = w["days_on_market"]
        elif dom <= 7:
            pts = w["days_on_market"] * 0.8
        elif dom <= 14:
            pts = w["days_on_market"] * 0.6
        elif dom <= 30:
            pts = w["days_on_market"] * 0.4
        else:
            pts = w["days_on_market"] * 0.1
    else:
        pts = w["days_on_market"] * 0.5  # Unknown freshness = average
    breakdown["days_on_market"] = round(pts, 1)

    # ── 5. Photo count (5 pts) ────────────────────────────────────────────
    photos = listing.photo_count
    if photos >= 15:
        pts = w["photo_count"]
    elif photos >= 8:
        pts = w["photo_count"] * 0.8
    elif photos >= 4:
        pts = w["photo_count"] * 0.5
    elif photos >= 1:
        pts = w["photo_count"] * 0.2
    else:
        pts = 0
    breakdown["photo_count"] = round(pts, 1)

    # ── 6. MRT walkability (15 pts) ───────────────────────────────────────
    if listing.nearest_mrt_walk_min is not None:
        walk_min = listing.nearest_mrt_walk_min
        if walk_min <= 5:
            pts = w["mrt_distance"]
        elif walk_min <= 10:
            pts = w["mrt_distance"] * 0.75
        elif walk_min <= 15:
            pts = w["mrt_distance"] * 0.5
        elif walk_min <= 20:
            pts = w["mrt_distance"] * 0.25
        else:
            pts = 0
    else:
        pts = w["mrt_distance"] * 0.3  # Unknown = assume average
    breakdown["mrt_distance"] = round(pts, 1)

    total = sum(breakdown.values())
    return min(total, 100.0), breakdown


def format_score_bar(score: float, width: int = 10) -> str:
    """Return a visual score bar like '███████░░░ 72'."""
    filled = round(score / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {score:.0f}"
