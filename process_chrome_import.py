"""Process Chrome-scraped PG listings: enrich with OneMap + rank + email"""
from __future__ import annotations
import json, os, re, smtplib, requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(override=True)

PG_FALLBACK_PHOTO = "https://www.propertyguru.com.sg/property-management-tool/images/PGSgLogo.png"

with open("data/chrome_import.json") as f:
    listings = json.load(f)
print(f"Loaded {len(listings)} listings")

# --- Photo fetch ---
print("\nFetching photos...")
for lst in listings:
    lst["photo_url"] = None
    if not lst.get("url"):
        continue
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp = requests.get(lst["url"], headers=headers, timeout=10)
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', resp.text)
        if not match:
            match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', resp.text)
        if match:
            lst["photo_url"] = match.group(1)
            print(f"  [{lst['title']}] photo: found")
        else:
            lst["photo_url"] = PG_FALLBACK_PHOTO
            print(f"  [{lst['title']}] photo: not found in og:image, using fallback")
    except Exception as e:
        lst["photo_url"] = PG_FALLBACK_PHOTO
        print(f"  [{lst['title']}] photo fetch error: {e}, using fallback")

# --- OneMap commute enrichment ---
FUNAN = (1.2916, 103.8501)
token = None
try:
    r = requests.post("https://www.onemap.gov.sg/api/auth/post/getToken",
        json={"email": os.getenv("ONEMAP_EMAIL"), "password": os.getenv("ONEMAP_PASSWORD")}, timeout=10)
    if r.ok and "access_token" in r.json():
        token = r.json()["access_token"]
        print("\nOneMap auth OK")
except Exception as e:
    print(f"\nOneMap auth failed: {e}")

for lst in listings:
    lst["commute_min"] = None
    if not token:
        continue
    try:
        sr = requests.get("https://www.onemap.gov.sg/api/common/elastic/search",
            params={"searchVal": lst["address"], "returnGeom": "Y", "getAddrDetails": "Y"}, timeout=10)
        results = sr.json().get("results", [])
        if not results:
            print(f"  [{lst['title']}] address not found")
            continue
        lat, lng = float(results[0]["LATITUDE"]), float(results[0]["LONGITUDE"])
        rr = requests.get("https://www.onemap.gov.sg/api/public/routingsvc/route",
            params={"start": f"{lat},{lng}", "end": f"{FUNAN[0]},{FUNAN[1]}",
                    "routeType": "pt", "date": "04-14-2026", "time": "08:30:00", "mode": "TRANSIT"},
            headers={"Authorization": token}, timeout=15)
        if rr.ok:
            itineraries = rr.json().get("plan", {}).get("itineraries", [])
            if itineraries:
                duration_sec = itineraries[0].get("duration", 0)
                lst["commute_min"] = round(duration_sec / 60) if duration_sec else None
                print(f"  [{lst['title']}] commute: {lst['commute_min']} min")
            else:
                print(f"  [{lst['title']}] no itineraries in response")
        else:
            print(f"  [{lst['title']}] route failed: {rr.status_code}")
    except Exception as e:
        print(f"  [{lst['title']}] error: {e}")

# --- Scoring ---
# Price (50 pts): S$2000 = 50, S$3800 = 0, linear
# Commute (30 pts): ≤10min = 30, linear decay; >40min = -20 hard penalty
# Size (10 pts): secondary
# Availability (10 pts): secondary
for lst in listings:
    score = 0

    # Price: 50 pts (PRIMARY)
    score += max(0, 50 - (lst["price_sgd"] - 2000) / 1800 * 50)

    # Commute: 30 pts with hard >40 min penalty
    commute = lst.get("commute_min")
    if commute is not None:
        if commute > 40:
            score -= 20  # hard penalty
        elif commute <= 10:
            score += 30
        else:
            score += max(0, 30 - (commute - 10) / 30 * 30)
    else:
        score += 5  # unknown = low neutral

    # Size: 10 pts
    score += min(10, max(0, (lst.get("size_sqft", 400) - 300) / 400 * 10))

    # Availability: 10 pts
    avail = lst.get("availability", "")
    if "Ready" in avail or "May" in avail or "Apr" in avail:
        score += 10
    elif avail:
        score += 5

    lst["score"] = round(max(0, score), 1)

listings.sort(key=lambda x: x["score"], reverse=True)

# --- Terminal table ---
print("\n" + "="*80)
print("SG Rental Candidates — Ranked by Score")
print("="*80)
for i, lst in enumerate(listings, 1):
    c = f"{lst['commute_min']}min" if lst["commute_min"] else "N/A"
    print(f"#{i} | Score: {lst['score']}/100 | {lst['title']}")
    print(f"   S${lst['price_sgd']}/mo | {lst['size_sqft']}sqft | {lst['bedrooms']}BR | Commute: {c}")
    print(f"   {lst['address']} | {lst['availability']}")
    print(f"   Photo: {'yes' if lst['photo_url'] and lst['photo_url'] != PG_FALLBACK_PHOTO else 'fallback'}")
    print(f"   {lst['url']}")
    print()

# --- Email ---
gmail_user = os.getenv("GMAIL_USER")
gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
recipient = os.getenv("EMAIL_RECIPIENT")

if gmail_user and gmail_pass and recipient:
    now = datetime.now().strftime("%Y-%m-%d")
    subject = f"SG Rental Digest — {now} ({len(listings)} candidates)"

    html = """
<html><body style='margin:0;padding:0;background:#f0f2f5;font-family:Arial,sans-serif'>
<div style='max-width:600px;margin:0 auto'>
"""
    # Header banner
    html += f"""
  <div style='background:linear-gradient(135deg,#1a5fa8,#2E75B6);padding:28px 24px;border-radius:0 0 12px 12px;margin-bottom:8px'>
    <h1 style='margin:0;color:#fff;font-size:24px;letter-spacing:0.5px'>SG Rental Digest</h1>
    <p style='margin:6px 0 0;color:#c8dff5;font-size:13px'>{now} &nbsp;|&nbsp; {len(listings)} candidates ranked by score</p>
  </div>
"""

    for i, lst in enumerate(listings, 1):
        c_label = f"🚇 {lst['commute_min']} min to Funan" if lst["commute_min"] else "🚇 Commute TBD"
        bg = "#fffbeb" if i == 1 else "#ffffff"
        border = "#f59e0b" if i == 1 else "#e5e7eb"
        badge_bg = "#f59e0b" if i == 1 else "#6b7280"
        badge_text = "TOP PICK" if i == 1 else f"#{i}"
        photo = lst.get("photo_url") or PG_FALLBACK_PHOTO

        html += f"""
  <div style='background:{bg};border:1.5px solid {border};border-radius:12px;margin:12px 8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.07)'>
    <img src='{photo}' style='width:100%;height:200px;object-fit:cover;display:block' />
    <div style='padding:16px'>
      <div style='display:flex;align-items:center;margin-bottom:8px'>
        <span style='background:{badge_bg};color:#fff;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:20px;letter-spacing:0.5px'>{badge_text}</span>
        <span style='margin-left:8px;color:#6b7280;font-size:12px'>Score: {lst['score']}/100</span>
      </div>
      <h3 style='margin:0 0 6px;font-size:17px;color:#111'>{lst['title']}</h3>
      <div style='font-size:22px;font-weight:bold;color:#16a34a;margin-bottom:8px'>S${lst['price_sgd']:,}<span style='font-size:13px;font-weight:normal;color:#6b7280'>/mo</span></div>
      <table style='border-collapse:collapse;width:100%;font-size:13px;color:#374151'>
        <tr><td style='padding:2px 0'>📐</td><td style='padding:2px 8px'>{lst['size_sqft']} sqft &nbsp;|&nbsp; {lst['bedrooms']}BR &nbsp;|&nbsp; {lst.get('property_type','')}</td></tr>
        <tr><td style='padding:2px 0'>📍</td><td style='padding:2px 8px'>{lst['address']}</td></tr>
        <tr><td style='padding:2px 0'>{c_label.split()[0]}</td><td style='padding:2px 8px'>{' '.join(c_label.split()[1:])}</td></tr>
        <tr><td style='padding:2px 0'>📅</td><td style='padding:2px 8px'>{lst['availability']}</td></tr>
      </table>
      <div style='margin-top:12px'>
        <a href='{lst['url']}' style='display:inline-block;background:#2E75B6;color:#fff;text-decoration:none;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:bold'>View on PropertyGuru &rarr;</a>
      </div>
    </div>
  </div>
"""

    html += """
  <div style='text-align:center;padding:16px 8px 24px'>
    <a href='https://www.propertyguru.com.sg' style='color:#2E75B6;font-size:13px;text-decoration:none'>View All on PropertyGuru</a>
    <p style='color:#9ca3af;font-size:11px;margin:8px 0 0'>Sent by sg_rental_finder &nbsp;|&nbsp; Commute data via OneMap</p>
  </div>
</div>
</body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, recipient, msg.as_string())
        print(f"Email sent to {recipient}")
    except Exception as e:
        print(f"Email failed: {e}")
