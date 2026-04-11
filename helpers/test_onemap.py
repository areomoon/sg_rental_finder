#!/usr/bin/env python3
"""
Quick end-to-end test for the OneMap enricher.

Usage:
    cd /path/to/sg_rental_finder
    python helpers/test_onemap.py

Tests:
  1. Authenticate with OneMap API
  2. Geocode a known SG address → lat/lng
  3. Route from sample condo to City Hall MRT → commute minutes
"""
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from src.processor.enricher import OneMapEnricher


def run_test():
    print("=" * 60)
    print("OneMap Enricher — End-to-End Test")
    print("=" * 60)

    enricher = OneMapEnricher()

    # Step 1: Authenticate
    print("\n[1] Authenticating with OneMap API...")
    ok = enricher.authenticate()
    if not ok:
        print("    ❌ Authentication failed. Check ONEMAP_EMAIL + ONEMAP_PASSWORD in .env")
        sys.exit(1)
    print("    ✅ Authenticated")

    # Step 2: Geocode a well-known SG address
    test_address = "1 Raffles Place, Singapore 048616"
    print(f"\n[2] Geocoding: {test_address!r}")
    coords = enricher.search_address(test_address)
    if not coords:
        print("    ❌ Geocoding returned no results")
        sys.exit(1)
    lat, lng = coords
    print(f"    ✅ Result: lat={lat:.6f}, lng={lng:.6f}")
    # Expected: roughly (1.2840, 103.8514) for Raffles Place

    # Step 3: Route from Parc Sovereign (D02) to City Hall MRT
    # Parc Sovereign: 65 Tras Street ≈ (1.2745, 103.8434)
    condo_address = "65 Tras Street, Singapore 079004"
    print(f"\n[3] Geocoding condo: {condo_address!r}")
    condo_coords = enricher.search_address(condo_address)
    if not condo_coords:
        print("    ⚠️  Condo geocoding failed, using hardcoded coords")
        condo_coords = (1.2745, 103.8434)
    else:
        print(f"    ✅ Condo coords: lat={condo_coords[0]:.6f}, lng={condo_coords[1]:.6f}")

    # City Hall MRT station coords
    city_hall_lat, city_hall_lng = 1.2931, 103.8520
    print(f"\n[4] Routing: condo → City Hall MRT ({city_hall_lat}, {city_hall_lng})")
    result = enricher.calculate_route(
        condo_coords[0], condo_coords[1],
        city_hall_lat, city_hall_lng,
        route_type="pt",
    )
    if not result:
        print("    ❌ Routing returned no result")
        sys.exit(1)

    print(f"    ✅ Commute result:")
    print(f"       Total time:     {result['total_time_min']} minutes")
    print(f"       Walking dist:   {result['walk_distance_m']:.0f} m")
    print(f"       Transfers:      {result['transfers']}")

    # Step 5: Also route to Raffles Place MRT for comparison
    raffles_lat, raffles_lng = 1.2842, 103.8513
    print(f"\n[5] Routing: condo → Raffles Place MRT ({raffles_lat}, {raffles_lng})")
    result2 = enricher.calculate_route(
        condo_coords[0], condo_coords[1],
        raffles_lat, raffles_lng,
        route_type="pt",
    )
    if result2:
        print(f"    ✅ Commute result:")
        print(f"       Total time:     {result2['total_time_min']} minutes")
        print(f"       Walking dist:   {result2['walk_distance_m']:.0f} m")
        print(f"       Transfers:      {result2['transfers']}")
    else:
        print("    ⚠️  Raffles routing returned no result")

    print("\n" + "=" * 60)
    print("✅ All OneMap tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    run_test()
