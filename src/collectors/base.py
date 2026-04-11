from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BaseListing:
    """Standard data model for a rental listing from any source."""

    # Core fields (always populated)
    title: str
    url: str
    source: str                   # "gmail_propertyguru" | "gmail_99co" | "scraper_pg" | "scraper_99"
    price_sgd: float              # Monthly rent in SGD
    date_fetched: datetime

    # Property details (may be None if not parsed)
    address: str = ""
    district: str = ""            # D01, D02, etc.
    postal_code: str = ""
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    sqft: Optional[float] = None
    floor_level: str = ""         # "High", "Mid", "Low", or floor number
    furnishing: str = ""          # "Fully Furnished" | "Partially Furnished" | "Unfurnished"
    property_type: str = ""       # "Condo" | "Apartment" | "Service Apartment"
    development_name: str = ""

    # Availability
    available_from: Optional[str] = None  # ISO date string
    lease_min_months: Optional[int] = None

    # Media / listing quality
    photo_count: int = 0
    thumbnail_url: str = ""

    # Platform metadata
    listing_id: str = ""          # Platform-specific ID
    days_on_market: Optional[int] = None
    agent_name: str = ""
    agent_contact: str = ""

    # Enrichment (filled by enricher.py)
    lat: Optional[float] = None
    lng: Optional[float] = None
    nearest_mrt: str = ""
    nearest_mrt_walk_min: Optional[float] = None
    commute_funan_min: Optional[float] = None
    commute_raffles_min: Optional[float] = None
    price_per_sqft: Optional[float] = None

    # Scoring (filled by ranker.py)
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.sqft and self.sqft > 0:
            self.price_per_sqft = round(self.price_sgd / self.sqft, 2)

    def __hash__(self):
        return hash(self.url)

    def __eq__(self, other):
        if not isinstance(other, BaseListing):
            return False
        return self.url == other.url

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "price_sgd": self.price_sgd,
            "address": self.address,
            "district": self.district,
            "bedrooms": self.bedrooms,
            "sqft": self.sqft,
            "furnishing": self.furnishing,
            "property_type": self.property_type,
            "development_name": self.development_name,
            "available_from": self.available_from,
            "photo_count": self.photo_count,
            "days_on_market": self.days_on_market,
            "nearest_mrt": self.nearest_mrt,
            "nearest_mrt_walk_min": self.nearest_mrt_walk_min,
            "commute_funan_min": self.commute_funan_min,
            "commute_raffles_min": self.commute_raffles_min,
            "price_per_sqft": self.price_per_sqft,
            "score": self.score,
            "date_fetched": self.date_fetched.isoformat(),
            "thumbnail_url": self.thumbnail_url,
            "listing_id": self.listing_id,
        }


class BaseCollector(ABC):
    """Abstract base class for all rental listing collectors."""

    source_name: str = "unknown"
    timeout: int = 30

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    def collect(self) -> list[BaseListing]:
        """Collect listings and return a list of BaseListing objects."""
        ...

    def safe_collect(self) -> list[BaseListing]:
        """Wrap collect() with error handling — returns [] on failure."""
        try:
            listings = self.collect()
            print(f"[{self.source_name}] 抓取到 {len(listings)} 筆物件")
            return listings
        except Exception as e:
            print(f"[{self.source_name}] 抓取失敗：{e}")
            return []
