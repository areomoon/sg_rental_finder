#!/usr/bin/env python3
"""
SG Rental Finder — Web UI

伴侶友善的網頁介面，支援批次貼入 PG 連結、評估、查看 shortlist。

啟動：
  python web.py

然後在瀏覽器開啟 http://localhost:5000
（同 WiFi 其他設備：http://你的電腦IP:5000）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__, template_folder="templates")
app.secret_key = os.urandom(24)

# ── In-memory job store for async imports ────────────────────────────────────
# {job_id: {"status": "processing"|"done"|"error", "total": N, "done": N,
#            "results": [...], "errors": [...]}}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

SHORTLIST_PATH = Path("data/shortlist.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_shortlist() -> dict:
    if SHORTLIST_PATH.exists():
        try:
            with open(SHORTLIST_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"shortlisted": []}


def _save_shortlist(data: dict) -> None:
    SHORTLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SHORTLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _entry_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _export_csv() -> Path:
    """Write shortlist.csv and return its path."""
    import csv
    data = _load_shortlist()
    entries = data.get("shortlisted", [])
    csv_path = SHORTLIST_PATH.parent / "shortlist.csv"
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
    return csv_path


def _process_url_in_thread(url: str, scraper, job_id: str) -> dict:
    """Process a single URL. Called from background thread."""
    from src.collectors.base import BaseListing
    from src.processor.enricher import OneMapEnricher
    from src.processor.ranker import rank_listings

    url = url.strip()
    raw = scraper.scrape_listing(url)

    if not raw.get("title") and not raw.get("price_sgd"):
        raise ValueError("Could not extract listing data (possible Cloudflare block)")

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

    enricher = OneMapEnricher()
    listing = enricher.enrich_listing(listing)

    ranked = rank_listings([listing])
    listing = ranked[0]

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

    # Save to shortlist (thread-safe via file read-modify-write)
    data = _load_shortlist()
    shortlisted = data.get("shortlisted", [])

    # Preserve notes/favorite if updating
    for existing in shortlisted:
        if existing.get("url") == url:
            entry["notes"] = existing.get("notes", "")
            entry["favorite"] = existing.get("favorite", False)
            break

    shortlisted = [e for e in shortlisted if e.get("url") != url]
    shortlisted.append(entry)
    shortlisted.sort(key=lambda x: x.get("score", 0), reverse=True)
    data["shortlisted"] = shortlisted
    data["updated_at"] = now.isoformat()
    _save_shortlist(data)
    _export_csv()

    return entry


def _run_import_job(job_id: str, urls: list[str]) -> None:
    """Background thread: process all URLs, update job status."""
    from src.collectors.pg_listing_scraper import PGListingScraper

    with _jobs_lock:
        _jobs[job_id]["status"] = "processing"
        _jobs[job_id]["total"] = len(urls)
        _jobs[job_id]["done_count"] = 0

    try:
        with PGListingScraper(headless=False) as scraper:
            for i, url in enumerate(urls):
                url = url.strip()
                if not url:
                    continue
                try:
                    entry = _process_url_in_thread(url, scraper, job_id)
                    with _jobs_lock:
                        _jobs[job_id]["results"].append({
                            "url": url,
                            "title": entry.get("title", ""),
                            "score": entry.get("score", 0),
                            "price_sgd": entry.get("price_sgd"),
                            "success": True,
                        })
                except Exception as e:
                    with _jobs_lock:
                        _jobs[job_id]["errors"].append({
                            "url": url,
                            "error": str(e),
                            "success": False,
                        })

                with _jobs_lock:
                    _jobs[job_id]["done_count"] = i + 1

                # Rate limit between URLs
                if i < len(urls) - 1:
                    time.sleep(5)

        with _jobs_lock:
            _jobs[job_id]["status"] = "done"

    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error_message"] = str(e)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("web_ui.html", page="import")


@app.route("/shortlist")
def shortlist_page():
    return render_template("web_ui.html", page="shortlist")


# ── API: Import ───────────────────────────────────────────────────────────────

@app.route("/api/import", methods=["POST"])
def api_import():
    """Start a batch import job. Returns job_id for polling."""
    body = request.get_json(silent=True) or {}
    urls_raw = body.get("urls", [])

    # Also accept newline-separated text
    if isinstance(urls_raw, str):
        urls_raw = [u.strip() for u in urls_raw.splitlines()]

    urls = [u.strip() for u in urls_raw if u.strip() and not u.strip().startswith("#")]

    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    job_id = hashlib.md5(f"{time.time()}".encode()).hexdigest()[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "total": len(urls),
            "done_count": 0,
            "results": [],
            "errors": [],
        }

    t = threading.Thread(target=_run_import_job, args=(job_id, urls), daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "total": len(urls)})


@app.route("/api/import/status/<job_id>")
def api_import_status(job_id: str):
    """Poll import job status."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ── API: Shortlist CRUD ───────────────────────────────────────────────────────

@app.route("/api/shortlist")
def api_get_shortlist():
    data = _load_shortlist()
    return jsonify(data.get("shortlisted", []))


@app.route("/api/shortlist/<entry_id>/note", methods=["POST"])
def api_update_note(entry_id: str):
    body = request.get_json(silent=True) or {}
    note = body.get("note", "")

    data = _load_shortlist()
    updated = False
    for entry in data.get("shortlisted", []):
        if entry.get("id") == entry_id:
            entry["notes"] = note
            updated = True
            break

    if not updated:
        return jsonify({"error": "Entry not found"}), 404

    _save_shortlist(data)
    return jsonify({"ok": True})


@app.route("/api/shortlist/<entry_id>/favorite", methods=["POST"])
def api_toggle_favorite(entry_id: str):
    data = _load_shortlist()
    updated = False
    new_value = False
    for entry in data.get("shortlisted", []):
        if entry.get("id") == entry_id:
            entry["favorite"] = not entry.get("favorite", False)
            new_value = entry["favorite"]
            updated = True
            break

    if not updated:
        return jsonify({"error": "Entry not found"}), 404

    _save_shortlist(data)
    return jsonify({"ok": True, "favorite": new_value})


@app.route("/api/shortlist/<entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id: str):
    data = _load_shortlist()
    before = len(data.get("shortlisted", []))
    data["shortlisted"] = [e for e in data.get("shortlisted", []) if e.get("id") != entry_id]

    if len(data["shortlisted"]) == before:
        return jsonify({"error": "Entry not found"}), 404

    _save_shortlist(data)
    _export_csv()
    return jsonify({"ok": True})


# ── API: Export ───────────────────────────────────────────────────────────────

@app.route("/api/export/csv")
def api_export_csv():
    csv_path = _export_csv()
    return send_file(
        csv_path,
        as_attachment=True,
        download_name="shortlist.csv",
        mimetype="text/csv",
    )


# ── API: Send digest ──────────────────────────────────────────────────────────

@app.route("/api/send-digest", methods=["POST"])
def api_send_digest():
    from datetime import datetime as _dt
    from src.collectors.base import BaseListing
    from src.messenger.email_sender import EmailSender
    from src.templates_builder import build_html_digest

    data = _load_shortlist()
    entries = data.get("shortlisted", [])

    if not entries:
        return jsonify({"error": "Shortlist is empty"}), 400

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
        return jsonify({"ok": True, "message": f"Sent {len(listings)} listings"})
    else:
        return jsonify({"error": "Failed to send email. Check GMAIL config in .env"}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket

    # Get local IP for LAN access display
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    print()
    print("🏠 SG Rental Finder — Web UI")
    print("=" * 45)
    print(f"→ 本機開啟：http://localhost:5000")
    print(f"→ 同 WiFi 其他設備：http://{local_ip}:5000")
    print()
    print("說明：匯入時會自動開啟 Chrome 瀏覽器視窗（用來繞過 Cloudflare）")
    print("按 Ctrl+C 停止")
    print("=" * 45)
    print()

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
