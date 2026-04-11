"""
Main orchestrator for SG Rental Finder digest.

Pipeline:
  1. Gmail alerts (PRIMARY)
  2. Playwright scraper fallback (if < threshold listings)
  3. Deduplicate
  4. Filter (price, bedrooms, blacklist)
  5. Enrich (OneMap commute times)
  6. Rank (score 0-100)
  7. Filter already-sent
  8. Generate HTML email
  9. Send email
  10. Save seen listings
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .collectors.base import BaseListing
from .collectors.gmail_alerts import GmailAlertsCollector
from .collectors.propertyguru_scraper import PropertyGuruScraper
from .collectors.ninetynineco_scraper import NinetyNineCoScraper
from .processor.dedup import deduplicate
from .processor.filter import filter_listings
from .processor.enricher import OneMapEnricher
from .processor.ranker import rank_listings
from .messenger.email_sender import EmailSender

SEEN_FILE = Path(__file__).parent.parent / "data" / "seen_listings.json"
SEEN_FILE.parent.mkdir(exist_ok=True)

SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("seen_urls", []))
    except Exception:
        return set()


def save_seen(new_urls: set[str]) -> None:
    existing = load_seen()
    all_urls = list(existing | new_urls)
    all_urls = all_urls[-2000:]  # Keep last 2000 listings
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"seen_urls": all_urls, "updated_at": datetime.now().isoformat()},
            f, ensure_ascii=False, indent=2,
        )


def run_digest(
    settings: Optional[dict] = None,
    send: bool = True,
    preview: bool = False,
    top_n: Optional[int] = None,
) -> str:
    """
    Run the full rental digest pipeline.

    Args:
        settings: Loaded config dict (auto-loaded if None)
        send: Whether to actually send the email
        preview: Print digest to console
        top_n: Override max listings in digest

    Returns:
        Summary string of what was found/sent
    """
    cfg = settings or load_settings()
    now = datetime.now(timezone.utc)

    print(f"\n🏠 SG Rental Finder — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    search_cfg = cfg.get("search", {})
    collector_cfg = cfg.get("collectors", {})
    digest_cfg = cfg.get("digest", {})
    ranker_cfg = cfg.get("ranker", {})

    budget_max = search_cfg.get("budget_max_sgd", 3800)
    bed_min = search_cfg.get("bedroom_min", 1)
    bed_max = search_cfg.get("bedroom_max", 2)
    gmail_threshold = digest_cfg.get("new_listings_threshold", 10)
    n = top_n or digest_cfg.get("top_n", 10)

    # ── Step 1: Gmail collector (PRIMARY) ─────────────────────────────────
    print("\n📧 Step 1: Fetching Gmail alerts...")
    gmail_collector = GmailAlertsCollector(collector_cfg.get("gmail", {}))
    listings = gmail_collector.safe_collect()
    print(f"  Gmail: {len(listings)} listings")

    # ── Step 2: Scraper fallback ──────────────────────────────────────────
    if len(listings) < gmail_threshold:
        print(f"\n🔍 Step 2: Gmail returned {len(listings)} (< {gmail_threshold}), activating scraper fallback...")
        scraper_cfg = collector_cfg.get("scraper", {})

        pg_scraper = PropertyGuruScraper(scraper_cfg)
        pg_listings = pg_scraper.safe_collect()
        listings.extend(pg_listings)
        print(f"  PropertyGuru scraper: {len(pg_listings)} listings")

        ninety_scraper = NinetyNineCoScraper(scraper_cfg)
        ninety_listings = ninety_scraper.safe_collect()
        listings.extend(ninety_listings)
        print(f"  99.co scraper: {len(ninety_listings)} listings")
    else:
        print(f"  Gmail has enough listings — scraper not needed")

    if not listings:
        print("\n⚠️  No listings collected from any source")
        return "No listings found"

    # ── Step 3: Dedup ─────────────────────────────────────────────────────
    print(f"\n🔄 Step 3: Deduplication ({len(listings)} listings)...")
    listings = deduplicate(listings)
    print(f"  After dedup: {len(listings)} unique listings")

    # ── Step 4: Filter ────────────────────────────────────────────────────
    print(f"\n🎯 Step 4: Filtering (budget ≤S${budget_max}, {bed_min}-{bed_max}BR)...")
    listings = filter_listings(
        listings,
        budget_max_sgd=budget_max,
        bedroom_min=bed_min,
        bedroom_max=bed_max,
    )
    print(f"  After filter: {len(listings)} listings")

    # ── Step 5: Enrich ────────────────────────────────────────────────────
    print(f"\n🗺️  Step 5: OneMap enrichment (commute times)...")
    enricher = OneMapEnricher()
    listings = enricher.enrich_all(listings)

    # ── Step 6: Rank ──────────────────────────────────────────────────────
    print(f"\n📊 Step 6: Ranking...")
    ranker_weights = {
        "price_per_sqft": ranker_cfg.get("price_per_sqft_weight", 25),
        "commute_funan": ranker_cfg.get("commute_funan_weight", 30),
        "commute_raffles": ranker_cfg.get("commute_raffles_weight", 15),
        "days_on_market": ranker_cfg.get("days_on_market_weight", 10),
        "photo_count": ranker_cfg.get("photo_count_weight", 5),
        "mrt_distance": ranker_cfg.get("mrt_distance_weight", 15),
    }
    ranked = rank_listings(listings, weights=ranker_weights)
    if ranked:
        print(f"  Top score: {ranked[0].score:.1f} — {ranked[0].title[:50]}")

    # ── Step 7: Filter already-seen ───────────────────────────────────────
    print(f"\n👁️  Step 7: Filtering seen listings...")
    seen = load_seen()
    new_listings = [l for l in ranked if l.url not in seen]
    print(f"  New listings: {len(new_listings)} (of {len(ranked)} ranked)")

    if not new_listings:
        print("\n✅ No new listings to send")
        return "No new listings since last digest"

    top_listings = new_listings[:n]

    # ── Step 8: Generate HTML ─────────────────────────────────────────────
    print(f"\n📝 Step 8: Generating HTML digest ({len(top_listings)} listings)...")
    from .templates_builder import build_html_digest
    subject, html = build_html_digest(top_listings, now)

    if preview:
        print("\n" + "=" * 60)
        print("PREVIEW MODE — email not sent")
        print(f"Subject: {subject}")
        print("=" * 60)
        for i, l in enumerate(top_listings, 1):
            print(f"{i:2d}. [{l.score:5.1f}] S${l.price_sgd:,} — {l.title[:60]}")
            if l.commute_funan_min:
                print(f"     Funan: {l.commute_funan_min:.0f}min | Raffles: {l.commute_raffles_min or '?':.0f}min")
            print(f"     {l.url}")
        print("=" * 60)

    # ── Step 9: Send email ────────────────────────────────────────────────
    if send:
        print(f"\n📬 Step 9: Sending digest email...")
        try:
            sender = EmailSender()
            success = sender.send(subject, html)
        except ValueError as e:
            print(f"  [EmailSender] Config error: {e}")
            success = False

        if not success:
            print("  Email send failed — listings NOT marked as seen")
            return f"Failed to send digest ({len(top_listings)} listings found)"
    else:
        print("  [Skip] send=False")

    # ── Step 10: Save seen ────────────────────────────────────────────────
    new_urls = {l.url for l in top_listings}
    save_seen(new_urls)
    print(f"\n💾 Step 10: Saved {len(new_urls)} URLs to seen_listings.json")
    print("\n✅ Done!")

    return f"Sent {len(top_listings)} new listings"


# ─────────────────────────────────────────────────────────────────────────────
# DEMO MODE — hardcoded sample listings, no Gmail OAuth required
# ─────────────────────────────────────────────────────────────────────────────

def _demo_listings() -> list[BaseListing]:
    """Return 7 realistic SG condo listings for demo/testing."""
    now = datetime.now(timezone.utc)
    return [
        BaseListing(
            title="The Sail @ Marina Bay #18-05",
            url="https://www.propertyguru.com.sg/listing/demo-101",
            source="demo",
            price_sgd=3600,
            date_fetched=now,
            address="2 Marina Boulevard, Singapore 018987",
            district="D01",
            bedrooms=2,
            sqft=710,
            furnishing="Fully Furnished",
            property_type="Condo",
            development_name="The Sail @ Marina Bay",
            nearest_mrt="Raffles Place",
            nearest_mrt_walk_min=6.0,
            photo_count=18,
            days_on_market=2,
            thumbnail_url="",
        ),
        BaseListing(
            title="Parc Sovereign #07-12 (1+Study)",
            url="https://www.propertyguru.com.sg/listing/demo-102",
            source="demo",
            price_sgd=3200,
            date_fetched=now,
            address="65 Tras Street, Singapore 079004",
            district="D02",
            bedrooms=1,
            sqft=560,
            furnishing="Fully Furnished",
            property_type="Condo",
            development_name="Parc Sovereign",
            nearest_mrt="Tanjong Pagar",
            nearest_mrt_walk_min=5.0,
            photo_count=14,
            days_on_market=5,
            thumbnail_url="",
        ),
        BaseListing(
            title="The Clift #15-08",
            url="https://www.propertyguru.com.sg/listing/demo-103",
            source="demo",
            price_sgd=2900,
            date_fetched=now,
            address="5 McCallum Street, Singapore 069954",
            district="D02",
            bedrooms=1,
            sqft=495,
            furnishing="Partially Furnished",
            property_type="Condo",
            development_name="The Clift",
            nearest_mrt="Tanjong Pagar",
            nearest_mrt_walk_min=4.0,
            photo_count=10,
            days_on_market=8,
            thumbnail_url="",
        ),
        BaseListing(
            title="Concourse Skyline #12-03 (2BR)",
            url="https://www.propertyguru.com.sg/listing/demo-104",
            source="demo",
            price_sgd=3500,
            date_fetched=now,
            address="302 Beach Road, Singapore 199600",
            district="D07",
            bedrooms=2,
            sqft=780,
            furnishing="Fully Furnished",
            property_type="Condo",
            development_name="Concourse Skyline",
            nearest_mrt="Bugis",
            nearest_mrt_walk_min=7.0,
            photo_count=16,
            days_on_market=3,
            thumbnail_url="",
        ),
        BaseListing(
            title="Citylights #05-18 (1+Study)",
            url="https://www.propertyguru.com.sg/listing/demo-105",
            source="demo",
            price_sgd=3100,
            date_fetched=now,
            address="78 Jellicoe Road, Singapore 208737",
            district="D07",
            bedrooms=1,
            sqft=614,
            furnishing="Fully Furnished",
            property_type="Condo",
            development_name="Citylights",
            nearest_mrt="Lavender",
            nearest_mrt_walk_min=6.0,
            photo_count=11,
            days_on_market=10,
            thumbnail_url="",
        ),
        BaseListing(
            title="Sophia Hills #08-11 (2BR)",
            url="https://www.propertyguru.com.sg/listing/demo-106",
            source="demo",
            price_sgd=3750,
            date_fetched=now,
            address="1 Mount Sophia, Singapore 228459",
            district="D09",
            bedrooms=2,
            sqft=807,
            furnishing="Fully Furnished",
            property_type="Condo",
            development_name="Sophia Hills",
            nearest_mrt="Dhoby Ghaut",
            nearest_mrt_walk_min=8.0,
            photo_count=20,
            days_on_market=1,
            thumbnail_url="",
        ),
        BaseListing(
            title="Novena Regency #10-06 (1BR)",
            url="https://www.propertyguru.com.sg/listing/demo-107",
            source="demo",
            price_sgd=2800,
            date_fetched=now,
            address="24 Novena Rise, Singapore 297944",
            district="D11",
            bedrooms=1,
            sqft=520,
            furnishing="Partially Furnished",
            property_type="Condo",
            development_name="Novena Regency",
            nearest_mrt="Novena",
            nearest_mrt_walk_min=10.0,
            photo_count=9,
            days_on_market=14,
            thumbnail_url="",
        ),
    ]


def run_demo(
    settings: Optional[dict] = None,
    send: bool = True,
    top_n: Optional[int] = None,
) -> str:
    """
    Demo mode: skip Gmail/scrapers, use hardcoded sample listings.
    Runs the full filter → enrich → rank → email pipeline.
    No Gmail OAuth required.
    """
    cfg = settings or load_settings()
    now = datetime.now(timezone.utc)

    print(f"\n🎭 SG Rental Finder DEMO — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    print("  Using hardcoded sample listings (no Gmail OAuth needed)")
    print("=" * 60)

    search_cfg = cfg.get("search", {})
    digest_cfg = cfg.get("digest", {})
    ranker_cfg = cfg.get("ranker", {})

    budget_max = search_cfg.get("budget_max_sgd", 3800)
    bed_min = search_cfg.get("bedroom_min", 1)
    bed_max = search_cfg.get("bedroom_max", 2)
    n = top_n or digest_cfg.get("top_n", 10)

    listings = _demo_listings()
    print(f"\n📋 Demo: {len(listings)} sample listings loaded")

    # Filter
    print(f"\n🎯 Filtering (budget ≤S${budget_max}, {bed_min}-{bed_max}BR)...")
    listings = filter_listings(listings, budget_max_sgd=budget_max, bedroom_min=bed_min, bedroom_max=bed_max)
    print(f"  After filter: {len(listings)} listings")

    # Enrich
    print(f"\n🗺️  OneMap enrichment (commute times)...")
    enricher = OneMapEnricher()
    listings = enricher.enrich_all(listings)

    # Rank
    print(f"\n📊 Ranking...")
    ranker_weights = {
        "price_per_sqft": ranker_cfg.get("price_per_sqft_weight", 25),
        "commute_funan": ranker_cfg.get("commute_funan_weight", 30),
        "commute_raffles": ranker_cfg.get("commute_raffles_weight", 15),
        "days_on_market": ranker_cfg.get("days_on_market_weight", 10),
        "photo_count": ranker_cfg.get("photo_count_weight", 5),
        "mrt_distance": ranker_cfg.get("mrt_distance_weight", 15),
    }
    ranked = rank_listings(listings, weights=ranker_weights)
    if ranked:
        print(f"  Top score: {ranked[0].score:.1f} — {ranked[0].title[:50]}")

    print("\n📊 Rankings:")
    for i, l in enumerate(ranked, 1):
        funan = f"{l.commute_funan_min:.0f}min" if l.commute_funan_min else "?"
        raffles = f"{l.commute_raffles_min:.0f}min" if l.commute_raffles_min else "?"
        print(f"  {i:2d}. [{l.score:5.1f}] S${l.price_sgd:,} {l.sqft or '?'}sqft — {l.title[:45]}")
        print(f"       Funan: {funan} | Raffles: {raffles} | {l.nearest_mrt}")

    top_listings = ranked[:n]

    # Generate HTML
    print(f"\n📝 Generating HTML digest ({len(top_listings)} listings)...")
    from .templates_builder import build_html_digest
    subject, html = build_html_digest(top_listings, now)
    subject = f"[DEMO] {subject}"

    # Send email
    if send:
        print(f"\n📬 Sending demo digest email...")
        try:
            sender = EmailSender()
            success = sender.send(subject, html)
        except ValueError as e:
            print(f"  [EmailSender] Config error: {e}")
            success = False

        if success:
            print("  ✅ Demo email sent!")
        else:
            print("  ❌ Email send failed")
    else:
        print("  [Skip] send=False")

    print("\n✅ Demo complete!")
    return f"Demo: {len(top_listings)} sample listings processed"
