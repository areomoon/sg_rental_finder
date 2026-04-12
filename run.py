#!/usr/bin/env python3
"""
SG Rental Finder — CLI Entry Point

用法：
  python run.py --now           立即執行（收集 + 篩選 + 排名 + 發送郵件）
  python run.py --test          測試模式（收集 + 排名，不發送）
  python run.py --preview       預覽摘要（顯示 top 10，不發送）
  python run.py --auth-gmail    只執行 Gmail OAuth 流程（初次設定用）
  python run.py --add URL       匯入單一 PG 物件連結
  python run.py --add-batch F   從檔案批次匯入 PG 物件連結
  python run.py --shortlist     顯示目前 shortlist
  python run.py --send-digest   將 shortlist 寄送為 email digest
  python run.py --web           啟動 Web UI（伴侶友善）
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(override=True)

import yaml


def load_settings() -> dict:
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Shortlist helpers ─────────────────────────────────────────────────────────

def _load_shortlist(path: Path) -> dict:
    """Load shortlist.json, returning a valid dict even if file is missing/empty."""
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"shortlisted": []}


def _save_shortlist(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _entry_id(url: str) -> str:
    """Short stable ID for a listing URL (used by web API)."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _export_shortlist_csv(entries: list, csv_path: Path) -> None:
    """Write shortlist entries to CSV for Google Sheets import."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "#", "Name", "Price (SGD)", "Size (sqft)", "Bedrooms",
        "Address", "Commute Funan (min)", "Commute Raffles (min)", "Score",
        "Type", "Built", "Availability", "PG URL", "Photo URL", "Agent", "Agent Phone",
        "Notes", "Favorite",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for i, entry in enumerate(entries, start=1):
            writer.writerow({
                "#": i,
                "Name": entry.get("title", ""),
                "Price (SGD)": entry.get("price_sgd", ""),
                "Size (sqft)": entry.get("size_sqft", ""),
                "Bedrooms": entry.get("bedrooms", ""),
                "Address": entry.get("address", ""),
                "Commute Funan (min)": entry.get("commute_funan_min", ""),
                "Commute Raffles (min)": entry.get("commute_raffles_min", ""),
                "Score": entry.get("score", ""),
                "Type": entry.get("property_type", ""),
                "Built": entry.get("built_year", ""),
                "Availability": entry.get("availability", ""),
                "PG URL": entry.get("url", ""),
                "Photo URL": entry.get("photo_url", ""),
                "Agent": entry.get("agent_name", ""),
                "Agent Phone": entry.get("agent_phone", ""),
                "Notes": entry.get("notes", ""),
                "Favorite": "❤" if entry.get("favorite") else "",
            })
    print(f"[CSV] Exported {len(entries)} entries → {csv_path}")


def _print_listing_summary(entry: dict) -> None:
    """Print a formatted summary of a single shortlist entry."""
    sep = "─" * 55
    print(f"\n{sep}")
    print(f"  {entry.get('title', 'Unknown')}")
    print(sep)
    price = entry.get("price_sgd")
    sqft = entry.get("size_sqft")
    if price:
        psf = f"  (S${price/sqft:.2f}/sqft)" if sqft else ""
        print(f"  Price  : S${price:,.0f}/mo{psf}")
    if sqft:
        print(f"  Size   : {sqft:.0f} sqft")
    if entry.get("bedrooms") is not None:
        print(f"  Beds   : {entry['bedrooms']}")
    if entry.get("address"):
        print(f"  Address: {entry['address']}")
    if entry.get("commute_funan_min") is not None:
        print(f"  Commute: {entry['commute_funan_min']:.0f} min to Funan", end="")
        if entry.get("commute_raffles_min") is not None:
            print(f" | {entry['commute_raffles_min']:.0f} min to Raffles", end="")
        print()
    if entry.get("score"):
        filled = round(entry["score"] / 100 * 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"  Score  : {bar} {entry['score']:.0f}/100")
    if entry.get("agent_name"):
        phone = entry.get("agent_phone", "")
        print(f"  Agent  : {entry['agent_name']}" + (f" | {phone}" if phone else ""))
    print(f"  URL    : {entry.get('url', '')}")
    print(sep)


def _process_single_url(
    url: str,
    settings: dict,
    scraper,
    shortlist_path: Path,
) -> tuple[bool, str, dict]:
    """
    Core logic: scrape → enrich → score → save.

    Returns (success, message, entry_dict).
    """
    from src.collectors.base import BaseListing
    from src.processor.enricher import OneMapEnricher
    from src.processor.ranker import rank_listings

    url = url.strip()

    # Load current shortlist
    shortlist_data = _load_shortlist(shortlist_path)
    shortlisted = shortlist_data.get("shortlisted", [])
    existing_urls = {e["url"] for e in shortlisted}

    is_update = url in existing_urls

    # Scrape
    try:
        raw = scraper.scrape_listing(url)
    except RuntimeError as e:
        return False, str(e), {}
    except Exception as e:
        return False, f"Scraping failed: {e}", {}

    if not raw.get("title") and not raw.get("price_sgd"):
        return False, (
            "Could not extract listing data. Page may be blocked by Cloudflare. "
            "Try again in a few minutes."
        ), {}

    # Build BaseListing
    now = datetime.now()
    listing = BaseListing(
        title=raw.get("title") or f"PropertyGuru ({url.split('/')[-1]})",
        url=url,
        source="manual_add",
        price_sgd=raw.get("price_sgd") or 0.0,
        date_fetched=now,
        address=raw.get("address") or "",
        district=raw.get("district") or "",
        bedrooms=raw.get("bedrooms"),
        sqft=raw.get("size_sqft"),
        property_type=raw.get("property_type") or "",
        available_from=raw.get("availability") or None,
        thumbnail_url=raw.get("photo_url") or "",
        photo_count=len(raw.get("photos") or []),
        agent_name=raw.get("agent_name") or "",
        agent_contact=raw.get("agent_phone") or "",
    )

    # Enrich with OneMap
    print(f"[OneMap] Getting commute times for: {listing.title[:50]}...")
    enricher = OneMapEnricher()
    listing = enricher.enrich_listing(listing)

    # Score
    ranked = rank_listings([listing])
    listing = ranked[0]

    # Build entry dict
    entry = {
        "id": _entry_id(url),
        "url": url,
        "title": listing.title,
        "price_sgd": listing.price_sgd,
        "size_sqft": listing.sqft,
        "bedrooms": listing.bedrooms,
        "address": listing.address,
        "district": listing.district,
        "property_type": listing.property_type,
        "built_year": raw.get("built_year"),
        "availability": raw.get("availability") or "",
        "agent_name": listing.agent_name,
        "agent_phone": listing.agent_contact,
        "photo_url": listing.thumbnail_url,
        "photos": raw.get("photos") or [],
        "commute_funan_min": listing.commute_funan_min,
        "commute_raffles_min": listing.commute_raffles_min,
        "price_per_sqft": listing.price_per_sqft,
        "score": listing.score,
        "score_breakdown": listing.score_breakdown,
        "added_date": now.isoformat(),
        "notes": "",
        "favorite": False,
    }

    # Preserve existing notes/favorite if updating
    if is_update:
        for existing in shortlisted:
            if existing.get("url") == url:
                entry["notes"] = existing.get("notes", "")
                entry["favorite"] = existing.get("favorite", False)
                break

    # Save
    shortlisted = [e for e in shortlisted if e.get("url") != url]
    shortlisted.append(entry)
    shortlisted.sort(key=lambda x: x.get("score", 0), reverse=True)
    shortlist_data["shortlisted"] = shortlisted
    shortlist_data["updated_at"] = now.isoformat()
    _save_shortlist(shortlist_path, shortlist_data)

    # Export CSV
    csv_path = shortlist_path.parent / "shortlist.csv"
    _export_shortlist_csv(shortlisted, csv_path)

    action = "Updated" if is_update else "Added"
    return True, f"{action}: {listing.title}", entry


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_now(settings: dict) -> None:
    from src.digest import run_digest
    run_digest(settings=settings, send=True, preview=False)


def cmd_test(settings: dict) -> None:
    from src.digest import run_digest
    print("🧪 測試模式：收集 + 排名，不發送郵件")
    run_digest(settings=settings, send=False, preview=True)


def cmd_preview(settings: dict) -> None:
    from src.digest import run_digest
    print("👁️  預覽模式：顯示 top 10，不發送")
    run_digest(settings=settings, send=False, preview=True)


def cmd_auth_gmail() -> None:
    """Trigger Gmail OAuth flow and save token.json."""
    print("🔐 Gmail OAuth 設定")
    print("=" * 50)
    from src.collectors.gmail_alerts import GmailAlertsCollector
    collector = GmailAlertsCollector()
    try:
        collector.authenticate()
        print("\n✅ Gmail OAuth 完成！token.json 已儲存至 config/")
        print("   之後執行 python run.py --test 測試完整流程")
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ OAuth 失敗：{e}")
        sys.exit(1)


def cmd_add(url: str, settings: dict) -> None:
    """Scrape a single PG listing URL and add to shortlist."""
    from src.collectors.pg_listing_scraper import PGListingScraper

    shortlist_path = Path("data/shortlist.json")
    print(f"\n[--add] Processing: {url.strip()}")

    with PGListingScraper(headless=False) as scraper:
        success, msg, entry = _process_single_url(url, settings, scraper, shortlist_path)

    if success:
        _print_listing_summary(entry)
        print(f"\n✅ {msg}")
        print(f"   data/shortlist.json and data/shortlist.csv updated.")
    else:
        print(f"\n❌ {msg}")


def cmd_add_batch(filepath: str, settings: dict) -> None:
    """Add multiple PG listing URLs from a text file."""
    from src.collectors.pg_listing_scraper import PGListingScraper

    urls_path = Path(filepath)
    if not urls_path.exists():
        print(f"[--add-batch] ERROR: File not found: {filepath}")
        sys.exit(1)

    urls = []
    with open(urls_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    if not urls:
        print("[--add-batch] No URLs found in file.")
        return

    shortlist_path = Path("data/shortlist.json")
    print(f"\n[--add-batch] Found {len(urls)} URLs to process\n")

    added = 0
    updated = 0
    failed = 0

    # Open a single browser for all URLs
    with PGListingScraper(headless=False) as scraper:
        # Pre-load existing URLs for dedup check
        shortlist_data = _load_shortlist(shortlist_path)
        existing_urls = {e["url"] for e in shortlist_data.get("shortlisted", [])}

        for i, url in enumerate(urls):
            print(f"\n[--add-batch] [{i+1}/{len(urls)}] {url}")
            is_update = url.strip() in existing_urls

            success, msg, entry = _process_single_url(url, settings, scraper, shortlist_path)

            if success:
                if is_update:
                    updated += 1
                else:
                    added += 1
                _print_listing_summary(entry)
                # Refresh existing URLs for next iteration
                shortlist_data = _load_shortlist(shortlist_path)
                existing_urls = {e["url"] for e in shortlist_data.get("shortlisted", [])}
            else:
                print(f"   FAILED: {msg}")
                failed += 1

            # Rate limit between requests (except after last)
            if i < len(urls) - 1:
                print(f"\n[--add-batch] Waiting 5s before next URL...")
                time.sleep(5)

    print(f"\n{'='*55}")
    print(f"[--add-batch] Done!")
    print(f"  Added   : {added}")
    print(f"  Updated : {updated}")
    print(f"  Failed  : {failed}")
    print(f"  Total   : {len(urls)}")
    print(f"{'='*55}")


def cmd_shortlist() -> None:
    """Display current shortlist in the terminal."""
    shortlist_path = Path("data/shortlist.json")
    data = _load_shortlist(shortlist_path)
    entries = data.get("shortlisted", [])

    if not entries:
        print("Shortlist is empty. Use --add <URL> to add listings.")
        return

    print(f"\nShortlist ({len(entries)} listings, sorted by score):\n")
    for i, entry in enumerate(entries, start=1):
        fav = " ❤" if entry.get("favorite") else ""
        commute = ""
        if entry.get("commute_funan_min") is not None:
            commute = f" | {entry['commute_funan_min']:.0f}min"
        price = f"S${entry.get('price_sgd', 0):,.0f}" if entry.get("price_sgd") else "?"
        score = f"{entry.get('score', 0):.0f}"
        print(f"  {i:2}. [{score:>3}/100]{fav}  {price}  {entry.get('title','')[:45]}{commute}")

    print()


def cmd_send_digest(settings: dict) -> None:
    """Send shortlist entries as an email digest."""
    from datetime import datetime as _dt
    from src.collectors.base import BaseListing
    from src.messenger.email_sender import EmailSender
    from src.templates_builder import build_html_digest

    shortlist_path = Path("data/shortlist.json")
    data = _load_shortlist(shortlist_path)
    entries = data.get("shortlisted", [])

    if not entries:
        print("Shortlist is empty — nothing to send.")
        return

    # Convert shortlist entries back to BaseListing objects for the template
    listings = []
    for e in entries:
        try:
            listing = BaseListing(
                title=e.get("title", ""),
                url=e.get("url", ""),
                source="manual_add",
                price_sgd=e.get("price_sgd") or 0.0,
                date_fetched=_dt.fromisoformat(e.get("added_date", _dt.now().isoformat())),
                address=e.get("address", ""),
                district=e.get("district", ""),
                bedrooms=e.get("bedrooms"),
                sqft=e.get("size_sqft"),
                property_type=e.get("property_type", ""),
                available_from=e.get("availability") or None,
                thumbnail_url=e.get("photo_url", ""),
                photo_count=len(e.get("photos") or []),
                agent_name=e.get("agent_name", ""),
                agent_contact=e.get("agent_phone", ""),
                commute_funan_min=e.get("commute_funan_min"),
                commute_raffles_min=e.get("commute_raffles_min"),
                score=e.get("score", 0.0),
                score_breakdown=e.get("score_breakdown", {}),
            )
            listings.append(listing)
        except Exception:
            continue

    subject, html = build_html_digest(listings, _dt.now())
    subject = f"[Shortlist] {subject}"

    sender = EmailSender()
    ok = sender.send(subject, html)
    if ok:
        print(f"✅ Digest sent ({len(listings)} listings)")
    else:
        print("❌ Failed to send digest. Check GMAIL_USER and GMAIL_APP_PASSWORD in .env")


def cmd_web() -> None:
    """Launch the Flask web UI."""
    import subprocess
    subprocess.run([sys.executable, "web.py"], check=False)


# ── Chrome debug helper ───────────────────────────────────────────────────────

def cmd_start_chrome_debug() -> None:
    """
    Launch Google Chrome with remote debugging on port 9222.

    This lets the PG scraper connect to your REAL Chrome session (with
    PropertyGuru cookies already set) to bypass Cloudflare challenges.

    After running this, open PropertyGuru in the Chrome window that appears,
    then use --add or the Web UI to import listings.
    """
    import subprocess
    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    chrome_bin = next((p for p in chrome_paths if Path(p).exists()), None)
    if not chrome_bin:
        print("❌ Google Chrome not found. Install it at https://www.google.com/chrome/")
        sys.exit(1)

    cmd = [
        chrome_bin,
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=/tmp/chrome_debug_sg_rental",
    ]
    print("🚀 Launching Chrome with remote debugging on port 9222...")
    print("   Visit PropertyGuru in the window that opens to get cookies,")
    print("   then run --add <URL> or use the Web UI to import listings.")
    print(f"\n   Command: {' '.join(cmd)}\n")
    subprocess.Popen(cmd)
    print("✅ Chrome launched. Keep this terminal open, then run your --add command.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SG Rental Finder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--now", action="store_true", help="立即執行（收集 + 發送郵件）")
    group.add_argument("--test", action="store_true", help="測試模式（不發送郵件）")
    group.add_argument("--preview", action="store_true", help="預覽 top 10（不發送）")
    group.add_argument("--auth-gmail", action="store_true", help="Gmail OAuth 初次設定")
    group.add_argument("--add", type=str, metavar="URL", help="匯入單一 PG 物件連結")
    group.add_argument("--add-batch", type=str, metavar="FILE", help="從檔案批次匯入 PG 物件連結")
    group.add_argument("--shortlist", action="store_true", help="顯示目前 shortlist")
    group.add_argument("--send-digest", action="store_true", help="將 shortlist 寄送為 email digest")
    group.add_argument("--web", action="store_true", help="啟動 Web UI（伴侶友善）")
    group.add_argument("--start-chrome-debug", action="store_true",
                       help="Launch Chrome with remote debugging (port 9222) to bypass Cloudflare")

    args = parser.parse_args()
    settings = load_settings()

    if args.now:
        cmd_now(settings)
    elif args.test:
        cmd_test(settings)
    elif args.preview:
        cmd_preview(settings)
    elif args.auth_gmail:
        cmd_auth_gmail()
    elif args.add:
        cmd_add(args.add, settings)
    elif args.add_batch:
        cmd_add_batch(args.add_batch, settings)
    elif args.shortlist:
        cmd_shortlist()
    elif args.send_digest:
        cmd_send_digest(settings)
    elif args.web:
        cmd_web()
    elif args.start_chrome_debug:
        cmd_start_chrome_debug()


if __name__ == "__main__":
    main()
