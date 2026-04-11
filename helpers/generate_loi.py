#!/usr/bin/env python3
"""
Letter of Intent (LoI) Generator for Singapore Rental Applications.

Usage:
    python helpers/generate_loi.py \\
        --listing "Parc Sovereign #07-12" \\
        --landlord "Mr. Tan" \\
        --price 3500 \\
        --deposit-months 1 \\
        --lease-months 12 \\
        --start 2026-05-01

Tenant details are read from config/user_profile.yaml (if it exists)
or from environment variables / .env:
    TENANT_NAME        Full legal name
    TENANT_NRIC        NRIC / Passport number
    TENANT_EMAIL       Contact email
    TENANT_PHONE       Contact phone

Output: prints the LoI to stdout. Redirect to a file if needed:
    python helpers/generate_loi.py ... > loi_parc_sovereign.txt
"""
import argparse
import os
import sys
from datetime import datetime, date
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def load_tenant_profile() -> dict:
    """Load tenant details from config/user_profile.yaml or fall back to env vars."""
    profile_path = Path(__file__).parent.parent / "config" / "user_profile.yaml"
    profile = {}

    if _HAS_YAML and profile_path.exists():
        with open(profile_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        profile = data.get("tenant", data)

    # Env vars override / fill gaps
    profile.setdefault("name", os.environ.get("TENANT_NAME", "[YOUR FULL NAME]"))
    profile.setdefault("nric", os.environ.get("TENANT_NRIC", "[NRIC/PASSPORT NO.]"))
    profile.setdefault("email", os.environ.get("TENANT_EMAIL", os.environ.get("GMAIL_USER", "[YOUR EMAIL]")))
    profile.setdefault("phone", os.environ.get("TENANT_PHONE", "[YOUR PHONE]"))
    profile.setdefault("nationality", os.environ.get("TENANT_NATIONALITY", "[NATIONALITY]"))
    profile.setdefault("occupation", os.environ.get("TENANT_OCCUPATION", "[OCCUPATION]"))
    profile.setdefault("employer", os.environ.get("TENANT_EMPLOYER", "Patsnap Pte Ltd"))

    return profile


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Cannot parse date '{s}'. Use YYYY-MM-DD format.")


def add_months(d: date, months: int) -> date:
    """Add N months to a date, clamping to month end."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def generate_loi(args: argparse.Namespace, tenant: dict) -> str:
    today = date.today()
    start = args.start
    end = add_months(start, args.lease_months)
    deposit_amount = args.price * args.deposit_months
    good_faith = args.price  # 1 month good faith deposit is standard in SG

    # Determine option period end (typically 14 days from offer)
    option_expiry = add_months(today, 0)  # same month
    from datetime import timedelta
    option_expiry = today + timedelta(days=14)

    loi = f"""
================================================================================
                    LETTER OF INTENT TO LEASE
================================================================================

Date: {today.strftime("%d %B %Y")}

TO:
  {args.landlord}
  (Landlord / Agent of Landlord)
  RE: {args.listing}

FROM:
  {tenant['name']}
  NRIC / Passport: {tenant['nric']}
  Nationality:     {tenant['nationality']}
  Occupation:      {tenant['occupation']}
  Employer:        {tenant['employer']}
  Email:           {tenant['email']}
  Phone:           {tenant['phone']}

─────────────────────────────────────────────────────────────────────────────

Dear {args.landlord},

I write to express my sincere interest in leasing the above-captioned property
and to set out the proposed terms of tenancy for your consideration.

1. PROPERTY
   Address:     {args.listing}
   (hereinafter referred to as "the Property")

2. PROPOSED LEASE TERMS

   Monthly Rent:       S${args.price:,}.00 (Singapore Dollars {_spell_amount(args.price)})
   Lease Commencement: {start.strftime("%d %B %Y")}
   Lease Expiry:       {end.strftime("%d %B %Y")} ({args.lease_months} months)
   Security Deposit:   {args.deposit_months} month{'s' if args.deposit_months != 1 else ''} rent
                       = S${deposit_amount:,}.00
   Good Faith Deposit: S${good_faith:,}.00 (payable upon signing this LOI;
                       credited toward security deposit upon lease execution)

3. TENANCY CONDITIONS

   a) The Property shall be used for residential purposes only.
   b) Utilities (electricity, water, gas) to be borne by Tenant.
   c) Monthly maintenance / conservancy charges to be borne by Landlord.
   d) Property to be handed over in good, clean, and tenantable condition,
      fully furnished as per current inventory.
   e) Tenant shall be entitled to install air-conditioner service contract
      at Tenant's cost; Landlord to maintain structural AC units.
   f) Minor repairs up to S$150 per incident to be borne by Tenant;
      major structural repairs by Landlord.
   g) This tenancy shall not be sub-let in whole or in part without
      Landlord's prior written consent.

4. DIPLOMATIC / BREAK CLAUSE (if applicable)

   In the event Tenant is required to relocate outside Singapore for
   employment reasons, Tenant may terminate this tenancy with
   TWO (2) MONTHS written notice after the first SIX (6) MONTHS
   of tenancy, provided supporting documentation is furnished.

5. OPTION PERIOD

   This Letter of Intent shall remain open for acceptance until:
   {option_expiry.strftime("%d %B %Y")} (14 days from date of issue).

   Upon Landlord's acceptance, the good faith deposit of S${good_faith:,}.00
   shall be paid within THREE (3) working days. Formal Tenancy Agreement
   to be executed within FOURTEEN (14) days of acceptance.

6. GOVERNING LAW

   This tenancy shall be governed by the laws of the Republic of Singapore.
   Any dispute shall be referred to the Community Disputes Resolution Tribunal
   or Singapore Mediation Centre in the first instance.

─────────────────────────────────────────────────────────────────────────────

I trust the above terms are acceptable and look forward to your favourable
response. Please do not hesitate to contact me should you require any
clarification or wish to discuss the terms further.

Yours sincerely,

___________________________________
{tenant['name']}
{tenant['nric']}
{today.strftime("%d %B %Y")}
Tel: {tenant['phone']}
Email: {tenant['email']}


─────────────────────────────────────────────────────────────────────────────
ACCEPTANCE BY LANDLORD / AUTHORISED AGENT

I / We, _____________________________________________, confirm acceptance of
the above terms and agree to proceed with the tenancy on the terms set out.

Signature: ___________________________  Date: _______________

Name:      ___________________________

NRIC/UEBN: ___________________________
================================================================================
"""
    return loi.strip()


def _spell_amount(amount: int) -> str:
    """Spell out a round SGD amount in words (simplified, for round thousands)."""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
            "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty",
            "Sixty", "Seventy", "Eighty", "Ninety"]

    if amount == 0:
        return "Zero"
    if amount >= 1000:
        thousands = amount // 1000
        remainder = amount % 1000
        t = ones[thousands] if thousands < 20 else tens[thousands // 10] + (" " + ones[thousands % 10] if thousands % 10 else "")
        if remainder == 0:
            return f"{t} Thousand"
        elif remainder < 100:
            h = ones[remainder] if remainder < 20 else tens[remainder // 10] + (" " + ones[remainder % 10] if remainder % 10 else "")
            return f"{t} Thousand and {h}"
        else:
            h = ones[remainder // 100] + " Hundred"
            r = remainder % 100
            if r == 0:
                return f"{t} Thousand {h}"
            sub = ones[r] if r < 20 else tens[r // 10] + (" " + ones[r % 10] if r % 10 else "")
            return f"{t} Thousand {h} and {sub}"
    if amount < 20:
        return ones[amount]
    return tens[amount // 10] + (" " + ones[amount % 10] if amount % 10 else "")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Singapore rental Letter of Intent (LoI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--listing", required=True,
                        help='Property listing reference, e.g. "Parc Sovereign #07-12"')
    parser.add_argument("--landlord", required=True,
                        help='Landlord name, e.g. "Mr. Tan" or "ABC Property Pte Ltd"')
    parser.add_argument("--price", required=True, type=int,
                        help="Agreed monthly rent in SGD (e.g. 3500)")
    parser.add_argument("--deposit-months", required=True, type=int, dest="deposit_months",
                        help="Number of months security deposit (typically 1 or 2)")
    parser.add_argument("--lease-months", required=True, type=int, dest="lease_months",
                        help="Lease duration in months (e.g. 12 or 24)")
    parser.add_argument("--start", required=True, type=parse_date,
                        help="Lease start date in YYYY-MM-DD format (e.g. 2026-05-01)")

    args = parser.parse_args()

    if args.price <= 0:
        parser.error("--price must be a positive integer")
    if args.deposit_months < 1:
        parser.error("--deposit-months must be at least 1")
    if args.lease_months < 1:
        parser.error("--lease-months must be at least 1")

    tenant = load_tenant_profile()
    loi = generate_loi(args, tenant)
    print(loi)


if __name__ == "__main__":
    main()
