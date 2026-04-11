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
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#e8f1fa;border:2px solid #2E75B6;border-radius:12px;margin-bottom:24px;overflow:hidden;">
      <tr>
        <td style="padding:20px;">
          <!-- Title row -->
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td valign="top">
                <div style="margin-bottom:8px;"><span style="background-color:#FFD966;color:#212529;font-size:11px;font-weight:700;padding:3px 10px;border-radius:4px;text-transform:uppercase;letter-spacing:0.5px;">&#9733; Top Pick</span></div>
                <a href="{l.url}" style="font-size:18px;font-weight:700;color:#2E75B6;text-decoration:none;line-height:1.3;">{_esc(l.title)}</a>
                <div style="font-size:12px;color:#6c757d;margin-top:4px;">{_esc(l.address or l.development_name)}</div>
              </td>
              <td valign="top" align="right" style="padding-left:16px;white-space:nowrap;">
                <div style="font-size:26px;font-weight:800;color:#2E75B6;line-height:1;">S${l.price_sgd:,.0f}</div>
                <div style="font-size:12px;color:#6c757d;">/mo{(" &nbsp;·&nbsp; " + psf_text) if psf_text else ""}</div>
              </td>
            </tr>
          </table>
          {"<img src='" + l.thumbnail_url + "' width='100%' style='max-height:200px;object-fit:cover;border-radius:8px;margin:12px 0;display:block;' />" if l.thumbnail_url else ""}
          <!-- Badges -->
          <div style="margin-top:12px;">
            {_badge(bed_text) if bed_text else ""}
            {_badge(sqft_text)}
            {_badge(furnish) if furnish else ""}
            {_badge("&#128643; " + _esc(l.nearest_mrt)) if l.nearest_mrt else ""}
          </div>
          <!-- Score & commute -->
          <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;background:#ffffff;border-radius:8px;">
            <tr>
              <td style="padding:10px 14px;">
                <div style="font-family:monospace;font-size:13px;color:#212529;margin-bottom:4px;">Score: {score_bar}</div>
                <div style="font-size:12px;color:#6c757d;">{commute}</div>
              </td>
            </tr>
          </table>
          <a href="{l.url}" style="display:inline-block;margin-top:14px;background-color:#2E75B6;color:#ffffff;padding:10px 22px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:700;">View Listing &#8594;</a>
        </td>
      </tr>
    </table>"""


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
        thumb_html = f'<td width="110" valign="top" style="padding-right:12px;"><img src="{l.thumbnail_url}" width="110" height="74" style="display:block;border-radius:6px;object-fit:cover;" /></td>'

    return f"""
    <tr>
      <td style="padding:14px 0;border-bottom:1px solid #dee2e6;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            {thumb_html}
            <td valign="top">
              <!-- Title + price -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="top">
                    <span style="color:#adb5bd;font-size:11px;font-weight:700;">#{rank}</span>
                    <a href="{l.url}" style="font-size:14px;font-weight:700;color:#212529;text-decoration:none;margin-left:5px;">{_esc(l.title)}</a>
                    <div style="font-size:11px;color:#6c757d;margin-top:2px;">{_esc(l.address or l.development_name)}</div>
                  </td>
                  <td valign="top" align="right" style="padding-left:8px;white-space:nowrap;">
                    <div style="font-size:16px;font-weight:700;color:#2E75B6;">S${l.price_sgd:,.0f}<span style="font-size:11px;font-weight:400;">/mo</span></div>
                    <div style="font-size:10px;color:#adb5bd;">{psf_text}</div>
                  </td>
                </tr>
              </table>
              <!-- Badges -->
              <div style="margin-top:7px;">
                {_badge_sm(bed_text) if bed_text else ""}
                {_badge_sm(sqft_text) if sqft_text else ""}
                {_badge_sm(furnish) if furnish else ""}
                {_badge_sm("&#128643; " + _esc(l.nearest_mrt)) if l.nearest_mrt else ""}
              </div>
              <!-- Score + commute -->
              <div style="margin-top:5px;font-family:monospace;font-size:10px;color:#6c757d;">{score_bar} &nbsp;|&nbsp; {commute}</div>
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
    return f'<span style="display:inline-block;background-color:#dbeafe;color:#1d4ed8;padding:4px 10px;border-radius:12px;font-size:12px;white-space:nowrap;margin:2px 4px 2px 0;">{text}</span>'


def _badge_sm(text: str) -> str:
    return f'<span style="display:inline-block;background-color:#f1f3f5;color:#495057;padding:2px 8px;border-radius:10px;font-size:10px;white-space:nowrap;margin:1px 3px 1px 0;">{text}</span>'


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
