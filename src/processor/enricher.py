"""
OneMap.sg Enricher — adds commute time and MRT distance to listings.

OneMap.sg is Singapore's official free government mapping API.
Register at https://www.onemap.gov.sg/apidocs/

Authentication:
  POST /api/auth/post/getToken with email + password
  Token expires in 3 days — cached in memory for the run.

APIs used:
  - GET /api/common/elastic/search     → geocode address → lat/lng
  - GET /api/public/routingsvc/route   → public transport routing
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from ..collectors.base import BaseListing

ONEMAP_BASE = "https://www.onemap.gov.sg/api"

# Work locations (lat/lng pre-computed to save API calls)
WORK_LOCATIONS = {
    "funan": {
        "name": "Patsnap (Funan)",
        "address": "107 North Bridge Road, Funan, Singapore 179105",
        "lat": 1.2912,
        "lng": 103.8484,
    },
    "raffles": {
        "name": "Raffles Place MRT",
        "address": "Raffles Place MRT, Singapore",
        "lat": 1.2842,
        "lng": 103.8513,
    },
}


class OneMapEnricher:
    """
    Enrich listings with commute times from OneMap.sg routing API.

    Uses public transport (PT) routing to estimate MRT + walk time
    from each listing to Funan (City Hall) and Raffles Place.
    """

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "sg-rental-finder/1.0",
            "Accept": "application/json",
        })

    def authenticate(self) -> bool:
        """Get OneMap API token using email + password from env."""
        email = os.environ.get("ONEMAP_EMAIL")
        password = os.environ.get("ONEMAP_PASSWORD")

        if not email or not password:
            print("[OneMap] WARNING: ONEMAP_EMAIL or ONEMAP_PASSWORD not set in .env")
            print("[OneMap] Register at https://www.onemap.gov.sg to get credentials")
            return False

        try:
            resp = self._session.post(
                f"{ONEMAP_BASE}/auth/post/getToken",
                json={"email": email, "password": password},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("access_token")
            # Token is valid for 3 days
            self._token_expiry = datetime.now(timezone.utc).timestamp() + (3 * 24 * 3600)
            print("[OneMap] Authentication successful")
            return True
        except Exception as e:
            print(f"[OneMap] Authentication failed: {e}")
            return False

    def _ensure_auth(self) -> bool:
        """Ensure we have a valid token."""
        if self._token and self._token_expiry:
            if datetime.now(timezone.utc).timestamp() < self._token_expiry - 300:
                return True
        return self.authenticate()

    def search_address(self, address: str) -> Optional[tuple[float, float]]:
        """
        Geocode an address string to (lat, lng) using OneMap search.
        Returns None if address not found.
        """
        if not self._ensure_auth():
            return None

        try:
            resp = self._session.get(
                f"{ONEMAP_BASE}/common/elastic/search",
                params={
                    "searchVal": address,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None
            first = results[0]
            lat = float(first.get("LATITUDE", 0))
            lng = float(first.get("LONGITUDE", 0))
            if lat and lng:
                return lat, lng
        except Exception as e:
            print(f"[OneMap] Geocode failed for '{address}': {e}")
        return None

    def calculate_route(
        self,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
        route_type: str = "pt",
    ) -> Optional[dict]:
        """
        Calculate route between two points.

        route_type: 'pt' (public transport), 'walk', 'drive', 'cycle'

        Returns dict with:
          - total_time_min: float
          - walk_distance_m: float
          - transfers: int (for PT)
        """
        if not self._ensure_auth():
            return None

        # OneMap routing uses a fixed date for transit schedules
        # Use a weekday at 8am for commute simulation
        date_str = "11-04-2026"  # Fixed Friday morning
        time_str = "0800"

        try:
            resp = self._session.get(
                f"{ONEMAP_BASE}/public/routingsvc/route",
                params={
                    "start": f"{start_lat},{start_lng}",
                    "end": f"{end_lat},{end_lng}",
                    "routeType": route_type,
                    "date": date_str,
                    "time": time_str,
                    "mode": "TRANSIT",
                    "maxWalkDistance": 1000,
                    "numItineraries": 1,
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            # Parse OneMap PT response
            plan = data.get("plan", {})
            itineraries = plan.get("itineraries", [])
            if not itineraries:
                return None

            best = itineraries[0]
            total_time_min = round(best.get("duration", 0) / 60, 1)
            walk_distance_m = sum(
                leg.get("distance", 0)
                for leg in best.get("legs", [])
                if leg.get("mode") == "WALK"
            )
            transfers = best.get("transfers", 0)

            return {
                "total_time_min": total_time_min,
                "walk_distance_m": round(walk_distance_m, 0),
                "transfers": transfers,
            }
        except Exception as e:
            print(f"[OneMap] Routing failed: {e}")
            return None

    def enrich_listing(self, listing: BaseListing) -> BaseListing:
        """
        Add commute times to a listing.

        If geocoding fails, uses a rough heuristic based on district.
        """
        # Geocode the listing address
        coords = None
        if listing.lat and listing.lng:
            coords = (listing.lat, listing.lng)
        elif listing.address or listing.postal_code:
            search_str = listing.postal_code or listing.address
            coords = self.search_address(search_str)

        if not coords:
            # Fallback: estimate from district
            listing.commute_funan_min = _estimate_commute_from_district(listing.district, "funan")
            listing.commute_raffles_min = _estimate_commute_from_district(listing.district, "raffles")
            return listing

        lat, lng = coords
        listing.lat = lat
        listing.lng = lng

        # Rate limit: small delay between API calls
        time.sleep(0.5)

        # Commute to Funan (City Hall)
        funan = WORK_LOCATIONS["funan"]
        result = self.calculate_route(lat, lng, funan["lat"], funan["lng"])
        if result:
            listing.commute_funan_min = result["total_time_min"]

        time.sleep(0.5)

        # Commute to Raffles Place
        raffles = WORK_LOCATIONS["raffles"]
        result = self.calculate_route(lat, lng, raffles["lat"], raffles["lng"])
        if result:
            listing.commute_raffles_min = result["total_time_min"]

        return listing

    def enrich_all(self, listings: list[BaseListing]) -> list[BaseListing]:
        """Enrich all listings with commute times."""
        if not self._ensure_auth():
            print("[OneMap] Skipping enrichment — authentication failed")
            print("[OneMap] Add ONEMAP_EMAIL and ONEMAP_PASSWORD to .env")
            return listings

        enriched: list[BaseListing] = []
        for i, listing in enumerate(listings):
            print(f"[OneMap] Enriching {i+1}/{len(listings)}: {listing.title[:50]}...")
            enriched.append(self.enrich_listing(listing))

        return enriched


def _estimate_commute_from_district(district: str, destination: str) -> Optional[float]:
    """
    Rough commute estimate (minutes) based on SG district code.
    Used as fallback when OneMap geocoding fails.
    """
    # District → approximate commute to City Hall (Funan area)
    funan_estimates = {
        "D01": 5,
        "D02": 8,
        "D06": 5,
        "D07": 8,
        "D08": 18,
        "D09": 12,
        "D10": 20,
        "D11": 15,
        "D12": 20,
        "D03": 15,
        "D04": 20,
        "D05": 25,
    }
    raffles_estimates = {k: max(v - 2, 3) for k, v in funan_estimates.items()}

    estimates = funan_estimates if destination == "funan" else raffles_estimates
    return estimates.get(district.upper())
