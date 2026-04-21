"""Eviivo API client with parallel fetching and Streamlit caching."""

import requests
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

from config import (
    EVIIVO_CLIENT_ID, EVIIVO_CLIENT_SECRET,
    EVIIVO_AUTH_URL, EVIIVO_API_URL, PROPERTIES,
)

CHUNK_DAYS = 30


def get_token() -> str:
    resp = requests.post(
        EVIIVO_AUTH_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     EVIIVO_CLIENT_ID,
            "client_secret": EVIIVO_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {
        "Authorization":   f"Bearer {token}",
        "X-Auth-ClientId": EVIIVO_CLIENT_ID,
        "Content-Type":    "application/json",
    }


def _safe_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _as_str(v) -> str:
    """Flatten any value (dict, list, etc.) to a plain string for grouping."""
    if v is None:
        return "Unknown"
    if isinstance(v, dict):
        return v.get("Name") or v.get("name") or v.get("Code") or v.get("code") or str(v)
    if isinstance(v, list):
        return ", ".join(_as_str(i) for i in v) if v else "Unknown"
    return str(v).strip() or "Unknown"


_OTA_EMAIL_MAP = [
    # OTAs — confirmed from live booking data
    ("guest.booking.com",           "Booking.com"),
    ("m.expediapartnercentral.com", "Expedia"),
    ("agoda-messaging.com",         "Agoda"),
    ("guest.trip.com",              "Trip.com"),
    # OTAs — standard domains (not yet confirmed in data but widely used)
    ("airbnb.com",                  "Airbnb"),
    ("tripadvisor.com",             "TripAdvisor"),
    ("hotelbeds.com",               "HotelBeds"),
    ("hrs.com",                     "HRS"),
    ("laterooms.com",               "LateRooms"),
    ("expedia.com",                 "Expedia"),
    # Corporate travel agencies — confirmed from live booking data
    ("travelctm.com",               "CTM (Corporate Travel)"),
    ("amexgbt.com",                 "Amex Global Business Travel"),
    ("agiito.com",                  "Agiito (Corporate Travel)"),
    ("travelperktrips.com",         "TravelPerk (Corporate)"),
    ("keytravel.com",               "Key Travel (Corporate)"),
    ("clydetravel.com",             "Clyde Travel (Corporate)"),
    ("inntel.co.uk",                "Inntel (Corporate)"),
    ("stewarttravelmanagement.com", "Stewart Travel (Corporate)"),
    ("bookings.roomex.com",         "Roomex (Corporate)"),
]


def _channel_from_email(email: str) -> str:
    """Infer booking channel from guest email domain."""
    if not email or "@" not in email:
        return "Direct / Phone"
    domain = email.split("@", 1)[-1].lower()
    for ota_domain, name in _OTA_EMAIL_MAP:
        if domain == ota_domain or domain.endswith("." + ota_domain):
            return name
    return "Direct / Phone"


def _rate_plan_from_note(note: str) -> str:
    n = note.lower()
    if "dbb" in n or ("dinner" in n and "breakfast" in n):
        return "Dinner B&B"
    if "half board" in n or "half-board" in n:
        return "Half Board"
    if "breakfast is included" in n or "b&b" in n or "bed and breakfast" in n or "base rate" in n:
        return "B&B"
    if "breakfast" in n:
        return "B&B"
    if "room only" in n:
        return "Room Only"
    return "Unknown"


def _parse_booking(rec: dict, venue_name: str) -> Optional[dict]:
    b = rec.get("Booking", rec)
    ref = b.get("BookingReference") or b.get("BookingRef") or b.get("Reference", "")
    if not ref:
        return None

    checkin  = _safe_date(b.get("CheckinDate") or b.get("ArrivalDate"))
    checkout = _safe_date(b.get("CheckoutDate") or b.get("DepartureDate"))
    # Eviivo returns booking creation time as BookedDateTime
    created  = _safe_date(b.get("BookedDateTime") or b.get("BookedDateTimeUTC"))

    nights = 0
    if checkin and checkout:
        nights = max((checkout - checkin).days, 0)

    # Explicit None check — don't use `or` chain (treats £0 as falsy)
    revenue = 0.0
    _total = b.get("Total")
    if isinstance(_total, dict):
        _gross = _total.get("GrossAmount")
        if isinstance(_gross, dict) and _gross.get("Value") is not None:
            revenue = float(_gross["Value"])

    # Channel: OrderSourceCode is always null in this API. Detect OTA from guest email domain.
    # OTAs use anonymised email addresses (e.g. @guest.booking.com, @agoda-messaging.com).
    # Real email addresses indicate a direct/phone booking.
    guests = rec.get("Guests", [])
    primary_email = ""
    if guests and isinstance(guests, list):
        primary_email = (guests[0].get("Email") or "").lower()
    channel = _channel_from_email(primary_email)

    # Rate plan: detected from BookingNote (RatePlanId is always 0 in this API)
    rate_plan = _rate_plan_from_note(b.get("BookingNote") or "")

    # Room type from the Room object
    room_obj = b.get("Room")
    if isinstance(room_obj, dict):
        room_type = room_obj.get("LocalisedName") or str(room_obj.get("RoomTypeId", "Unknown"))
    else:
        room_type = "Unknown"

    # Each Eviivo booking record = 1 room
    num_rooms = 1
    cancelled = bool(b.get("Cancelled"))

    return {
        "booking_ref": ref,
        "venue_name":  venue_name,
        "cancelled":   cancelled,
        "checkin":     checkin,
        "checkout":    checkout,
        "created":     created,
        "nights":      nights,
        "num_rooms":   num_rooms,
        "revenue":     revenue,
        "channel":     channel,
        "rate_plan":   rate_plan,
        "room_type":   room_type,
    }


def _fetch_chunk(token: str, shortname: str, from_d: date, to_d: date) -> list:
    resp = requests.get(
        f"{EVIIVO_API_URL}/property/{shortname}/bookings",
        headers=_headers(token),
        params={
            "request.CheckInFrom": from_d.strftime("%Y-%m-%d"),
            "request.CheckInTo":   to_d.strftime("%Y-%m-%d"),
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("Bookings", data if isinstance(data, list) else [])


def _fetch_property(token: str, venue_name: str, cfg: dict, from_date: date, to_date: date) -> list:
    shortname = cfg["shortname"]
    seen: dict = {}
    chunk_start = from_date

    while chunk_start <= to_date:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), to_date)
        try:
            records = _fetch_chunk(token, shortname, chunk_start, chunk_end)
        except Exception:
            records = []

        for rec in records:
            parsed = _parse_booking(rec, venue_name)
            if parsed and parsed["booking_ref"] not in seen:
                seen[parsed["booking_ref"]] = parsed

        chunk_start = chunk_end + timedelta(days=1)

    return list(seen.values())


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_bookings(from_date: date, to_date: date) -> list:
    """Fetch all bookings (by check-in date) across all properties in parallel."""
    token = get_token()
    all_bookings = []
    errors = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_fetch_property, token, name, cfg, from_date, to_date): name
            for name, cfg in PROPERTIES.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                all_bookings.extend(future.result())
            except Exception as e:
                errors.append(f"{name}: {e}")

    if errors:
        st.warning("Some properties had fetch errors: " + "; ".join(errors))

    return all_bookings


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_blocked_rooms(from_date: date, to_date: date) -> list:
    """Fetch blocked / closed rooms across all properties."""
    token = get_token()
    all_blocks = []

    candidate_endpoints = [
        ("closures",   "request.From",  "request.To"),
        ("blocks",     "request.From",  "request.To"),
        ("roomblocks", "request.From",  "request.To"),
        ("stops",      "request.From",  "request.To"),
        ("closures",   "from",          "to"),
        ("blocks",     "from",          "to"),
    ]

    for venue_name, cfg in PROPERTIES.items():
        shortname = cfg["shortname"]
        fetched = False
        for endpoint, from_key, to_key in candidate_endpoints:
            if fetched:
                break
            try:
                resp = requests.get(
                    f"{EVIIVO_API_URL}/property/{shortname}/{endpoint}",
                    headers=_headers(token),
                    params={
                        from_key: from_date.strftime("%Y-%m-%d"),
                        to_key:   to_date.strftime("%Y-%m-%d"),
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = (
                        data if isinstance(data, list)
                        else data.get("Items") or data.get("Blocks")
                        or data.get("Closures") or data.get("Stops") or []
                    )
                    for item in items:
                        from_d = _safe_date(item.get("From") or item.get("StartDate") or item.get("Start"))
                        to_d   = _safe_date(item.get("To") or item.get("EndDate") or item.get("End"))
                        nights = (to_d - from_d).days if from_d and to_d else 0
                        all_blocks.append({
                            "venue_name": venue_name,
                            "from_date":  from_d,
                            "to_date":    to_d,
                            "nights":     nights,
                            "reason":     (
                                item.get("Reason") or item.get("Description")
                                or item.get("Notes") or item.get("ReasonCode") or "Not specified"
                            ),
                            "rooms":      int(item.get("Rooms") or item.get("NumberOfRooms") or item.get("RoomCount") or 1),
                        })
                    fetched = True
            except Exception:
                continue

    return all_blocks
