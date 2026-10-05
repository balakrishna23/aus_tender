#!/usr/bin/env python3
"""
Listings pipeline: crawl -> unified listings -> segregated contacts -> Google Sheets -> share.

Reads every spider database, normalises the rows into one listings table,
extracts and de-duplicates contact emails/phones, segregates them by sector
and by source portal/state, writes CSVs to exports/, publishes three
worksheets (Listings, Contacts, Summary) and shares the spreadsheet with the
addresses in GSHEETS_SHARE_WITH.

Usage:
  python listings_pipeline.py                     # export + publish from existing DBs
  python listings_pipeline.py --crawl             # run all spiders first
  python listings_pipeline.py --crawl --spiders aus_tender buy_ict
  python listings_pipeline.py --no-sheets         # CSVs only
  python listings_pipeline.py --share-with someone@gmail.com --share-role writer
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from dateutil import parser as date_parser
from dotenv import load_dotenv

from database_manager import DatabaseManager
from project_paths import PROJECT_DIR

logger = logging.getLogger("listings_pipeline")

EXPORT_DIR = PROJECT_DIR / "exports"
STATES = ["ACT", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT"]
STATE_RE = re.compile(r"\b(ACT|NSW|VIC|QLD|SA|WA|TAS|NT)\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Australian landline/mobile/13-numbers, with optional +61 / (0x) prefixes.
PHONE_RE = re.compile(
    r"(?:\+?61[\s-]?\(?0?\d\)?|\(0\d\)|0\d)[\s-]?\d{4}[\s-]?\d{4}"
    r"|(?:\+?61[\s-]?|0)4\d{2}[\s-]?\d{3}[\s-]?\d{3}"
    r"|1[38]00[\s-]?\d{3}[\s-]?\d{3}"
)

SOURCE_LABELS = {
    "aus_contract": "AusTender - Contract Notice",
    "aus_tender": "AusTender - Open Tender (ATM)",
    "planned_procurement": "AusTender - Planned Procurement",
    "standing_offer": "AusTender - Standing Offer",
    "buy_ict": "BuyICT",
    "act_tender": "ACT Tenders",
    "nsw_tender": "NSW buy.nsw",
}

# First match wins, so the most specific sectors come first.
SECTOR_RULES = [
    ("Labour Hire & Recruitment", [
        "labour hire", "labor hire", "temporary personnel", "personnel recruitment",
        "recruitment", "contingent", "staffing", "contractor services", "secondment",
    ]),
    ("ICT & Digital", [
        "ict", "computer", "software", "information technology", "digital", "cyber",
        "data", "cloud", "network", "telecommunication", "it services",
        "application", "systems integration", "database", "website", "web", "internet",
    ]),
    ("Health & Community", [
        "health", "medical", "aged care", "disability", "nursing", "hospital",
        "mental", "community and social", "social services", "pharmaceutical",
        "clinical", "indigenous", "first nations", "welfare", "psycholog", "counselling",
    ]),
    ("Professional & Consulting", [
        "management advisory", "management support", "project management", "consult",
        "advisory", "audit", "legal", "accounting", "financial", "insurance", "education",
        "training", "research", "business administration", "human resources", "evaluation",
        "communications", "marketing", "advertising", "events", "policy", "administrative",
        "public administration", "printing", "publishing", "translation",
    ]),
    ("Construction, Facilities & Logistics", [
        "building", "construction", "maintenance and repair", "cleaning", "facilit",
        "property", "lease and rental", "engineering", "civil", "road", "electrical",
        "plumbing", "landscap", "security services", "waste", "environmental",
        "utilities", "transport", "freight", "cargo", "mail", "logistic", "removal",
    ]),
    ("Defence, Equipment & Supplies", [
        "military", "defence", "weapon", "ammunition", "aircraft", "vehicle", "marine",
        "watercraft", "equipment", "machinery", "components", "hardware", "instrument",
        "pumps", "lubricant", "containers", "fluid", "fuel", "chemical", "furniture",
        "clothing", "food", "paper", "tools", "supplies", "products",
    ]),
]
# Keywords match at word starts; keywords of four letters or fewer must be whole words
# so "ict" does not match "district" and "mental" does not match "environmental".
SECTOR_PATTERNS = [
    (sector, re.compile("|".join(
        rf"\b{re.escape(k)}\b" if len(k) <= 4 else rf"\b{re.escape(k)}" for k in keywords)))
    for sector, keywords in SECTOR_RULES
]
ROLE_WORD_RE = re.compile(
    r"procure|tender|enq|contract|admin|support|office|team|group|unit|service|branch|"
    r"division|program|project|secretariat|workshop|sanitis|account|finance|corporate",
    re.I,
)

GENERIC_INBOX_RE = re.compile(
    r"^(tender|tenders|procurement|purchasing|contract|contracts|enquir|info|admin|"
    r"help|support|finance|accounts|reception|office|general|mail|contact|noreply|"
    r"no-reply|feedback|corporate|business|operations|ops|service|team|hr|recruit|"
    r"careers|jobs|media|governance)",
    re.I,
)

LISTING_COLUMNS = [
    "source", "listing_id", "title", "agency", "category", "sector", "states",
    "status", "published", "closes", "value_aud", "contact_name", "contact_email",
    "contact_phone", "description", "url",
]


# ---------------------------------------------------------------- normalisers

def clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_date(value) -> Optional[datetime]:
    """Parse the various portal date formats; returns None when unparseable."""
    text = clean(value)
    if not text or text.lower().startswith("quarter"):
        return None
    # BuyICT appends "• 11:59pm, Canberra time" (sometimes mis-decoded as �).
    text = re.split(r"[•�]|, Canberra", text)[0].strip()
    try:
        return date_parser.parse(text, dayfirst=True, fuzzy=True)
    except (ValueError, OverflowError):
        return None


def iso(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def postcode_state(postcode) -> str:
    digits = re.sub(r"\D", "", clean(postcode))
    if not digits:
        return ""
    pc = int(digits)
    if 200 <= pc <= 299 or 2600 <= pc <= 2618 or 2900 <= pc <= 2920:
        return "ACT"
    if 800 <= pc <= 999:
        return "NT"
    if 1000 <= pc <= 2999:
        return "NSW"
    if 3000 <= pc <= 3999 or 8000 <= pc <= 8999:
        return "VIC"
    if 4000 <= pc <= 4999 or 9000 <= pc <= 9999:
        return "QLD"
    if 5000 <= pc <= 5999:
        return "SA"
    if 6000 <= pc <= 6999:
        return "WA"
    if 7000 <= pc <= 7999:
        return "TAS"
    return ""


def extract_states(text) -> str:
    found = {m for m in STATE_RE.findall(clean(text))}
    if len(found) == len(STATES):
        return "National"
    return ", ".join(s for s in STATES if s in found)


def classify_sector(*texts) -> str:
    blob = " ".join(clean(t).lower() for t in texts)
    for sector, pattern in SECTOR_PATTERNS:
        if pattern.search(blob):
            return sector
    return "Other"


def extract_emails(*texts) -> List[str]:
    seen = []
    for text in texts:
        for email in EMAIL_RE.findall(clean(text)):
            email = email.lower().strip(".'")
            if email not in seen:
                seen.append(email)
    return seen


def normalise_phone(raw) -> str:
    """Return +61 format for AU numbers, spaced 1300/1800 numbers, else ''."""
    digits = re.sub(r"\D", "", clean(raw))
    if digits.startswith("61"):
        digits = "0" + digits[2:]
        if digits.startswith("00"):  # +61 (0)2 ...
            digits = digits[1:]
    if re.fullmatch(r"1[38]00\d{6}", digits):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if re.fullmatch(r"04\d{8}", digits):
        return f"+61 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    if re.fullmatch(r"0[2378]\d{8}", digits):
        return f"+61 {digits[1]} {digits[2:6]} {digits[6:]}"
    return ""


def extract_phones(*texts) -> List[str]:
    seen = []
    for text in texts:
        for match in PHONE_RE.findall(clean(text)):
            phone = normalise_phone(match)
            if phone and phone not in seen:
                seen.append(phone)
    return seen


def inbox_type(email: str) -> str:
    """Personal = looks like firstname.lastname; everything else is a shared/role inbox."""
    local = email.split("@", 1)[0].lower()
    parts = re.split(r"[._'-]", local)
    name_like = (
        2 <= len(parts) <= 3
        and all(re.fullmatch(r"[a-z]{2,}\d{0,2}", p) and re.search(r"[aeiouy]", p) for p in parts)
    )
    if GENERIC_INBOX_RE.match(local) or ROLE_WORD_RE.search(local) or not name_like:
        return "Generic"
    return "Personal"


# ----------------------------------------------------------------- listings

def _read_table(db: DatabaseManager, spider: str) -> pd.DataFrame:
    path = db.get_db_path(spider)
    if not path.exists():
        logger.warning("Database missing for %s: %s", spider, path)
        return pd.DataFrame()
    table = db.SCHEMAS[spider]["table_name"]
    with db.get_connection(spider) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def _row_listing(spider: str, r: Dict, today: datetime) -> Dict:
    g = lambda k: clean(r.get(k))
    published = closes = None
    status = ""
    value = ""
    emails: List[str] = []
    phones: List[str] = []

    if spider == "aus_contract":
        listing_id, title, category = g("cn_id"), g("description"), g("category")
        published = parse_date(g("publish_date"))
        end = parse_date(g("contract_period").split(" to ")[-1]) or parse_date(g("max_end_date"))
        status = "Active contract" if end and end >= today else "Awarded"
        closes = end
        value = r.get("contract_value_aud")
        states = postcode_state(g("office_postcode"))
        emails = extract_emails(g("email_address"))
        extra = f"Supplier: {g('supplier_name')}" if g("supplier_name") else ""
        description = " | ".join(x for x in [g("procurement_method"), extra] if x)
    elif spider == "aus_tender":
        listing_id, title, category = g("atm_id"), g("service_name"), g("category")
        published = parse_date(g("publish_date"))
        closes = parse_date(g("close_date_time"))
        status = "Open" if closes and closes >= today else "Closed"
        states = extract_states(g("location"))
        emails = extract_emails(g("contact_email"))
        phones = extract_phones(g("contact_phone"))
        description = g("description")
    elif spider == "planned_procurement":
        listing_id, title, category = g("agency_reference"), g("title"), g("category")
        published = parse_date(g("last_updated"))
        status = f"Planned ({g('estimated_date_of_approach_to_market')})".replace(" ()", "")
        states = ""
        emails = extract_emails(g("contact_email"))
        phones = extract_phones(g("contact_phone"))
        description = g("change_comments")
    elif spider == "standing_offer":
        listing_id, title, category = g("son_id"), g("title"), g("primary_category")
        published = parse_date(g("publish_date"))
        closes = parse_date(g("standing_offer_period").split(" to ")[-1])
        status = "Active panel" if closes and closes >= today else "Expired panel"
        states = postcode_state(g("office_postcode"))
        emails = extract_emails(g("email_address"))
        description = g("description")
    elif spider == "buy_ict":
        listing_id, title, category = g("rfq_id"), g("title"), g("rfq_type")
        published = parse_date(g("rfq_published_date"))
        closes = parse_date(g("rfq_closing_date"))
        status = "Open" if closes and closes >= today else "Closed"
        states = extract_states(g("location_of_work"))
        emails = extract_emails(g("buyer_contact"))
        phones = extract_phones(g("buyer_contact"))
        description = " | ".join(x for x in [
            g("working_arrangements"), g("initial_contract_duration"), g("requirements")
        ] if x)
    else:  # act_tender / nsw_tender
        listing_id, title, category = g("reference"), g("title"), g("category")
        published = parse_date(g("publish_date"))
        closes = parse_date(g("close_date"))
        status = g("status") or ("Open" if closes and closes >= today else "")
        states = "ACT" if spider == "act_tender" else "NSW"
        value = g("value")
        emails = extract_emails(g("contact_email"))
        description = g("description")

    # Contact details buried in free text are still worth keeping.
    for email in extract_emails(description):
        if email not in emails:
            emails.append(email)
    for phone in extract_phones(description):
        if phone not in phones:
            phones.append(phone)

    agency = g("agency") or g("buyer")
    return {
        "source": SOURCE_LABELS[spider],
        "listing_id": listing_id,
        "title": title,
        "agency": agency,
        "category": category,
        "sector": classify_sector(category, title),
        "states": states,
        "status": status,
        "published": iso(published),
        "closes": iso(closes),
        "value_aud": "" if value in (None, "") or pd.isna(value) else value,
        "contact_name": g("contact_name"),
        "contact_email": "; ".join(emails),
        "contact_phone": "; ".join(phones),
        "description": description[:500],
        "url": g("url"),
    }


def build_listings(db: Optional[DatabaseManager] = None, today: Optional[datetime] = None) -> pd.DataFrame:
    db = db or DatabaseManager()
    today = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for spider in SOURCE_LABELS:
        df = _read_table(db, spider)
        logger.info("Read %d rows from %s", len(df), spider)
        for record in df.to_dict("records"):
            rows.append(_row_listing(spider, record, today))
    listings = pd.DataFrame(rows, columns=LISTING_COLUMNS)
    return listings.sort_values(["published", "source"], ascending=[False, True], kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------- contacts

def _join_unique(values, limit: int = 0) -> str:
    out = []
    for v in values:
        for part in str(v).split("; "):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    if limit and len(out) > limit:
        return "; ".join(out[:limit]) + f"; (+{len(out) - limit} more)"
    return "; ".join(out)


def build_contacts(listings: pd.DataFrame) -> pd.DataFrame:
    """One row per unique email (or phone, when no email), segregated by sector/source/state."""
    exploded = []
    for r in listings.to_dict("records"):
        emails = [e for e in r["contact_email"].split("; ") if e]
        keys = emails or ([f"phone:{p}" for p in r["contact_phone"].split("; ") if p][:1])
        for key in keys:
            exploded.append({**r, "key": key})
    if not exploded:
        return pd.DataFrame()

    df = pd.DataFrame(exploded)
    open_status = df["status"].isin(["Open", "Active panel", "Active contract"]) | df["status"].str.startswith("Planned")
    df["is_live"] = open_status

    contacts = []
    for key, grp in df.groupby("key", sort=False):
        grp = grp.sort_values("published", ascending=False)
        latest = grp.iloc[0]
        sector_counts = grp["sector"].value_counts()
        state_list = _join_unique(grp["states"]).replace("; ", ", ")
        state_set = sorted({s.strip() for s in state_list.split(",") if s.strip()})
        email = "" if key.startswith("phone:") else key
        contacts.append({
            "email": email,
            "inbox_type": inbox_type(email) if email else "",
            "contact_name": _join_unique(grp["contact_name"], limit=3),
            "phone": _join_unique(grp["contact_phone"], limit=3),
            "agency": _join_unique(grp["agency"], limit=3),
            "primary_sector": sector_counts.index[0],
            "all_sectors": ", ".join(sector_counts.index),
            "sources": _join_unique(grp["source"]),
            "states": ", ".join(state_set),
            "listings_total": len(grp),
            "listings_live": int(grp["is_live"].sum()),
            "latest_listing": latest["title"][:200],
            "latest_listing_status": latest["status"],
            "latest_published": latest["published"],
            "latest_url": latest["url"],
        })
    out = pd.DataFrame(contacts)
    return out.sort_values(
        ["primary_sector", "listings_live", "listings_total"], ascending=[True, False, False], kind="stable"
    ).reset_index(drop=True)


def build_summary(listings: pd.DataFrame, contacts: pd.DataFrame) -> pd.DataFrame:
    """Counts of listings and contacts by sector x source, and by sector x state."""
    rows = [{"view": "Generated", "sector": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "group": "", "listings": len(listings), "live_listings": "", "contacts": len(contacts)}]
    live = listings["status"].isin(["Open", "Active panel", "Active contract"]) | listings["status"].str.startswith("Planned")
    for sector, sgrp in listings.groupby("sector"):
        for source, grp in sgrp.groupby("source"):
            emails = {e for v in grp["contact_email"] for e in v.split("; ") if e}
            rows.append({"view": "By source", "sector": sector, "group": source, "listings": len(grp),
                         "live_listings": int(live[grp.index].sum()), "contacts": len(emails)})
    state_rows = listings.assign(state=listings["states"].replace("", "Unknown").str.split(", ")).explode("state")
    for sector, sgrp in state_rows.groupby("sector"):
        for state, grp in sgrp.groupby("state"):
            emails = {e for v in grp["contact_email"] for e in v.split("; ") if e}
            rows.append({"view": "By state", "sector": sector, "group": state, "listings": len(grp),
                         "live_listings": int(live[grp.index.unique()].sum()), "contacts": len(emails)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ google sheets

def _service_account_info() -> Dict:
    import settings as project_settings  # resolves file-path vs inline JSON
    content = project_settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT
    if not content:
        raise SystemExit("Google credentials missing: set GSHEETS_SERVICE_ACCOUNT_FILE or "
                         "GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT in .env")
    return json.loads(content)


def open_spreadsheet():
    import gspread
    import settings as project_settings
    if not project_settings.GSHEETS_SPREADSHEET_ID:
        raise SystemExit("GSHEETS_SPREADSHEET_ID is not set in .env")
    client = gspread.service_account_from_dict(_service_account_info())
    return client.open_by_key(project_settings.GSHEETS_SPREADSHEET_ID)


def publish(spreadsheet, frames: Dict[str, pd.DataFrame]) -> None:
    import gspread
    from sheets_writer import replace_worksheets
    replacements = []
    for title, frame in frames.items():
        try:
            ws = spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=title, rows=max(len(frame) + 1, 2),
                                           cols=max(len(frame.columns), 2))
        replacements.append((ws, frame.fillna("").astype(str)))
    replace_worksheets(spreadsheet, replacements)
    for ws, frame in replacements:
        try:
            ws.freeze(rows=1)
            ws.set_basic_filter(1, 1, len(frame) + 1, len(frame.columns))
        except Exception:
            logger.debug("Could not format %s", ws.title, exc_info=True)


def share(spreadsheet, emails: List[str], role: str = "reader") -> List[str]:
    """Grant access to each address that does not already have it. Returns newly shared."""
    existing = {(p.get("emailAddress") or "").lower() for p in spreadsheet.list_permissions()}
    shared = []
    for email in emails:
        if email.lower() in existing:
            logger.info("Already shared with %s", email)
            continue
        spreadsheet.share(email, perm_type="user", role=role, notify=True,
                          email_message="AusTender listings and contacts sheet (auto-updated).")
        shared.append(email)
        logger.info("Shared spreadsheet with %s as %s", email, role)
    return shared


# --------------------------------------------------------------------- main

def run_crawl(spiders: Optional[List[str]], timeout: int, sync_raw: bool) -> int:
    cmd = [sys.executable, str(PROJECT_DIR / "run_spiders.py"), "--timeout", str(timeout)]
    if spiders:
        cmd += ["--spiders", *spiders]
    if not sync_raw:
        cmd.append("--no-sync")
    logger.info("Crawling: %s", " ".join(cmd[1:]))
    return subprocess.call(cmd, cwd=PROJECT_DIR)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(PROJECT_DIR / ".env")
    ap = argparse.ArgumentParser(description="Crawl, export listings/contacts to Google Sheets and share")
    ap.add_argument("--crawl", action="store_true", help="Run the spiders before exporting")
    ap.add_argument("--spiders", nargs="+", choices=list(SOURCE_LABELS), help="Spiders to crawl (default: all)")
    ap.add_argument("--timeout", type=int, default=10800, help="Per-spider crawl timeout in seconds")
    ap.add_argument("--sync-raw-tabs", action="store_true", help="Also let spiders publish their raw worksheets")
    ap.add_argument("--no-sheets", action="store_true", help="Write CSVs only; skip Google Sheets")
    ap.add_argument("--share-with", nargs="+", help="Addresses to share with (default: GSHEETS_SHARE_WITH)")
    ap.add_argument("--share-role", choices=["reader", "commenter", "writer"],
                    default=os.environ.get("GSHEETS_SHARE_ROLE", "reader"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    crawl_code = 0
    if args.crawl:
        crawl_code = run_crawl(args.spiders, args.timeout, args.sync_raw_tabs)
        if crawl_code:
            logger.warning("Crawl reported problems (exit %d); exporting the data that was saved.", crawl_code)

    listings = build_listings()
    contacts = build_contacts(listings)
    summary = build_summary(listings, contacts)

    EXPORT_DIR.mkdir(exist_ok=True)
    listings.to_csv(EXPORT_DIR / "listings_master.csv", index=False, encoding="utf-8-sig")
    contacts.to_csv(EXPORT_DIR / "contacts_master.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(EXPORT_DIR / "listings_summary.csv", index=False, encoding="utf-8-sig")
    logger.info("Listings: %d | Contacts: %d (emails: %d, phone-only: %d) -> %s",
                len(listings), len(contacts), int((contacts["email"] != "").sum()),
                int((contacts["email"] == "").sum()), EXPORT_DIR)

    if args.no_sheets:
        return crawl_code

    spreadsheet = open_spreadsheet()
    publish(spreadsheet, {"Listings": listings, "Contacts": contacts, "Summary": summary})
    logger.info("Published Listings/Contacts/Summary to %s", spreadsheet.url)

    share_with = args.share_with or [e.strip() for e in os.environ.get("GSHEETS_SHARE_WITH", "").split(",") if e.strip()]
    if share_with:
        share(spreadsheet, share_with, args.share_role)
    else:
        logger.warning("No GSHEETS_SHARE_WITH configured; spreadsheet not shared with anyone new.")
    return crawl_code


if __name__ == "__main__":
    sys.exit(main())
