"""
Listing Ranker — score each listing on a 0–100 scale.

New scoring breakdown (updated priority order):
  - Price (raw SGD/mo):        50 pts  PRIMARY — S$2,000 = 50pts, S$3,800 = 0pts, linear
  - Commute to Funan (PT):     30 pts  ≤10min = 30, linear decay; >40min = −20 hard penalty
  - Size (sqft):               10 pts  secondary
  - Availability date:         10 pts  secondary (sooner = better)

Total: 100 pts (can go negative before clamping to 0 due to commute penalty)
"""
from __future__ import annotations

from typing import Optional

from ..collectors.base import BaseListing


def rank_listings(
    listings: list[BaseListing],
    weights: Optional[dict] = None,
) -> list[BaseListing]:
    """Score and sort listings. weights arg retained for API compatibility but ignored."""
    for listing in listings:
        score, breakdown = _score_listing(listing)
        listing.score = round(score, 1)
        listing.score_breakdown = breakdown

    return sorted(listings, key=lambda x: x.score, reverse=True)


def _score_listing(listing: BaseListing) -> tuple[float, dict]:
    """Compute weighted score and return (total_score, breakdown_dict)."""
    breakdown: dict[str, float] = {}

    # ── 1. Price: 50 pts (PRIMARY) ────────────────────────────────────────
    # S$2,000/mo → 50 pts | S$3,800/mo → 0 pts | linear
    price = listing.price_sgd or 0.0
    pts = max(0.0, 50.0 - (price - 2000.0) / 1800.0 * 50.0)
    breakdown["price"] = round(pts, 1)

    # ── 2. Commute to Funan: 30 pts with hard >40 min penalty ────────────
    # ≤10 min = 30 pts | linear decay to 30 min = 0 pts | >40 min = −20 pts
    commute = listing.commute_funan_min
    if commute is not None:
        if commute > 40:
            pts = -20.0  # hard penalty
        elif commute <= 10:
            pts = 30.0
        else:
            pts = max(0.0, 30.0 - (commute - 10.0) / 30.0 * 30.0)
    else:
        pts = 5.0  # unknown = low neutral (don't reward unknowns)
    breakdown["commute"] = round(pts, 1)

    # ── 3. Size: 10 pts (secondary) ───────────────────────────────────────
    sqft = listing.sqft or 400.0
    pts = min(10.0, max(0.0, (sqft - 300.0) / 400.0 * 10.0))
    breakdown["size"] = round(pts, 1)

    # ── 4. Availability: 10 pts (secondary) ──────────────────────────────
    # "Ready" or near-term months = full points; unknown = 5
    avail = listing.available_from or ""
    if any(kw in avail for kw in ("Ready", "May", "Apr", "Immediate", "Now")):
        pts = 10.0
    elif avail:
        pts = 5.0
    else:
        pts = 0.0
    breakdown["availability"] = round(pts, 1)

    total = sum(breakdown.values())
    return max(0.0, round(total, 1)), breakdown


def commute_warning(listing: BaseListing) -> bool:
    """Return True if commute exceeds the 40-minute hard cutoff."""
    return listing.commute_funan_min is not None and listing.commute_funan_min > 40


def format_score_bar(score: float, width: int = 10) -> str:
    """Return a visual score bar like '███████░░░ 72'."""
    filled = round(score / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {score:.0f}"
