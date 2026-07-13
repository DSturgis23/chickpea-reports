from datetime import date
import streamlit as st

def _secret(key: str, fallback: str = "") -> str:
    try:
        return st.secrets["eviivo"][key]
    except Exception:
        return fallback

EVIIVO_CLIENT_ID     = _secret("client_id")
EVIIVO_CLIENT_SECRET = _secret("client_secret")
EVIIVO_AUTH_URL      = "https://auth.eviivo.com/api/connect/token"
EVIIVO_API_URL       = "https://io.eviivo.com/pms/v2"

PROPERTIES = {
    "The Bell & Crown":    {"shortname": "TheBellBA121",          "rooms": 6},
    "The Dog & Gun":       {"shortname": "DogandGunSP4",          "rooms": 6},
    "The Fleur de Lys":    {"shortname": "TheFleurdeLysInnBH21",  "rooms": 9},
    "The Grosvenor Arms":  {"shortname": "TheGrosvenorArmsSP3",   "rooms": 9},
    "The Manor House Inn": {"shortname": "TheManorHouseInnBA4",   "rooms": 9},
    "The Pembroke Arms":   {"shortname": "PembrokeSP2",           "rooms": 9},
    "The Queen's Head":    {"shortname": "TheQueensHeadSP5",      "rooms": 9},
}

# Queen's Head expanded from 4 to 9 rooms on 1 April 2026
_QH_EXPANSION = date(2026, 4, 1)
_QH_OLD_ROOMS = 4

# Ad-hoc room blocks: rooms taken out of saleable inventory for private events etc.
# Each entry: (property, from_date, to_date, rooms_blocked, reason)
ROOM_BLOCKS = [
    ("The Pembroke Arms", date(2026, 5, 22), date(2026, 5, 24), 9, "Jordan's wedding (private event)"),
]


def get_room_count(property_name: str, for_date: date) -> int:
    if property_name == "The Queen's Head" and for_date < _QH_EXPANSION:
        rooms = _QH_OLD_ROOMS
    else:
        rooms = PROPERTIES[property_name]["rooms"]
    for prop, from_d, to_d, blocked, _ in ROOM_BLOCKS:
        if prop == property_name and from_d <= for_date <= to_d:
            rooms -= blocked
    return max(rooms, 0)


def room_block_notes(property_name: str, dates: list) -> list[str]:
    """Describe any ROOM_BLOCKS entries overlapping the given dates."""
    notes = []
    for prop, from_d, to_d, blocked, reason in ROOM_BLOCKS:
        if prop == property_name and any(from_d <= d <= to_d for d in dates):
            span = from_d.strftime("%d %b") if from_d == to_d else \
                f"{from_d.strftime('%d')}-{to_d.strftime('%d %b %Y')}"
            notes.append(f"{blocked} room{'s' if blocked != 1 else ''} blocked {span} ({reason})")
    return notes


def available_room_nights(property_name: str, from_date: date, to_date: date) -> int:
    """Total room-nights available for a property across a date range (inclusive)."""
    total = 0
    d = from_date
    while d <= to_date:
        total += get_room_count(property_name, d)
        d = date(d.year, d.month, d.day + 1) if d.day < 28 else d + __import__("datetime").timedelta(days=1)
    return total


BRAND_GREEN = "#1C3829"
BRAND_LIGHT = "#C8DFC8"
APP_PASSWORD = "chickpea2024"

# Property opening dates — months before these are pre-opening (no data)
OPENING_DATES = {
    "The Manor House Inn": date(2025, 2, 1),   # opened 1 Feb 2025 (Jan 2025 = pre-opening, Feb = partial)
    "The Fleur de Lys":    date(2025, 9, 1),   # opened Sep 2025
}

# Known closure / refurbishment periods — flag these rather than show misleading variances
# Each entry: (property, from_date, to_date, reason)
KNOWN_CLOSURES = [
    ("The Dog & Gun",   date(2026, 1, 1), date(2026, 1, 31), "Closed for refurbishment"),
    ("The Queen's Head", date(2026, 1, 1), date(2026, 2, 28), "Closed for refurbishment/expansion"),
]

# Partial closures: property was closed for part of the month
PARTIAL_CLOSURES = [
    ("The Queen's Head", 2026, 3, "Closed until 27 Mar — re-opened 27th March"),
]


def is_pre_opening(property_name: str, year: int, month: int) -> bool:
    """True if this property wasn't open in the given year/month."""
    opening = OPENING_DATES.get(property_name)
    if not opening:
        return False
    return date(year, month, 1) < date(opening.year, opening.month, 1)


def is_partial_opening(property_name: str, year: int, month: int) -> bool:
    """True if the property opened partway through this month."""
    opening = OPENING_DATES.get(property_name)
    if not opening:
        return False
    return opening.year == year and opening.month == month and opening.day > 1


def closure_note(property_name: str, year: int, month: int) -> str | None:
    """Return a closure reason if this property had a full known closure in this month, else None."""
    for name, from_d, to_d, reason in KNOWN_CLOSURES:
        if name == property_name and from_d.year == year and from_d.month <= month <= to_d.month:
            return reason
    return None


def partial_closure_note(property_name: str, year: int, month: int) -> str | None:
    """Return a note if the property was partially closed this month."""
    for name, yr, mo, note in PARTIAL_CLOSURES:
        if name == property_name and yr == year and mo == month:
            return note
    return None
