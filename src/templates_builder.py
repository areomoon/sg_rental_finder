"""
Build HTML digest email from ranked listings.
Loads the Jinja2-free template from templates/daily_rental_digest.html
and substitutes listing cards.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .collectors.base import BaseListing
from .processor.ranker import format_score_bar

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "daily_rental_digest.html"


def build_html_digest(
    listings: list[BaseListing],
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """
    Build the email subject and HTML body.

    Returns:
        (subject_str, html_str)
    """
    date_str = (now or datetime.now()).strftime("%Y-%m-%d")
    dow = (now or datetime.now()).strftime("%A")
    subject = f"🏠 SG Rental Digest {date_str} ({dow}) — {len(listings)} new listings"

    top = listings[0] if listings else None
    html = _render(listings, top, now or datetime.now())
    return subject, html


def _render(listings: list[BaseListing], top: Optional[BaseListing], now: datetime) -> str:
    """Render the full HTML email string."""
    date_str = now.strftime("%B %d, %Y")

    top_card = _render_top_card(top) if top else "<p>No featured listing today.</p>"
    listing_cards = "\n".join(_render_card(l, i + 1) for i, l in enumerate(listings))

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    return (template
        .replace("{{DATE}}", date_str)
        .replace("{{COUNT}}", str(len(listings)))
        .replace("{{TOP_CARD}}", top_card)
        .replace("{{LISTING_CARDS}}", listing_cards)
    )


def _render_top_card(l: BaseListing) -> str:
    """Render the featured #1 listing as a highlighted card."""
    commute = _commute_text(l)
    sqft_text = f"{l.sqft:,.0f} sqft" if l.sqft else "sqft N/A"
    psf_text = f"S${l.price_per_sqft:.2f}/sqft" if l.price_per_sqft else ""
    bed_text = f"{l.bedrooms}BR" if l.bedrooms else ""
    furnish = l.furnishing or ""
    score_bar = format_score_bar(l.score)

    return f"""
    <div style="background:#f0f7ff;border:2px solid #2563eb;border-radius:12px;padding:20px;margin-bottom:24px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;">
        <div>
          <span style="background:#2563eb;color:#fff;font-size:11px;font-weight:bold;padding:3px 8px;border-radius:4px;text-transform:uppercase;">⭐ Top Pick</span>
          <h2 style="margin:8px 0 4px;font-size:18px;">
            <a href="{l.url}" style="color:#1d4ed8;text-decoration:none;">{_esc(l.title)}</a>
          </h2>
          <p style="margin:0;color:#64748b;font-size:13px;">{_esc(l.address or l.development_name)}</p>
        </div>
        <div style="text-align:right;margin-top:8px;">
          <div style="font-size:24px;font-weight:bold;color:#1d4ed8;">S${l.price_sgd:,.0f}<span style="font-size:14px;font-weight:normal;">/mo</span></div>
          <div style="font-size:12px;color:#64748b;">{psf_text}</div>
        </div>
      </div>
      {"<img src='" + l.thumbnail_url + "' style='width:100%;max-height:200px;object-fit:cover;border-radius:8px;margin:12px 0;' />" if l.thumbnail_url else ""}
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px;">
        {_badge(bed_text) if bed_text else ""}
        {_badge(sqft_text)}
        {_badge(furnish) if furnish else ""}
        {_badge("🚇 " + l.nearest_mrt) if l.nearest_mrt else ""}
      </div>
      <div style="margin-top:12px;padding:10px;background:#fff;border-radius:8px;">
        <div style="font-family:monospace;font-size:13px;color:#374151;">Score: {score_bar}</div>
        <div style="font-size:12px;color:#64748b;margin-top:4px;">{commute}</div>
      </div>
      <a href="{l.url}" style="display:inline-block;margin-top:12px;background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold;">View Listing →</a>
    </div>"""


def _render_card(l: BaseListing, rank: int) -> str:
    """Render a standard listing card."""
    commute = _commute_text(l)
    sqft_text = f"{l.sqft:,.0f} sqft" if l.sqft else ""
    psf_text = f"S${l.price_per_sqft:.2f}/sqft" if l.price_per_sqft else ""
    bed_text = f"{l.bedrooms}BR" if l.bedrooms else ""
    furnish = l.furnishing or ""
    score_bar = format_score_bar(l.score, width=8)

    thumb_html = ""
    if l.thumbnail_url:
        thumb_html = f'<td style="width:120px;vertical-align:top;padding-right:12px;"><img src="{l.thumbnail_url}" style="width:120px;height:80px;object-fit:cover;border-radius:6px;" /></td>'

    return f"""
    <tr>
      <td style="padding:16px 0;border-bottom:1px solid #e2e8f0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            {thumb_html}
            <td style="vertical-align:top;">
              <div style="display:flex;justify-content:space-between;flex-wrap:wrap;">
                <div>
                  <span style="color:#94a3b8;font-size:12px;font-weight:bold;">#{rank}</span>
                  <a href="{l.url}" style="font-size:15px;font-weight:bold;color:#1e293b;text-decoration:none;margin-left:6px;">{_esc(l.title)}</a>
                  <div style="font-size:12px;color:#64748b;margin-top:2px;">{_esc(l.address or l.development_name)}</div>
                </div>
                <div style="text-align:right;">
                  <div style="font-size:18px;font-weight:bold;color:#2563eb;">S${l.price_sgd:,.0f}<span style="font-size:12px;font-weight:normal;">/mo</span></div>
                  <div style="font-size:11px;color:#94a3b8;">{psf_text}</div>
                </div>
              </div>
              <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
                {_badge_sm(bed_text) if bed_text else ""}
                {_badge_sm(sqft_text) if sqft_text else ""}
                {_badge_sm(furnish) if furnish else ""}
                {_badge_sm("🚇 " + l.nearest_mrt) if l.nearest_mrt else ""}
              </div>
              <div style="margin-top:6px;font-family:monospace;font-size:11px;color:#64748b;">{score_bar} &nbsp;|&nbsp; {commute}</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def _commute_text(l: BaseListing) -> str:
    parts = []
    if l.commute_funan_min is not None:
        parts.append(f"Funan: {l.commute_funan_min:.0f}min")
    if l.commute_raffles_min is not None:
        parts.append(f"Raffles: {l.commute_raffles_min:.0f}min")
    if l.nearest_mrt_walk_min is not None:
        parts.append(f"MRT walk: {l.nearest_mrt_walk_min:.0f}min")
    return " · ".join(parts) if parts else "Commute: N/A"


def _badge(text: str) -> str:
    return f'<span style="background:#e0f2fe;color:#0369a1;padding:4px 10px;border-radius:12px;font-size:12px;white-space:nowrap;">{_esc(text)}</span>'


def _badge_sm(text: str) -> str:
    return f'<span style="background:#f1f5f9;color:#475569;padding:2px 8px;border-radius:10px;font-size:11px;white-space:nowrap;">{_esc(text)}</span>'


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
