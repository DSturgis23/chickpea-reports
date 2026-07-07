"""
Chickpea — Hotel Performance Reports
Standalone Streamlit app pulling data from the Eviivo PMS API.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import warnings

warnings.filterwarnings("ignore")

from config import (
    PROPERTIES, get_room_count, BRAND_GREEN, BRAND_LIGHT, APP_PASSWORD,
    is_pre_opening, is_partial_opening, closure_note, partial_closure_note,
    OPENING_DATES, room_block_notes,
)
from eviivo import fetch_bookings, fetch_bookings_by_stay

import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors as rl_colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import plotly.graph_objects as go

st.set_page_config(
    page_title="Chickpea — Performance Reports",
    page_icon="🏨",
    layout="wide",
)

# ── Auth ─────────────────────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown(f"<h2 style='color:{BRAND_GREEN}'>chickpea. performance reports</h2>", unsafe_allow_html=True)
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if pw == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# ── Styling ───────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
    .header-bar {{
        background:{BRAND_GREEN};color:white;padding:.8rem 1.2rem;
        border-radius:8px;margin-bottom:1rem;
    }}
    .report-note {{
        font-size:.8rem;color:#666;font-style:italic;margin-bottom:.5rem;
    }}
    .stTabs [data-baseweb="tab"] {{ font-size:.9rem; }}
    [data-testid="stMetric"] {{
        background:#f8f9fa;border-radius:6px;padding:.4rem .6rem;
    }}
</style>
""", unsafe_allow_html=True)

st.markdown(
    f'<div class="header-bar"><b>chickpea.</b> &nbsp; hotel performance reports</div>',
    unsafe_allow_html=True,
)

TODAY = date.today()
CY = TODAY.year
LY = CY - 1
PROP_NAMES = sorted(PROPERTIES.keys())

# ── Sidebar ───────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"### Settings")
    if st.button("🔄 Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Data is cached for 1 hour. Today: {TODAY.strftime('%d %b %Y')}")
    st.divider()
    st.caption("Reports pull from Eviivo PMS via API.")


# ── Helpers ───────────────────────────────────────────────────────────────────────

def to_df(bookings: list) -> pd.DataFrame:
    if not bookings:
        return pd.DataFrame()
    df = pd.DataFrame(bookings)
    for col in ["checkin", "checkout", "created"]:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df["nights"]  = pd.to_numeric(df["nights"],  errors="coerce").fillna(0).astype(int)
    return df


def confirmed(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["cancelled"]].copy() if not df.empty else df


def expand_stay_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Expand bookings to one row per night stayed."""
    rows = []
    for _, row in df.iterrows():
        ci, co = row["checkin"], row["checkout"]
        if pd.isna(ci) or pd.isna(co):
            continue
        n = (co - ci).days
        if n <= 0:
            continue
        nightly = row["revenue"] / n
        for i in range(n):
            d = ci + timedelta(days=i)
            rows.append({**row.to_dict(), "stay_date": d, "nightly_revenue": nightly})
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["stay_date"] = pd.to_datetime(out["stay_date"]).dt.date
    return out


def day_type(d: date) -> str:
    wd = d.weekday()  # Mon=0 … Sun=6
    if wd == 6:
        return "Sunday"
    if wd >= 4:
        return "Weekend (Fri–Sat)"
    return "Weekday (Mon–Thu)"


def fmt_gbp(v):
    return f"£{v:,.0f}" if not pd.isna(v) else "—"


def fmt_pct(v):
    if pd.isna(v):
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def fmt_var(v, is_pct=False):
    if pd.isna(v):
        return "—"
    if is_pct:
        return fmt_pct(v)
    sign = "+" if v > 0 else ""
    return f"{sign}£{v:,.0f}"


def avail_nights_in_range(prop: str, dates: list) -> int:
    return sum(get_room_count(prop, d) for d in dates)


def kpi_values(subset_df: pd.DataFrame, dates_list: list, prop: str | None = None) -> dict:
    """Compute raw RNs/Revenue/ADR/Occupancy/RevPAR for a property (or group total)."""
    if prop:
        avail = avail_nights_in_range(prop, dates_list)
        pdf = subset_df[subset_df["venue_name"] == prop] if "venue_name" in subset_df.columns else subset_df
    else:
        avail = sum(avail_nights_in_range(p, dates_list) for p in PROP_NAMES)
        pdf = subset_df
    rns = pdf["num_rooms"].sum() if "num_rooms" in pdf.columns else 0
    rev = pdf["nightly_revenue"].sum() if "nightly_revenue" in pdf.columns else 0
    adr = rev / rns if rns else 0
    occ = rns / avail if avail else 0
    revpar = rev / avail if avail else 0
    return {"rns": rns, "rev": rev, "adr": adr, "occ": occ, "revpar": revpar}


def loading_stay_data(from_d: date, to_d: date) -> pd.DataFrame:
    """Fetch all bookings with any stay night in [from_d, to_d] (in-house guests)."""
    try:
        with st.spinner("Fetching data from Eviivo…"):
            raw = fetch_bookings_by_stay(from_d, to_d)
        return confirmed(to_df(raw))
    except Exception as e:
        st.error(
            "Could not fetch data from Eviivo. "
            "If you are the app owner, check that API credentials are set correctly in Streamlit secrets. "
            f"Error type: {type(e).__name__}"
        )
        return pd.DataFrame()


def loading_data(from_d: date, to_d: date) -> pd.DataFrame:
    try:
        with st.spinner("Fetching data from Eviivo…"):
            raw = fetch_bookings(from_d, to_d)
        return confirmed(to_df(raw))
    except Exception as e:
        st.error(
            "Could not fetch data from Eviivo. "
            "If you are the app owner, check that API credentials are set correctly in Streamlit secrets. "
            f"Error type: {type(e).__name__}"
        )
        return pd.DataFrame()


# ── Month-End Report helpers ────────────────────────────────────────────────────

def _mer_month_options() -> list:
    """Last 25 month-starts, most recent (current, in-progress) month first."""
    cur = date(TODAY.year, TODAY.month, 1)
    return [cur - relativedelta(months=i) for i in range(25)]


def _mer_month_label(m: date) -> str:
    lbl = m.strftime("%B %Y")
    if m.year == TODAY.year and m.month == TODAY.month:
        lbl += "  ⚠️ (in progress)"
    return lbl


def _mer_bounds(m_start: date) -> tuple:
    return m_start, (m_start + relativedelta(months=1)) - timedelta(days=1)


def _mer_lead_bucket(days) -> str:
    if pd.isna(days):
        return "Unknown"
    if days <= 0:
        return "Same day"
    if days <= 7:
        return "1–7 days"
    if days <= 14:
        return "8–14 days"
    if days <= 30:
        return "15–30 days"
    return "31+ days"


def _mer_group_kpi(sdf: pd.DataFrame, dates_list: list, props: list) -> dict:
    """Like kpi_values(), but for an arbitrary subset of properties (like-for-like total)."""
    avail = sum(avail_nights_in_range(p, dates_list) for p in props)
    if "venue_name" in sdf.columns:
        pdf = sdf[sdf["venue_name"].isin(props)]
    else:
        pdf = sdf
    rns = pdf["num_rooms"].sum() if "num_rooms" in pdf.columns else 0
    rev = pdf["nightly_revenue"].sum() if "nightly_revenue" in pdf.columns else 0
    return {
        "rns": rns, "rev": rev,
        "adr": rev / rns if rns else 0,
        "occ": rns / avail if avail else 0,
        "revpar": rev / avail if avail else 0,
    }


def _mer_signal(var_pct) -> str:
    """Same thresholds as the Pace Report's _pace_signal (dashboard.py ~1454)."""
    if var_pct is None:
        return "—"
    if var_pct >= 15:
        return "🟢 Strong"
    if var_pct >= 0:
        return "🟡 Ahead"
    if var_pct >= -15:
        return "🟠 Behind"
    return "🔴 Weak"


def _mer_daytype_split(cy_sdf: pd.DataFrame, ly_sdf: pd.DataFrame, cy_dates: list, ly_dates: list) -> pd.DataFrame:
    """Group-total Occ%/RevPAR split by Weekday/Weekend/Sunday (reuses day_type(), same idea as the
    Weekly/MTD tab's 3-way split, but at group level to keep this section compact)."""
    rows = []
    for dtype in ["Weekday (Mon–Thu)", "Weekend (Fri–Sat)", "Sunday"]:
        cy_days = [d for d in cy_dates if day_type(d) == dtype]
        ly_days = [d for d in ly_dates if day_type(d) == dtype]
        cy_sub = cy_sdf[cy_sdf["stay_date"].apply(day_type) == dtype] if not cy_sdf.empty else cy_sdf
        ly_sub = ly_sdf[ly_sdf["stay_date"].apply(day_type) == dtype] if not ly_sdf.empty else ly_sdf
        cy_v = kpi_values(cy_sub, cy_days, None) if cy_days else None
        ly_v = kpi_values(ly_sub, ly_days, None) if ly_days else None
        rows.append({
            "Day Type": dtype,
            "Occ % (CY)": f"{cy_v['occ']*100:.1f}%" if cy_v else "—",
            "RevPAR (CY)": fmt_gbp(cy_v["revpar"]) if cy_v else "—",
            "Occ % (LY)": f"{ly_v['occ']*100:.1f}%" if ly_v else "—",
            "RevPAR (LY)": fmt_gbp(ly_v["revpar"]) if ly_v else "—",
        })
    return pd.DataFrame(rows)


def _mer_trailing_trend(cy_start: date, months: int = 6) -> pd.DataFrame:
    """Group-total Occ%/RevPAR for the trailing `months` (CY line) and their LY equivalents.
    Reuses loading_stay_data/kpi_values — the current month's call hits the same cache key
    already warmed by the main KPI section above, so this only adds (months-1) new fetches."""
    rows = []
    for i in range(months - 1, -1, -1):
        m_start = cy_start - relativedelta(months=i)
        m_end = (m_start + relativedelta(months=1)) - timedelta(days=1)
        ly_m_start = m_start - relativedelta(years=1)
        ly_m_end = m_end - relativedelta(years=1)
        m_dates = [m_start + timedelta(days=d) for d in range((m_end - m_start).days + 1)]
        ly_m_dates = [ly_m_start + timedelta(days=d) for d in range((ly_m_end - ly_m_start).days + 1)]

        cy_sdf_i = expand_stay_dates(loading_stay_data(m_start, m_end))
        ly_sdf_i = expand_stay_dates(loading_stay_data(ly_m_start, ly_m_end))
        if not cy_sdf_i.empty:
            cy_sdf_i = cy_sdf_i[(cy_sdf_i["stay_date"] >= m_start) & (cy_sdf_i["stay_date"] <= m_end)]
        if not ly_sdf_i.empty:
            ly_sdf_i = ly_sdf_i[(ly_sdf_i["stay_date"] >= ly_m_start) & (ly_sdf_i["stay_date"] <= ly_m_end)]

        cy_v = kpi_values(cy_sdf_i, m_dates, None)
        ly_v = kpi_values(ly_sdf_i, ly_m_dates, None)
        rows.append({
            "month": m_start.strftime("%b %Y"),
            "cy_occ": cy_v["occ"] * 100, "ly_occ": ly_v["occ"] * 100,
            "cy_revpar": cy_v["revpar"], "ly_revpar": ly_v["revpar"],
        })
    return pd.DataFrame(rows)


def _mer_narrative(kpi_rows, lt_cy, lt_ly, cancel_cy, cancel_ly, cy_start, ly_start) -> str:
    """Templated (not LLM-generated) 3-4 sentence summary off numbers already computed above."""
    prop_rows = [r for r in kpi_rows if r["cy"] and r["ly"] and not r["property"].startswith("GROUP TOTAL")]
    lfl_row = next((r for r in kpi_rows if r["property"].startswith("GROUP TOTAL (like-for-like")), None)

    sentences = []
    if lfl_row and lfl_row["cy"] and lfl_row["ly"] and lfl_row["ly"]["rev"]:
        grp_yoy = (lfl_row["cy"]["rev"] - lfl_row["ly"]["rev"]) / lfl_row["ly"]["rev"] * 100
        direction = "up" if grp_yoy >= 0 else "down"
        sentences.append(
            f"Group like-for-like revenue is {direction} {abs(grp_yoy):.1f}% vs "
            f"{ly_start:%B %Y}."
        )

    # Rank on RevPAR YoY, not raw Revenue YoY — Revenue/RNs scale mechanically with room
    # count, which would make a property that gained rooms (e.g. Queen's Head 4→9) look
    # like the "best performer" for capacity reasons alone. RevPAR is already normalized
    # per available room-night via get_room_count(), so it isolates real trading performance.
    # Properties with a room-count change vs LY are excluded from ranking entirely and
    # called out separately, since even RevPAR can be transitionally noisy right after
    # a room-count change (e.g. new rooms still ramping up to full occupancy).
    room_changed_props = [r["property"] for r in prop_rows if r.get("room_changed")]
    ranked_rows = [r for r in prop_rows if not r.get("room_changed")]

    yoys = []
    for r in ranked_rows:
        if r["ly"]["revpar"]:
            yoys.append((r["property"], (r["cy"]["revpar"] - r["ly"]["revpar"]) / r["ly"]["revpar"] * 100))
    if yoys:
        yoys.sort(key=lambda x: x[1], reverse=True)
        best = yoys[0]
        worst = yoys[-1]
        if best[1] != worst[1]:
            sentences.append(
                f"On RevPAR, {best[0]} has the strongest growth ({'+' if best[1] >= 0 else ''}{best[1]:.1f}%), "
                f"while {worst[0]} is trading furthest behind last year "
                f"({'+' if worst[1] >= 0 else ''}{worst[1]:.1f}%)."
            )
    if room_changed_props:
        plural = len(room_changed_props) > 1
        sentences.append(
            f"{', '.join(room_changed_props)} {'have' if plural else 'has'} a different room count than "
            f"{ly_start:%B %Y}, so {'they are' if plural else 'it is'} excluded from the comparison above — "
            f"Revenue/RNs growth for {'them' if plural else 'it'} reflects added capacity, not just trading performance."
        )

    if lt_cy["avg"] is not None and lt_ly["avg"] is not None:
        delta = lt_cy["avg"] - lt_ly["avg"]
        trend = "shortened" if delta < 0 else "lengthened"
        sentences.append(
            f"Average booking lead time has {trend} by {abs(delta):.1f} days compared with "
            f"{ly_start:%B %Y}."
        )

    if cancel_cy["n"] or cancel_ly["n"]:
        cancel_delta = cancel_cy["n"] - cancel_ly["n"]
        cancel_dir = "up" if cancel_delta > 0 else ("down" if cancel_delta < 0 else "unchanged")
        sentences.append(
            f"Cancellations are {cancel_dir} {abs(cancel_delta)} bookings on the same point last year "
            f"({cancel_cy['n']} vs {cancel_ly['n']})."
        )

    if not sentences:
        return "Not enough comparable data to generate a summary for this month."
    return " ".join(sentences)


def _mer_chart_png(fig, scale: float = 2.0) -> bytes:
    """Bridge: rasterize an existing Plotly figure to PNG bytes via kaleido, for PDF embedding."""
    return fig.to_image(format="png", scale=scale)


def _mer_kpi_rows(cy_sdf, ly_sdf, cy_dates, ly_dates, cy_ym, ly_ym) -> list:
    """Per-property + group-total + like-for-like-total KPI rows with YoY.

    Skips kpi_values() for any property/side that was pre-opening or fully closed —
    config.get_room_count() doesn't know about OPENING_DATES/KNOWN_CLOSURES, so trusting
    it there would silently report near-zero occupancy instead of "not open".
    """
    rows, lfl_props = [], []
    for prop in PROP_NAMES:
        cy_blocked = is_pre_opening(prop, *cy_ym) or closure_note(prop, *cy_ym)
        ly_blocked = is_pre_opening(prop, *ly_ym) or closure_note(prop, *ly_ym)
        cy_v = None if cy_blocked else kpi_values(cy_sdf, cy_dates, prop)
        ly_v = None if ly_blocked else kpi_values(ly_sdf, ly_dates, prop)
        if not cy_blocked and not ly_blocked:
            lfl_props.append(prop)

        note_parts = []
        for n in (closure_note(prop, *cy_ym), closure_note(prop, *ly_ym),
                  partial_closure_note(prop, *cy_ym), partial_closure_note(prop, *ly_ym)):
            if n and n not in note_parts:
                note_parts.append(n)

        # Room-count changes (e.g. Queen's Head 4→9 rooms) inflate raw Revenue/RNs YoY
        # mechanically — Occ%/RevPAR are already room-count-normalized via get_room_count(),
        # but flag it explicitly so Revenue/RNs aren't misread as like-for-like performance.
        room_changed = get_room_count(prop, cy_dates[0]) != get_room_count(prop, ly_dates[0])
        if room_changed:
            cy_rooms, ly_rooms = get_room_count(prop, cy_dates[0]), get_room_count(prop, ly_dates[0])
            note_parts.append(
                f"Room count changed ({ly_rooms}→{cy_rooms} rooms) — Revenue/RNs/YoY Rev% "
                f"not like-for-like; Occ%/RevPAR already account for this"
            )

        rows.append({
            "property": prop, "cy": cy_v, "ly": ly_v, "note": "; ".join(note_parts),
            "room_changed": room_changed,
        })

    rows.append({
        "property": "GROUP TOTAL (all trading properties)",
        "cy": kpi_values(cy_sdf, cy_dates, None),
        "ly": kpi_values(ly_sdf, ly_dates, None),
        "note": "Includes newly-opened properties",
        "room_changed": False,
    })
    if lfl_props:
        rows.append({
            "property": f"GROUP TOTAL (like-for-like, {len(lfl_props)} properties)",
            "cy": _mer_group_kpi(cy_sdf, cy_dates, lfl_props),
            "ly": _mer_group_kpi(ly_sdf, ly_dates, lfl_props),
            "note": "",
            "room_changed": False,
        })
    return rows


def _mer_kpi_table_rows(kpi_rows: list) -> list:
    """Formatted [Property, RNs, Revenue, ADR, Occ%, RevPAR, YoY Rev%, YoY Occ, Signal, Note] rows."""
    out = []
    for r in kpi_rows:
        cy, ly, note = r["cy"], r["ly"], r["note"]
        notes = []
        if cy is None:
            notes.append("Not open this month")
        if ly is None and cy is not None:
            notes.append("No LY data — not open last year")
        if note:
            notes.append(note)

        rev_yoy = occ_yoy_pp = None
        if cy and ly and ly["rev"]:
            rev_yoy = (cy["rev"] - ly["rev"]) / ly["rev"] * 100
        if cy and ly and cy["occ"] is not None and ly["occ"] is not None:
            occ_yoy_pp = (cy["occ"] - ly["occ"]) * 100

        out.append([
            r["property"],
            f"{cy['rns']:.0f}" if cy else "N/A",
            fmt_gbp(cy["rev"]) if cy else "N/A",
            fmt_gbp(cy["adr"]) if cy else "N/A",
            f"{cy['occ']*100:.1f}%" if cy else "N/A",
            fmt_gbp(cy["revpar"]) if cy else "N/A",
            fmt_pct(rev_yoy) if rev_yoy is not None else "N/A",
            (f"{'+' if occ_yoy_pp >= 0 else ''}{occ_yoy_pp:.1f}pp" if occ_yoy_pp is not None else "N/A"),
            _mer_signal(rev_yoy),
            "; ".join(notes),
        ])
    return out


def _mer_source_mix(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["channel", "Bookings", "Room_Nights", "Revenue", "ADR", "Rev %"])
    ch = df.groupby("channel").agg(
        Bookings=("booking_ref", "count"),
        Room_Nights=("nights", "sum"),
        Revenue=("revenue", "sum"),
    ).reset_index()
    ch["ADR"] = ch["Revenue"] / ch["Room_Nights"].replace(0, np.nan)
    total_rev = ch["Revenue"].sum()
    ch["Rev %"] = (ch["Revenue"] / total_rev * 100) if total_rev else 0
    return ch.sort_values("Revenue", ascending=False)


def _mer_leadtime_stats(df: pd.DataFrame) -> dict:
    empty = {"avg": None, "median": None, "pct_7": None, "pct_14": None, "pct_30": None, "n": 0}
    if df.empty:
        return empty
    d = df.copy()
    d["lead_time"] = (pd.to_datetime(d["checkin"]) - pd.to_datetime(d["created"])).dt.days
    d = d[d["lead_time"].notna() & (d["lead_time"] >= 0)]
    if d.empty:
        return empty
    return {
        "avg": d["lead_time"].mean(),
        "median": d["lead_time"].median(),
        "pct_7": (d["lead_time"] <= 7).mean() * 100,
        "pct_14": (d["lead_time"] <= 14).mean() * 100,
        "pct_30": (d["lead_time"] <= 30).mean() * 100,
        "n": len(d),
    }


def _mer_leadtime_buckets(df: pd.DataFrame) -> pd.DataFrame:
    order = ["Same day", "1–7 days", "8–14 days", "15–30 days", "31+ days"]
    if df.empty:
        return pd.DataFrame({"Lead Time": order, "Bookings": [0] * len(order)})
    d = df.copy()
    d["lead_time"] = (pd.to_datetime(d["checkin"]) - pd.to_datetime(d["created"])).dt.days
    d = d[d["lead_time"].notna() & (d["lead_time"] >= 0)]
    if d.empty:
        return pd.DataFrame({"Lead Time": order, "Bookings": [0] * len(order)})
    d["bucket"] = d["lead_time"].apply(_mer_lead_bucket)
    out = d.groupby("bucket").size().reindex(order, fill_value=0).reset_index()
    out.columns = ["Lead Time", "Bookings"]
    return out


def _mer_cancellation_stats(raw_df: pd.DataFrame) -> dict:
    empty_breakdown = pd.DataFrame(columns=["group", "Bookings", "Revenue"])
    if raw_df.empty or "cancelled" not in raw_df.columns:
        return {"n": 0, "rns": 0, "rev": 0.0, "pct": None, "total": 0,
                "by_channel": empty_breakdown, "by_property": empty_breakdown}
    cx = raw_df[raw_df["cancelled"]]
    total = len(raw_df)
    by_channel = (
        cx.groupby("channel").agg(Bookings=("booking_ref", "count"), Revenue=("revenue", "sum"))
        .reset_index().rename(columns={"channel": "group"}).sort_values("Revenue", ascending=False)
        if not cx.empty else empty_breakdown
    )
    by_property = (
        cx.groupby("venue_name").agg(Bookings=("booking_ref", "count"), Revenue=("revenue", "sum"))
        .reset_index().rename(columns={"venue_name": "group"}).sort_values("Revenue", ascending=False)
        if not cx.empty else empty_breakdown
    )
    return {
        "n": len(cx),
        "rns": int(cx["nights"].sum()),
        "rev": float(cx["revenue"].sum()),
        "pct": (len(cx) / total * 100) if total else None,
        "total": total,
        "by_channel": by_channel,
        "by_property": by_property,
    }


def _mer_blocked_stats(blk_df: pd.DataFrame, month_start: date, month_end: date) -> dict:
    if blk_df.empty:
        return {"available": False, "n": 0, "rns": 0}
    covers = blk_df["from_date"].min() <= month_start
    active = blk_df[blk_df["deleted_on"] == ""]
    in_range = active[
        (active["from_date"] <= month_end) & (active["to_date"] > month_start)
    ].copy()
    in_range["nights"] = (
        pd.to_datetime(in_range["to_date"]) - pd.to_datetime(in_range["from_date"])
    ).dt.days
    return {"available": covers, "n": len(in_range), "rns": int(in_range["nights"].sum())}


# ── Month-End Report notes (persisted to GitHub, same pattern as the Pick-up Report's
# rate-change log — Streamlit Cloud's local filesystem doesn't survive restarts/redeploys,
# so anything a user types in needs to round-trip through the repo instead of a local file) ──
_MER_NOTES_COLS   = ["month", "notes", "updated_at"]
_MER_NOTES_GHPATH = "data/month_end_notes.csv"


def _load_mer_notes() -> pd.DataFrame:
    import base64, requests as _req
    try:
        resp = _req.get(
            f"https://api.github.com/repos/{_RATE_LOG_REPO}/contents/{_MER_NOTES_GHPATH}",
            headers=_gh_headers(), timeout=15,
        )
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode()
            from io import StringIO
            return pd.read_csv(StringIO(content), dtype=str).fillna("")
    except Exception:
        pass
    return pd.DataFrame(columns=_MER_NOTES_COLS)


def _save_mer_notes(df: pd.DataFrame):
    import base64, requests as _req
    content = df.to_csv(index=False)
    encoded = base64.b64encode(content.encode()).decode()
    url = f"https://api.github.com/repos/{_RATE_LOG_REPO}/contents/{_MER_NOTES_GHPATH}"
    get_resp = _req.get(url, headers=_gh_headers(), timeout=15)
    payload = {"message": "Update month-end report notes", "content": encoded}
    if get_resp.status_code == 200:
        payload["sha"] = get_resp.json()["sha"]
    put_resp = _req.put(url, headers=_gh_headers(), json=payload, timeout=15)
    if put_resp.status_code not in (200, 201):
        st.error(f"Could not save notes to GitHub ({put_resp.status_code}). Check github_token in secrets.")
        return False
    return True


def _mer_get_note(notes_df: pd.DataFrame, month_key: str) -> str:
    if notes_df.empty:
        return ""
    match = notes_df[notes_df["month"] == month_key]
    return match.iloc[-1]["notes"] if not match.empty else ""


def _mer_set_note(notes_df: pd.DataFrame, month_key: str, text: str) -> pd.DataFrame:
    df = notes_df[notes_df["month"] != month_key].copy() if not notes_df.empty else pd.DataFrame(columns=_MER_NOTES_COLS)
    new_row = pd.DataFrame([{
        "month": month_key, "notes": text,
        "updated_at": date.today().strftime("%Y-%m-%d"),
    }])
    return pd.concat([df, new_row], ignore_index=True)


def _mer_build_pdf(
    cy_start, ly_start, is_partial, narrative, notes_text,
    kpi_pdf_rows, daytype_rows,
    source_cy, source_ly,
    lt_cy, lt_ly, cancel_cy, cancel_ly, block_cy, block_ly,
    chart_pngs: dict,
) -> bytes:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("MERTitle", parent=styles["Title"], textColor=rl_colors.HexColor(BRAND_GREEN))
    h2 = ParagraphStyle("MERH2", parent=styles["Heading2"], textColor=rl_colors.HexColor(BRAND_GREEN), spaceBefore=14)
    note_style = ParagraphStyle("MERNote", parent=styles["BodyText"], fontSize=8, textColor=rl_colors.grey, spaceAfter=6)
    narrative_style = ParagraphStyle("MERNarrative", parent=styles["BodyText"], fontSize=9.5, spaceAfter=6)

    def table(header, rows, widths=None):
        t = Table([header] + rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor(BRAND_GREEN)),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, rl_colors.HexColor(BRAND_LIGHT)]),
            ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#999999")),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def chart_image(key, width=170 * mm, height=85 * mm):
        png = chart_pngs.get(key)
        if not png:
            return None
        return Image(io.BytesIO(png), width=width, height=height)

    story = [
        Paragraph("Chickpea — Month-End Performance Report", title_style),
        Paragraph(f"{cy_start:%B %Y} vs {ly_start:%B %Y}", styles["Normal"]),
    ]
    if is_partial:
        story.append(Paragraph(
            "⚠ PARTIAL MONTH — this report was run before month-end; figures will change.",
            ParagraphStyle("MERWarn", parent=note_style, textColor=rl_colors.HexColor("#CC2200"))))
    story.append(Paragraph(f"Generated {date.today():%d %b %Y}", note_style))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(narrative, narrative_style))
    story.append(Spacer(1, 4 * mm))

    if notes_text and notes_text.strip():
        story.append(Paragraph("Observations", h2))
        for para in notes_text.strip().split("\n"):
            if para.strip():
                story.append(Paragraph(para, narrative_style))
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Headline KPIs", h2))
    cell_style = ParagraphStyle("MERCell", fontName="Helvetica", fontSize=7.5, leading=9)
    kpi_col_widths = [w * mm for w in (34, 11, 16, 12, 12, 14, 14, 14, 16, 27)]
    kpi_rows_wrapped = [
        [Paragraph(str(row[0]), cell_style), *row[1:-1], Paragraph(str(row[-1]), cell_style)]
        for row in kpi_pdf_rows
    ]
    story.append(table(
        ["Property", "RNs", "Revenue", "ADR", "Occ%", "RevPAR", "YoY Rev%", "YoY Occ", "Signal", "Note"],
        kpi_rows_wrapped,
        widths=kpi_col_widths,
    ))
    story.append(Paragraph(
        "Like-for-like total excludes properties not open in both the current and prior-year month.",
        note_style))

    if daytype_rows:
        story.append(Paragraph("Weekday / Weekend / Sunday Split (group total)", h2))
        story.append(table(
            ["Day Type", "Occ % (CY)", "RevPAR (CY)", "Occ % (LY)", "RevPAR (LY)"],
            daytype_rows,
        ))

    trend_occ_img = chart_image("trend_occ", height=70 * mm)
    trend_revpar_img = chart_image("trend_revpar", height=70 * mm)
    if trend_occ_img or trend_revpar_img:
        story.append(Paragraph("6-Month Trend — Occupancy & RevPAR", h2))
        if trend_occ_img:
            story.append(trend_occ_img)
        if trend_revpar_img:
            story.append(Spacer(1, 3 * mm))
            story.append(trend_revpar_img)

    story.append(PageBreak())

    def source_table_rows(sdf):
        rows = []
        for _, r in sdf.iterrows():
            rows.append([r["channel"], f"{int(r['Bookings'])}", f"{int(r['Room_Nights'])}",
                         fmt_gbp(r["Revenue"]), fmt_gbp(r["ADR"]) if not pd.isna(r["ADR"]) else "—",
                         f"{r['Rev %']:.1f}%"])
        return rows

    story.append(Paragraph(f"Booking Source Mix — {cy_start:%B %Y}", h2))
    story.append(table(["Channel", "Bookings", "Room Nights", "Revenue", "ADR", "Rev %"],
                        source_table_rows(source_cy)))
    cy_pie = chart_image("source_pie_cy", width=85 * mm, height=70 * mm)
    ly_pie = chart_image("source_pie_ly", width=85 * mm, height=70 * mm)
    if cy_pie or ly_pie:
        story.append(Table([[cy_pie or "", ly_pie or ""]], colWidths=[85 * mm, 85 * mm]))
    story.append(Paragraph(f"Booking Source Mix — {ly_start:%B %Y} (LY)", h2))
    story.append(table(["Channel", "Bookings", "Room Nights", "Revenue", "ADR", "Rev %"],
                        source_table_rows(source_ly)))
    story.append(Paragraph(
        "Source mix is based on check-in date, unlike the stay-date-based KPIs above — "
        "the two sections will not reconcile exactly for stays spanning month-end.", note_style))

    lt_header = ["Metric", f"{cy_start:%b %Y}", f"{ly_start:%b %Y} (LY)"]
    def fmt_lt(v, pct=False):
        if v is None:
            return "N/A"
        return f"{v:.1f}%" if pct else f"{v:.1f} days"
    lt_rows = [
        ["Avg lead time", fmt_lt(lt_cy["avg"]), fmt_lt(lt_ly["avg"])],
        ["Median lead time", fmt_lt(lt_cy["median"]), fmt_lt(lt_ly["median"])],
        ["Booked within 7 days", fmt_lt(lt_cy["pct_7"], True), fmt_lt(lt_ly["pct_7"], True)],
        ["Booked within 14 days", fmt_lt(lt_cy["pct_14"], True), fmt_lt(lt_ly["pct_14"], True)],
        ["Booked within 30 days", fmt_lt(lt_cy["pct_30"], True), fmt_lt(lt_ly["pct_30"], True)],
    ]
    story.append(Paragraph("Booking Lead Time / Pace", h2))
    story.append(table(lt_header, lt_rows))
    lt_chart = chart_image("leadtime")
    if lt_chart:
        story.append(Spacer(1, 3 * mm))
        story.append(lt_chart)

    story.append(PageBreak())

    story.append(Paragraph("Cancellations", h2))
    def fmt_cancel_row(label, c):
        return [label, str(c["n"]), str(c["rns"]), fmt_gbp(c["rev"]),
                f"{c['pct']:.1f}%" if c["pct"] is not None else "N/A"]
    story.append(table(
        ["Period", "Cancelled Bookings", "Room-Nights Lost", "Revenue Lost", "% of Bookings"],
        [fmt_cancel_row(f"{cy_start:%b %Y}", cancel_cy), fmt_cancel_row(f"{ly_start:%b %Y} (LY)", cancel_ly)],
    ))
    if not cancel_cy["by_channel"].empty:
        story.append(Paragraph(f"Cancellations by Channel — {cy_start:%B %Y}", h2))
        story.append(table(
            ["Channel", "Bookings", "Revenue"],
            [[r["group"], str(int(r["Bookings"])), fmt_gbp(r["Revenue"])]
             for _, r in cancel_cy["by_channel"].iterrows()],
        ))
    if not cancel_cy["by_property"].empty:
        story.append(Paragraph(f"Cancellations by Property — {cy_start:%B %Y}", h2))
        story.append(table(
            ["Property", "Bookings", "Revenue"],
            [[r["group"], str(int(r["Bookings"])), fmt_gbp(r["Revenue"])]
             for _, r in cancel_cy["by_property"].iterrows()],
        ))
    story.append(Paragraph(
        "Reflects bookings for check-ins in this month currently marked cancelled as of today — "
        "Eviivo does not expose a cancellation date, so this cannot show cancellations that "
        "occurred during the month itself, and may differ from a report run on a different day.",
        note_style))

    story.append(Paragraph("Blocked Rooms", h2))
    def block_row(label, b):
        if not b["available"]:
            return [label, "No data available", "—"]
        return [label, str(b["n"]), str(b["rns"])]
    story.append(table(
        ["Period", "Blocks", "Room-Nights Blocked"],
        [block_row(f"{cy_start:%b %Y}", block_cy), block_row(f"{ly_start:%b %Y} (LY)", block_ly)],
    ))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(rl_colors.HexColor(BRAND_GREEN))
        canvas.line(16 * mm, 14 * mm, A4[0] - 16 * mm, 14 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(rl_colors.grey)
        canvas.drawString(16 * mm, 9 * mm, "Chickpea — Confidential")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
        title=f"Month-End Report {cy_start:%B %Y}",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════════════════════
# TABS
# ════════════════════════════════════════════════════════════════════════════════
tabs = st.tabs([
    "📅 Monthly Revenue",
    "📈 Weekly / MTD",
    "🔀 Source Report",
    "💰 Rate Plans",
    "🚫 Blocked Rooms",
    "🔭 Pace Report",
    "📡 Pick-up Report",
    "🪟 Booking Window",
    "💼 Potential Corporate Guests",
    "📋 Month-End Report",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Monthly Revenue by Property (YoY)
# ─────────────────────────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Monthly Accommodation Revenue by Property")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Total gross accommodation revenue (inc. VAT) per property per month,
with a year-on-year comparison.

**Revenue table:** Actual revenue for completed months. Future months (marked `*`) show
confirmed bookings already on the books — useful for forecasting but not yet earned.
The **YTD Total** column covers completed months only.

**Year-on-year table:** How much more or less each property made vs the same month last year.
Months affected by closures or a property not yet being open are flagged and left out of the
total so the comparison stays fair.

**Charts:** Monthly group revenue trend (both years side by side) and a per-property YTD bar
to show which venues are driving growth.
        """)

    col1, col2 = st.columns([1, 3])
    with col1:
        yr_a = st.selectbox("Year", [CY, LY, CY - 2], index=0, key="mr_yr_a")
        yr_b = st.selectbox("Compare with", [LY, CY - 2, CY - 3], index=0, key="mr_yr_b")

    df_a = loading_data(date(yr_a, 1, 1), date(yr_a, 12, 31))
    df_b = loading_data(date(yr_b, 1, 1), date(yr_b, 12, 31))

    MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    last_complete_month = TODAY.month - 1 if TODAY.day < 28 else TODAY.month
    if last_complete_month == 0:
        last_complete_month = 12

    def monthly_pivot(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(0.0, index=PROP_NAMES, columns=range(1, 13))
        df = df.copy()
        df["month"] = pd.to_datetime(df["checkin"], errors="coerce").dt.month
        return (
            df.groupby(["venue_name", "month"])["revenue"]
            .sum()
            .unstack(fill_value=0.0)
            .reindex(index=PROP_NAMES, columns=range(1, 13), fill_value=0.0)
        )

    piv_a = monthly_pivot(df_a)
    piv_b = monthly_pivot(df_b)

    def _val(piv, prop, m):
        if prop in piv.index and m in piv.columns:
            return float(piv.loc[prop, m])
        return 0.0

    compare_months = list(range(1, last_complete_month + 1)) if yr_a == CY else list(range(1, 13))
    compare_label  = f"Jan–{MONTHS[last_complete_month - 1]}" if yr_a == CY else "Full year"

    # ── Group headline metrics (top of page) ──────────────────────────────────
    total_a = sum(_val(piv_a, p, m) for p in PROP_NAMES for m in compare_months)
    total_b = sum(_val(piv_b, p, m) for p in PROP_NAMES for m in compare_months)
    diff_g  = total_a - total_b
    pct_g   = (diff_g / total_b * 100) if total_b else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Group Revenue {yr_a} ({compare_label})", fmt_gbp(total_a))
    c2.metric(f"Group Revenue {yr_b} ({compare_label})", fmt_gbp(total_b))
    c3.metric("Variance (£)", fmt_var(diff_g))
    c4.metric("Variance (%)", fmt_pct(pct_g))

    st.divider()

    # ── Revenue table ─────────────────────────────────────────────────────────
    st.markdown(f"#### {yr_a} Revenue by Property")
    if yr_a == CY:
        st.caption(
            f"Jan–{MONTHS[last_complete_month - 1]} = actual earned revenue. "
            f"{MONTHS[last_complete_month]}–Dec* = forward bookings on the books (not yet earned)."
        )

    rev_rows = []
    for prop in PROP_NAMES:
        row = {"Property": prop}
        for m in range(1, 13):
            v = _val(piv_a, prop, m)
            if yr_a == CY and m > last_complete_month:
                row[MONTHS[m - 1]] = f"{fmt_gbp(v)} *" if v else "—"
            else:
                row[MONTHS[m - 1]] = fmt_gbp(v) if v else "—"
        ytd = sum(_val(piv_a, prop, m) for m in compare_months)
        row["YTD Total"] = fmt_gbp(ytd)
        rev_rows.append(row)

    st.dataframe(pd.DataFrame(rev_rows), hide_index=True, use_container_width=True)
    if yr_a == CY:
        st.caption("* = forward bookings. YTD Total = completed months only.")

    # ── YoY variance table ────────────────────────────────────────────────────
    st.markdown(f"#### Year-on-Year vs {yr_b}  —  {compare_label}")
    st.caption(
        f"How much more (+) or less (−) each property made vs the same month in {yr_b}. "
        f"Months affected by closures or a property not yet open are flagged and excluded from the total."
    )

    var_rows = []
    for prop in PROP_NAMES:
        vrow = {"Property": prop}
        skip_months = set()
        for m in compare_months:
            flag = None
            if is_pre_opening(prop, yr_b, m):
                flag = "not open"
                skip_months.add(m)
            elif is_partial_opening(prop, yr_b, m):
                flag = "partial open"
                skip_months.add(m)
            cn_a = closure_note(prop, yr_a, m)
            cn_b = closure_note(prop, yr_b, m)
            if cn_a or cn_b:
                flag = "⚠ closed"
                skip_months.add(m)

            pc_a = partial_closure_note(prop, yr_a, m)
            pc_b = partial_closure_note(prop, yr_b, m)

            if flag:
                vrow[MONTHS[m - 1]] = flag
            else:
                va   = _val(piv_a, prop, m)
                vb   = _val(piv_b, prop, m)
                diff = va - vb
                pct  = (diff / vb * 100) if vb else (100.0 if va else 0.0)
                sign = "+" if diff >= 0 else ""
                cell = f"{sign}£{abs(diff):,.0f}  ({fmt_pct(pct)})"
                if pc_a or pc_b:
                    cell += " †"
                vrow[MONTHS[m - 1]] = cell

        valid = [m for m in compare_months if m not in skip_months]
        va_t  = sum(_val(piv_a, prop, m) for m in valid)
        vb_t  = sum(_val(piv_b, prop, m) for m in valid)
        dt    = va_t - vb_t
        pt    = (dt / vb_t * 100) if vb_t else 0.0
        excl  = f"  ({len(skip_months)} mo. excluded)" if skip_months else ""
        sign  = "+" if dt >= 0 else ""
        vrow["Total"] = f"{sign}£{abs(dt):,.0f}  ({fmt_pct(pt)}){excl}"
        var_rows.append(vrow)

    yoy_cols = ["Property"] + [MONTHS[m - 1] for m in compare_months] + ["Total"]
    st.dataframe(pd.DataFrame(var_rows)[yoy_cols], hide_index=True, use_container_width=True)
    st.caption(
        "**not open** = property wasn't trading yet  ·  "
        "**⚠ closed** = full closure, excluded from total  ·  "
        "**†** = partial closure (figures included but may not be directly comparable)"
    )

    # ── Charts ────────────────────────────────────────────────────────────────
    import plotly.graph_objects as go

    st.divider()
    st.markdown("#### Charts")

    # Chart 1: Monthly group revenue — CY vs LY
    grp_a = [sum(_val(piv_a, p, m) for p in PROP_NAMES) for m in range(1, 13)]
    grp_b = [sum(_val(piv_b, p, m) for p in PROP_NAMES) for m in range(1, 13)]

    fig_monthly = go.Figure([
        go.Bar(name=str(yr_b), x=MONTHS, y=grp_b, marker_color=BRAND_LIGHT),
        go.Bar(name=str(yr_a), x=MONTHS, y=grp_a, marker_color=BRAND_GREEN),
    ])
    fig_monthly.update_layout(
        barmode="group",
        title=f"Monthly Group Revenue — {yr_a} vs {yr_b}",
        yaxis_title="Revenue (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=20), height=350,
    )
    st.plotly_chart(fig_monthly, use_container_width=True)

    # Chart 2: YTD revenue by property — CY vs LY (horizontal bars)
    ytd_a = [sum(_val(piv_a, p, m) for m in compare_months) for p in PROP_NAMES]
    ytd_b = [sum(_val(piv_b, p, m) for m in compare_months) for p in PROP_NAMES]
    # Shorten property names for chart
    short_names = [p.replace("The ", "") for p in PROP_NAMES]

    fig_ytd = go.Figure([
        go.Bar(name=f"{yr_b} ({compare_label})", y=short_names, x=ytd_b,
               orientation="h", marker_color=BRAND_LIGHT),
        go.Bar(name=f"{yr_a} ({compare_label})", y=short_names, x=ytd_a,
               orientation="h", marker_color=BRAND_GREEN),
    ])
    fig_ytd.update_layout(
        barmode="group",
        title=f"YTD Revenue by Property ({compare_label}) — {yr_a} vs {yr_b}",
        xaxis_title="Revenue (£)", xaxis_tickprefix="£", xaxis_tickformat=",.0f",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=20), height=380,
    )
    st.plotly_chart(fig_ytd, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Weekly / MTD Performance
# ─────────────────────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("Weekly & Month-to-Date Performance")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Key performance metrics for a chosen date range, split by day type
(Weekday, Weekend, Sunday) and by property.

**Metrics explained:**
- **Room Nights Sold** — total number of room-nights occupied in the period (e.g. a 3-night stay = 3 room-nights)
- **Revenue** — total gross revenue (inc. VAT) attributed to those nights, split proportionally across each night of the stay
- **ADR (Average Daily Rate)** — Revenue ÷ Room Nights Sold. The average rate charged per occupied room per night
- **Occupancy %** — Room Nights Sold ÷ Room Nights Available. Shows what percentage of available rooms were filled. 100% = fully booked every night
- **RevPAR (Revenue per Available Room)** — Revenue ÷ Room Nights Available. The single most important hotel KPI — it captures both rate and occupancy together. ADR × Occupancy = RevPAR

**Day type split:**
- **Weekday (Mon–Thu)** — typically lower demand, more likely to include corporate/mid-week bookings
- **Weekend (Fri–Sat)** — typically highest demand and rates
- **Sunday** — often sits between the two; useful to track separately

**Tip:** To see month-to-date figures, set the date range to the 1st of the current month through today.
        """)


    c1, c2 = st.columns(2)
    with c1:
        wk_from = st.date_input("From", value=TODAY - timedelta(days=TODAY.weekday() + 7), key="wk_from")
    with c2:
        wk_to   = st.date_input("To",   value=TODAY, key="wk_to")

    df_wk = loading_stay_data(wk_from, wk_to)

    if df_wk.empty:
        st.info("No confirmed bookings in this period.")
    else:
        sdf = expand_stay_dates(df_wk)
        if sdf.empty:
            st.info("Could not expand stay dates (checkout dates may be missing from API).")
        else:
            sdf = sdf[(sdf["stay_date"] >= wk_from) & (sdf["stay_date"] <= wk_to)]
            sdf["day_type"] = sdf["stay_date"].apply(day_type)

            all_dates  = [wk_from + timedelta(days=i) for i in range((wk_to - wk_from).days + 1)]

            for prop in PROP_NAMES:
                for note in room_block_notes(prop, all_dates):
                    st.info(f"**{prop}**: {note} — excluded from available room-nights below.")

            def kpi_table(subset_df, dates_list):
                results = []
                for prop in PROP_NAMES:
                    v = kpi_values(subset_df, dates_list, prop)
                    results.append({
                        "Property":        prop,
                        "Room Nights Sold": int(v["rns"]),
                        "Revenue":         fmt_gbp(v["rev"]),
                        "ADR":             fmt_gbp(v["adr"]),
                        "Occupancy":       f"{v['occ']*100:.1f}%",
                        "RevPAR":          fmt_gbp(v["revpar"]),
                    })
                # Group total
                vt = kpi_values(subset_df, dates_list, None)
                results.append({
                    "Property":        "GROUP TOTAL",
                    "Room Nights Sold": int(vt["rns"]),
                    "Revenue":         fmt_gbp(vt["rev"]),
                    "ADR":             fmt_gbp(vt["adr"]),
                    "Occupancy":       f"{vt['occ']*100:.1f}%",
                    "RevPAR":          fmt_gbp(vt["revpar"]),
                })
                return pd.DataFrame(results)

            for dtype in ["Weekday (Mon–Thu)", "Weekend (Fri–Sat)", "Sunday"]:
                sub  = sdf[sdf["day_type"] == dtype]
                days = [d for d in all_dates if day_type(d) == dtype]
                st.markdown(f"#### {dtype}")
                if sub.empty or not days:
                    st.caption("No stays in this segment for the selected period.")
                else:
                    st.dataframe(kpi_table(sub, days), hide_index=True, use_container_width=True)

            st.markdown("#### All Days Combined")
            st.dataframe(kpi_table(sdf, all_dates), hide_index=True, use_container_width=True)

            # ── YoY comparison vs same period last year ─────────────────────────
            wk_from_ly = wk_from - relativedelta(years=1)
            wk_to_ly   = wk_to   - relativedelta(years=1)
            all_dates_ly = [wk_from_ly + timedelta(days=i) for i in range((wk_to_ly - wk_from_ly).days + 1)]

            df_wk_ly = loading_stay_data(wk_from_ly, wk_to_ly)
            sdf_ly = expand_stay_dates(df_wk_ly) if not df_wk_ly.empty else pd.DataFrame()
            if not sdf_ly.empty:
                sdf_ly = sdf_ly[(sdf_ly["stay_date"] >= wk_from_ly) & (sdf_ly["stay_date"] <= wk_to_ly)]

            st.markdown(
                f"#### vs Same Period Last Year "
                f"({wk_from_ly.strftime('%d %b %Y')} – {wk_to_ly.strftime('%d %b %Y')})"
            )

            def pct_change(curr, prev):
                if not prev:
                    return None
                return (curr - prev) / prev * 100

            def yoy_row(label, curr, prev, not_open_ly, capacity_changed=False):
                if not_open_ly:
                    return {"Property": label, "Room Nights Sold": "Not open (LY)",
                            "Revenue": "Not open (LY)", "ADR": "Not open (LY)",
                            "Occupancy": "Not open (LY)", "RevPAR": "Not open (LY)"}
                display_label = f"{label} *" if capacity_changed else label
                row = {"Property": display_label}
                for key, col in [("rns", "Room Nights Sold"), ("rev", "Revenue"),
                                  ("adr", "ADR"), ("occ", "Occupancy"), ("revpar", "RevPAR")]:
                    pc = pct_change(curr[key], prev[key])
                    row[col] = fmt_pct(pc) if pc is not None else "—"
                return row

            yoy_rows = []
            avail_curr_total, avail_prev_total = 0, 0
            for prop in PROP_NAMES:
                opening = OPENING_DATES.get(prop)
                not_open_ly = bool(opening) and all(d < opening for d in all_dates_ly)
                curr_v = kpi_values(sdf, all_dates, prop)
                prev_v = kpi_values(sdf_ly, all_dates_ly, prop)
                avail_curr = avail_nights_in_range(prop, all_dates)
                avail_prev = avail_nights_in_range(prop, all_dates_ly)
                avail_curr_total += avail_curr
                avail_prev_total += avail_prev
                capacity_changed = (not not_open_ly) and avail_curr != avail_prev
                yoy_rows.append(yoy_row(prop, curr_v, prev_v, not_open_ly, capacity_changed))

            curr_t = kpi_values(sdf, all_dates, None)
            prev_t = kpi_values(sdf_ly, all_dates_ly, None)
            yoy_rows.append(yoy_row("GROUP TOTAL", curr_t, prev_t, False,
                                     avail_curr_total != avail_prev_total))

            yoy_df = pd.DataFrame(yoy_rows)

            def _yoy_color(v):
                if isinstance(v, str):
                    if v.startswith("+"):
                        return "color: #1a7f37; font-weight: 600"
                    if v.startswith("-"):
                        return "color: #c0392b; font-weight: 600"
                return ""

            yoy_styled = yoy_df.style.map(_yoy_color, subset=[c for c in yoy_df.columns if c != "Property"])
            st.dataframe(yoy_styled, hide_index=True, use_container_width=True)
            st.caption(
                "Green = up vs same period last year, red = down. 'Not open (LY)' = "
                "property wasn't trading in the comparison period. * = room count changed "
                "between the two periods (e.g. The Queen's Head expanded from 4 to 9 rooms "
                "on 1 Apr 2026) — Room Nights Sold and Revenue % aren't like-for-like for "
                "these rows; Occupancy and RevPAR are capacity-adjusted and remain comparable."
            )

            # ── Charts ────────────────────────────────────────────────────────
            import plotly.graph_objects as go
            st.divider()
            st.markdown("#### Charts")

            rev_by_prop, occ_by_prop, revpar_by_prop = [], [], []
            for prop in PROP_NAMES:
                pdf   = sdf[sdf["venue_name"] == prop]
                avail = avail_nights_in_range(prop, all_dates)
                rns   = pdf["num_rooms"].sum()
                rev   = pdf["nightly_revenue"].sum()
                rev_by_prop.append(float(rev))
                occ_by_prop.append(rns / avail * 100 if avail else 0)
                revpar_by_prop.append(rev / avail if avail else 0)

            short_props = [p.replace("The ", "") for p in PROP_NAMES]

            fig_wk_rev = go.Figure([
                go.Bar(x=short_props, y=rev_by_prop, marker_color=BRAND_GREEN)
            ])
            fig_wk_rev.update_layout(
                title="Revenue by Property",
                yaxis_title="Revenue (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
                margin=dict(t=50, b=20), height=320, showlegend=False,
            )

            fig_wk_occ = go.Figure([
                go.Bar(x=short_props, y=occ_by_prop, marker_color=BRAND_LIGHT)
            ])
            fig_wk_occ.update_layout(
                title="Occupancy % by Property",
                yaxis_title="Occupancy %", yaxis_ticksuffix="%",
                margin=dict(t=50, b=20), height=320, showlegend=False,
            )

            fig_wk_rp = go.Figure([
                go.Bar(x=short_props, y=revpar_by_prop, marker_color=BRAND_GREEN)
            ])
            fig_wk_rp.update_layout(
                title="RevPAR by Property",
                yaxis_title="RevPAR (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
                margin=dict(t=50, b=20), height=320, showlegend=False,
            )

            c1, c2 = st.columns(2)
            c1.plotly_chart(fig_wk_rev, use_container_width=True)
            c2.plotly_chart(fig_wk_occ, use_container_width=True)
            st.plotly_chart(fig_wk_rp, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Source Report
# ─────────────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Source Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Where your bookings are coming from, for a chosen month — broken down by
booking channel (OTA, direct, phone, etc.) with revenue and volume for each.

**Columns explained:**
- **Channel** — the booking source. `Direct / Phone` means the guest booked without going through an OTA (could be phone, walk-in, or your own website). OTA names (e.g. Booking.com, Expedia) appear as reported by Eviivo
- **Bookings** — number of confirmed reservations from this channel
- **Room Nights** — total room-nights booked via this channel
- **Revenue** — gross revenue (inc. VAT) from this channel
- **ADR** — average rate per room-night for bookings from this channel
- **Rev %** — this channel's share of total revenue for the month
- **Bkg %** — this channel's share of total booking volume

**Why it matters:** OTA bookings typically come with commission costs (usually 15–25%). A high OTA
share reduces net revenue. Tracking this helps you assess whether direct booking investment is paying off.

**Note:** Channel data depends on Eviivo correctly recording the source. If you see unexpected results,
check that Eviivo is set up to capture channel information for each booking type.
        """)


    st.info(
        "Channel is detected from the guest email domain (e.g. `@guest.booking.com` = Booking.com, "
        "`@agoda-messaging.com` = Agoda). Guests who provided their real email address are shown as "
        "**Direct / Phone** — this includes your own website, phone, and walk-in bookings. "
        "A small number of OTA bookings where the guest shared their real email may be misclassified as Direct."
    )

    c1, c2 = st.columns(2)
    with c1:
        src_from = st.date_input("From", value=date(CY, 1, 1), key="src_from")
    with c2:
        src_to = st.date_input("To", value=TODAY, key="src_to")

    if src_from > src_to:
        st.error("'From' date must be before 'To' date.")
        st.stop()

    df_src = loading_data(src_from, src_to)

    if df_src.empty:
        st.info("No confirmed bookings in this period.")
    else:
        ch_all = df_src.groupby("channel").agg(
            Bookings=("booking_ref", "count"),
            Room_Nights=("nights", "sum"),
            Revenue=("revenue", "sum"),
        ).reset_index()
        ch_all["ADR"]   = ch_all["Revenue"] / ch_all["Room_Nights"].replace(0, np.nan)
        ch_all["Rev %"] = ch_all["Revenue"] / ch_all["Revenue"].sum() * 100
        ch_all["Bkg %"] = ch_all["Bookings"] / ch_all["Bookings"].sum() * 100

        total_row = pd.DataFrame([{
            "channel":     "TOTAL",
            "Bookings":    ch_all["Bookings"].sum(),
            "Room_Nights": ch_all["Room_Nights"].sum(),
            "Revenue":     ch_all["Revenue"].sum(),
            "ADR":         ch_all["Revenue"].sum() / ch_all["Room_Nights"].sum() if ch_all["Room_Nights"].sum() else 0,
            "Rev %":       100.0,
            "Bkg %":       100.0,
        }])
        ch_all = pd.concat([ch_all, total_row], ignore_index=True)
        ch_all = ch_all.sort_values("Revenue", ascending=False)

        display = ch_all.copy()
        display.columns = ["Channel", "Bookings", "Room Nights", "Revenue", "ADR", "Rev %", "Bkg %"]
        display["Revenue"] = display["Revenue"].apply(fmt_gbp)
        display["ADR"]     = display["ADR"].apply(fmt_gbp)
        display["Rev %"]   = display["Rev %"].apply(lambda v: f"{v:.1f}%")
        display["Bkg %"]   = display["Bkg %"].apply(lambda v: f"{v:.1f}%")

        st.dataframe(display, hide_index=True, use_container_width=True)

        # ── Charts ────────────────────────────────────────────────────────────
        import plotly.graph_objects as go
        st.divider()
        st.markdown("#### Charts")

        chart_src = ch_all[ch_all["channel"] != "TOTAL"].copy().sort_values("Revenue", ascending=True)

        fig_src_rev = go.Figure([
            go.Bar(y=chart_src["channel"], x=chart_src["Revenue"],
                   orientation="h", marker_color=BRAND_GREEN)
        ])
        fig_src_rev.update_layout(
            title="Revenue by Channel",
            xaxis_title="Revenue (£)", xaxis_tickprefix="£", xaxis_tickformat=",.0f",
            margin=dict(t=50, b=20, l=160), height=max(300, len(chart_src) * 52),
            showlegend=False,
        )

        green_palette = ["#1b4332","#2d6a4f","#40916c","#52b788","#74c69d","#95d5b2","#b7e4c7","#d8f3dc"]
        fig_src_pie = go.Figure([
            go.Pie(
                labels=chart_src["channel"], values=chart_src["Revenue"],
                hole=0.45,
                marker=dict(colors=green_palette[:len(chart_src)]),
                textinfo="label+percent",
            )
        ])
        fig_src_pie.update_layout(
            title="Revenue Share by Channel",
            margin=dict(t=50, b=20), height=380, showlegend=False,
        )

        c1, c2 = st.columns(2)
        c1.plotly_chart(fig_src_rev, use_container_width=True)
        c2.plotly_chart(fig_src_pie, use_container_width=True)

        with st.expander("Breakdown by property"):
            for prop in PROP_NAMES:
                pch = df_src[df_src["venue_name"] == prop].groupby("channel").agg(
                    Bookings=("booking_ref", "count"),
                    Revenue=("revenue", "sum"),
                ).reset_index().sort_values("Revenue", ascending=False)
                if not pch.empty:
                    st.caption(f"**{prop}**")
                    pch["Revenue"] = pch["Revenue"].apply(fmt_gbp)
                    st.dataframe(pch.rename(columns={"channel": "Channel"}), hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — Rate Plan Report
# ─────────────────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Rate Plan Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Revenue and bookings broken down by rate plan (e.g. B&B, Room Only,
Dinner B&B, promotional rates) for a chosen month.

**Columns explained:**
- **Rate Plan** — the pricing package used for the booking (e.g. "B&B" includes breakfast, "Room Only" does not)
- **Bookings** — number of reservations on this rate plan
- **Room Nights** — total room-nights at this rate
- **Revenue** — gross revenue (inc. VAT) from this rate plan
- **ADR** — average nightly rate for this plan
- **Rev %** — this plan's share of total monthly revenue

**Why it matters:** Rate plan mix tells you what guests are buying. A shift from Room Only to B&B
increases revenue per booking. Promotional rates will show lower ADR — useful to see whether
discounted rates are filling rooms that would otherwise be empty, or cannibalising full-rate bookings.

**Note:** The Eviivo API currently returns a rate plan ID rather than the plan name. If plan names
are showing as IDs, this can be resolved by asking Eviivo support to confirm how rate plan names
are exposed in the API.
        """)


    st.info(
        "Rate plan is detected from the booking notes field in Eviivo (e.g. 'breakfast is included "
        "in the room rate' → B&B). Bookings with no recognisable keyword appear as **Unknown**."
    )

    c1, c2 = st.columns(2)
    with c1:
        rp_from = st.date_input("From", value=date(CY, 1, 1), key="rp_from")
    with c2:
        rp_to = st.date_input("To", value=TODAY, key="rp_to")

    if rp_from > rp_to:
        st.error("'From' date must be before 'To' date.")
    else:
        df_rp = loading_data(rp_from, rp_to)

        if df_rp.empty:
            st.info("No confirmed bookings in this period.")
        else:
            rp_grp = df_rp.groupby("rate_plan").agg(
                Bookings=("booking_ref", "count"),
                Room_Nights=("nights", "sum"),
                Revenue=("revenue", "sum"),
            ).reset_index()
            rp_grp["ADR"]   = rp_grp["Revenue"] / rp_grp["Room_Nights"].replace(0, np.nan)
            rp_grp["Rev %"] = rp_grp["Revenue"] / rp_grp["Revenue"].sum() * 100
            rp_grp = rp_grp.sort_values("Revenue", ascending=False)

            total_rp = pd.DataFrame([{
                "rate_plan":   "TOTAL",
                "Bookings":    rp_grp["Bookings"].sum(),
                "Room_Nights": rp_grp["Room_Nights"].sum(),
                "Revenue":     rp_grp["Revenue"].sum(),
                "ADR":         rp_grp["Revenue"].sum() / rp_grp["Room_Nights"].sum() if rp_grp["Room_Nights"].sum() else 0,
                "Rev %":       100.0,
            }])
            rp_grp = pd.concat([rp_grp, total_rp], ignore_index=True)

            disp_rp = rp_grp.copy()
            disp_rp.columns = ["Rate Plan", "Bookings", "Room Nights", "Revenue", "ADR", "Rev %"]
            disp_rp["Revenue"] = disp_rp["Revenue"].apply(fmt_gbp)
            disp_rp["ADR"]     = disp_rp["ADR"].apply(fmt_gbp)
            disp_rp["Rev %"]   = disp_rp["Rev %"].apply(lambda v: f"{v:.1f}%")

            st.markdown("#### All Properties")
            st.dataframe(disp_rp, hide_index=True, use_container_width=True)

            # ── Charts ────────────────────────────────────────────────────────
            import plotly.graph_objects as go
            st.divider()
            st.markdown("#### Charts")

            rp_chart = rp_grp[rp_grp["rate_plan"] != "TOTAL"].copy().sort_values("Revenue", ascending=True)

            fig_rp_rev = go.Figure([
                go.Bar(y=rp_chart["rate_plan"], x=rp_chart["Revenue"],
                       orientation="h", marker_color=BRAND_GREEN)
            ])
            fig_rp_rev.update_layout(
                title="Revenue by Rate Plan",
                xaxis_title="Revenue (£)", xaxis_tickprefix="£", xaxis_tickformat=",.0f",
                margin=dict(t=50, b=20, l=130), height=max(280, len(rp_chart) * 60),
                showlegend=False,
            )

            green_palette = ["#1b4332","#2d6a4f","#40916c","#52b788","#74c69d","#95d5b2","#b7e4c7","#d8f3dc"]
            fig_rp_pie = go.Figure([
                go.Pie(
                    labels=rp_chart["rate_plan"], values=rp_chart["Revenue"],
                    hole=0.45,
                    marker=dict(colors=green_palette[:len(rp_chart)]),
                    textinfo="label+percent",
                )
            ])
            fig_rp_pie.update_layout(
                title="Revenue Share by Rate Plan",
                margin=dict(t=50, b=20), height=320, showlegend=False,
            )

            c1, c2 = st.columns(2)
            c1.plotly_chart(fig_rp_rev, use_container_width=True)
            c2.plotly_chart(fig_rp_pie, use_container_width=True)

            with st.expander("Breakdown by property"):
                for prop in PROP_NAMES:
                    pdf_rp = df_rp[df_rp["venue_name"] == prop].groupby("rate_plan").agg(
                        Bookings=("booking_ref", "count"),
                        Revenue=("revenue", "sum"),
                    ).reset_index().sort_values("Revenue", ascending=False)
                    if not pdf_rp.empty:
                        st.caption(f"**{prop}**")
                        pdf_rp["Revenue"] = pdf_rp["Revenue"].apply(fmt_gbp)
                        st.dataframe(
                            pdf_rp.rename(columns={"rate_plan": "Rate Plan"}),
                            hide_index=True,
                        )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — Blocked Rooms
# ─────────────────────────────────────────────────────────────────────────────

BLOCKS_CSV_PATH = "data/blocks.csv"

def _load_blocks_csv(path: str) -> pd.DataFrame:
    """Parse the Eviivo History-Blocks CSV export format."""
    import os
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, header=None, dtype=str).fillna("")
        # Format: each row = 11 repeated human headers + 11 data values
        # Skip row 0 (technical Textbox headers); data starts at column index 11
        data_rows = raw.iloc[1:, 11:].copy()
        data_rows.columns = [
            "property", "from_date", "to_date", "type",
            "description", "room_type", "room",
            "created_on", "created_by", "deleted_on", "deleted_by",
        ]
        data_rows = data_rows.reset_index(drop=True)

        for col in ["from_date", "to_date"]:
            data_rows[col] = pd.to_datetime(
                data_rows[col], format="%d %b %Y", errors="coerce"
            ).dt.date

        # Remove rows where dates couldn't be parsed
        data_rows = data_rows.dropna(subset=["from_date", "to_date"])
        return data_rows
    except Exception as e:
        st.error(f"Error reading blocks CSV: {e}")
        return pd.DataFrame()


with tabs[4]:
    st.subheader("Blocked Rooms Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Rooms that were taken out of sale during the selected period — maintenance
blocks, owner use, scheduled closures, and group holds.

**Columns explained:**
- **Property** — which venue
- **From / To** — start and end dates of the block
- **Nights** — number of nights blocked
- **Type** — category (Maintenance, Reserved for owner, Scheduled Closure, Group Booking)
- **Description** — the reason recorded in Eviivo
- **Room Type / Room** — which room was blocked

**Active vs deleted:** Blocks that were added and then removed (e.g. a maintenance block
that got resolved and re-opened) appear in the raw data with a "Deleted On" date. This
report shows only blocks that were **not deleted** — i.e. rooms that were genuinely
taken out of sale for the period shown.

**How to update:** Export "History - Blocks" from Eviivo (Dashboard → Standard Reports →
History Blocks → Export) and upload it below. The file is saved so you only need to
re-upload when you want fresher data.
        """)

    # ── File upload ───────────────────────────────────────────────────────────
    import os
    os.makedirs("data", exist_ok=True)

    uploaded = st.file_uploader(
        "Upload Eviivo History-Blocks CSV export to refresh data",
        type="csv", key="blocks_upload",
        help="Export from Eviivo: Dashboard → Standard Reports → History Blocks → Export/Download",
    )
    if uploaded:
        with open(BLOCKS_CSV_PATH, "wb") as f:
            f.write(uploaded.read())
        st.success("File saved — report updated.")

    blk_df = _load_blocks_csv(BLOCKS_CSV_PATH)

    if blk_df.empty:
        st.info(
            "No data yet. Export **History - Blocks** from Eviivo "
            "(Dashboard → Standard Reports → History Blocks) and upload the CSV above."
        )
    else:
        # ── Filters ──────────────────────────────────────────────────────────
        c1, c2, c3 = st.columns(3)
        with c1:
            blk_from = st.date_input("From", value=date(CY, 1, 1), key="blk_from")
        with c2:
            blk_to   = st.date_input("To",   value=TODAY,           key="blk_to")
        with c3:
            show_deleted = st.checkbox("Include removed blocks", value=False, key="blk_deleted")

        # Active = not deleted; filter by date overlap
        active = blk_df if show_deleted else blk_df[blk_df["deleted_on"] == ""]
        in_range = active[
            (active["from_date"] <= blk_to) & (active["to_date"] > blk_from)
        ].copy()
        in_range["nights"] = (
            pd.to_datetime(in_range["to_date"]) - pd.to_datetime(in_range["from_date"])
        ).dt.days

        # Normalise property name (Eviivo sometimes uses "Fleur de Lys Inn, Cranborne")
        prop_name_map = {
            "Fleur de Lys Inn, Cranborne": "The Fleur de Lys",
            "The Dog and Gun": "The Dog & Gun",
        }
        in_range["property"] = in_range["property"].replace(prop_name_map)

        st.caption(
            f"Showing {'all' if show_deleted else 'active (not removed)'} blocks "
            f"overlapping {blk_from.strftime('%d %b %Y')} – {blk_to.strftime('%d %b %Y')}. "
            f"**{len(in_range)} blocks, {int(in_range['nights'].sum())} room-nights.**"
        )

        # ── Summary metrics ───────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Blocks",         len(in_range))
        c2.metric("Room-Nights Blocked",  int(in_range["nights"].sum()))
        c3.metric("Properties Affected",  in_range["property"].nunique())
        c4.metric("Most Common Type",     in_range["type"].mode().iloc[0] if not in_range.empty else "—")

        # ── By type ───────────────────────────────────────────────────────────
        st.markdown("#### By Type")
        by_type = (
            in_range.groupby("type")
            .agg(Blocks=("nights", "count"), Room_Nights=("nights", "sum"))
            .reset_index()
            .sort_values("Room_Nights", ascending=False)
        )
        by_type.columns = ["Type", "Blocks", "Room-Nights"]
        st.dataframe(by_type, hide_index=True, use_container_width=True)

        # ── By property ───────────────────────────────────────────────────────
        st.markdown("#### By Property")
        by_prop = (
            in_range.groupby("property")
            .agg(Blocks=("nights", "count"), Room_Nights=("nights", "sum"))
            .reset_index()
            .sort_values("Room_Nights", ascending=False)
        )
        by_prop.columns = ["Property", "Blocks", "Room-Nights"]
        st.dataframe(by_prop, hide_index=True, use_container_width=True)

        # ── Charts ────────────────────────────────────────────────────────────
        import plotly.graph_objects as go
        st.divider()
        st.markdown("#### Charts")

        by_type_chart = by_type.sort_values("Room-Nights", ascending=True)
        fig_blk_type = go.Figure([
            go.Bar(y=by_type_chart["Type"], x=by_type_chart["Room-Nights"],
                   orientation="h", marker_color=BRAND_GREEN)
        ])
        fig_blk_type.update_layout(
            title="Room-Nights Blocked by Type",
            xaxis_title="Room-Nights",
            margin=dict(t=50, b=20, l=160), height=max(280, len(by_type_chart) * 60),
            showlegend=False,
        )

        by_prop_chart = by_prop.sort_values("Room-Nights", ascending=True)
        fig_blk_prop = go.Figure([
            go.Bar(y=by_prop_chart["Property"], x=by_prop_chart["Room-Nights"],
                   orientation="h", marker_color=BRAND_LIGHT)
        ])
        fig_blk_prop.update_layout(
            title="Room-Nights Blocked by Property",
            xaxis_title="Room-Nights",
            margin=dict(t=50, b=20, l=160), height=max(280, len(by_prop_chart) * 52),
            showlegend=False,
        )

        c1, c2 = st.columns(2)
        c1.plotly_chart(fig_blk_type, use_container_width=True)
        c2.plotly_chart(fig_blk_prop, use_container_width=True)

        # ── Full detail ───────────────────────────────────────────────────────
        with st.expander("Full block detail"):
            disp = in_range[[
                "property", "from_date", "to_date", "nights",
                "type", "description", "room_type", "room",
            ]].copy()
            disp.columns = [
                "Property", "From", "To", "Nights",
                "Type", "Description", "Room Type", "Room",
            ]
            disp = disp.sort_values(["From", "Property"])
            st.dataframe(disp, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — Pace Report
# ─────────────────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Pace Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** For each upcoming month, how many room nights and how much revenue you
currently have booked — compared to how many you had booked at this same point last year.

**How to read it:**
- **OTB RNs / OTB Rev** — what's currently on the books for that month
- **LY RNs / LY Rev** — what was on the books for the equivalent month last year,
  counted only up to the same calendar date (so it's a fair like-for-like comparison)
- **Δ RNs / Δ Rev** — how far ahead or behind you are vs last year
- **Signal** — a quick read on pace: 🟢 Strong (>15% ahead), 🟡 Ahead (0–15%),
  🟠 Behind (0–15% behind), 🔴 Weak (>15% behind)

**Example:** If May shows 🟠 and you're 8 room nights behind last year's pace,
that's a prompt to consider a promotion or rate adjustment for May.

**Note:** Pace reflects bookings on the books today — not final revenue.
Bookings can still come in or cancel before the month arrives.
        """)

    ly_today = date(TODAY.year - 1, TODAY.month, TODAY.day)
    st.caption(
        f"As of {TODAY.strftime('%d %b %Y')} — LY comparison counts only bookings "
        f"made on or before {ly_today.strftime('%d %b %Y')}."
    )

    months_ahead = st.selectbox(
        "Months to show", [2, 3, 4, 6], index=1,
        format_func=lambda n: f"Next {n} months", key="pace_months"
    )

    # Build month list starting from current month
    month_list = []
    for i in range(months_ahead + 1):
        m_start = date(TODAY.year, TODAY.month, 1) + relativedelta(months=i)
        m_end   = (m_start + relativedelta(months=1)) - timedelta(days=1)
        month_list.append((m_start, m_end))

    cy_start = month_list[0][0]
    cy_end   = month_list[-1][1]
    ly_start = cy_start - relativedelta(years=1)
    ly_end   = cy_end   - relativedelta(years=1)

    df_cy_pace     = loading_data(cy_start, cy_end)
    df_ly_pace_all = loading_data(ly_start, ly_end)

    def _filter_ly(df):
        if df.empty:
            return df
        return df[df["created"].apply(lambda d: d is not None and d <= ly_today if d else False)]

    df_ly_pace = _filter_ly(df_ly_pace_all)

    def _pace_signal(var_pct):
        if var_pct is None:
            return "—"
        if var_pct >= 15:
            return "🟢 Strong"
        if var_pct >= 0:
            return "🟡 Ahead"
        if var_pct >= -15:
            return "🟠 Behind"
        return "🔴 Weak"

    def _pace_month_rows(cy_df, ly_df, props):
        rows = []
        for m_start, m_end in month_list:
            ly_m_start = m_start - relativedelta(years=1)
            ly_m_end   = m_end   - relativedelta(years=1)

            cy_m = cy_df[
                cy_df["venue_name"].isin(props) &
                (cy_df["checkin"] >= m_start) & (cy_df["checkin"] <= m_end)
            ] if not cy_df.empty else pd.DataFrame()
            ly_m = ly_df[
                ly_df["venue_name"].isin(props) &
                (ly_df["checkin"] >= ly_m_start) & (ly_df["checkin"] <= ly_m_end)
            ] if not ly_df.empty else pd.DataFrame()

            cy_rns = int(cy_m["nights"].sum()) if not cy_m.empty else 0
            cy_rev = cy_m["revenue"].sum() if not cy_m.empty else 0.0
            ly_rns = int(ly_m["nights"].sum()) if not ly_m.empty else 0
            ly_rev = ly_m["revenue"].sum() if not ly_m.empty else 0.0

            var_rns     = cy_rns - ly_rns
            var_rns_pct = (var_rns / ly_rns * 100) if ly_rns else None
            var_rev     = cy_rev - ly_rev
            var_rev_pct = (var_rev / ly_rev * 100) if ly_rev else None

            rows.append({
                "Month":    m_start.strftime("%B %Y"),
                "OTB RNs":  cy_rns or "—",
                "OTB Rev":  fmt_gbp(cy_rev) if cy_rev else "—",
                "LY RNs":   ly_rns or "—",
                "LY Rev":   fmt_gbp(ly_rev) if ly_rev else "—",
                "Δ RNs":    f"{'+' if var_rns >= 0 else ''}{var_rns}" if ly_rns else "—",
                "Δ Rev":    fmt_var(var_rev) if ly_rev else "—",
                "Δ Rev%":   fmt_pct(var_rev_pct) if var_rev_pct is not None else "—",
                "Signal":   _pace_signal(var_rns_pct),
            })
        return pd.DataFrame(rows)

    # ── Group total ───────────────────────────────────────────────────────────
    st.markdown("#### Group Total")
    group_df = _pace_month_rows(df_cy_pace, df_ly_pace, PROP_NAMES)
    st.dataframe(group_df, hide_index=True, use_container_width=True)

    # ── Bar charts ────────────────────────────────────────────────────────────
    import plotly.graph_objects as go

    # Build raw numeric values for charting (re-derive from data)
    chart_months, cy_rns_list, ly_rns_list, cy_rev_list, ly_rev_list = [], [], [], [], []
    for m_start, m_end in month_list:
        ly_m_start = m_start - relativedelta(years=1)
        ly_m_end   = m_end   - relativedelta(years=1)
        cy_m = df_cy_pace[(df_cy_pace["checkin"] >= m_start) & (df_cy_pace["checkin"] <= m_end)] if not df_cy_pace.empty else pd.DataFrame()
        ly_m = df_ly_pace[(df_ly_pace["checkin"] >= ly_m_start) & (df_ly_pace["checkin"] <= ly_m_end)] if not df_ly_pace.empty else pd.DataFrame()
        chart_months.append(m_start.strftime("%b %Y"))
        cy_rns_list.append(int(cy_m["nights"].sum()) if not cy_m.empty else 0)
        ly_rns_list.append(int(ly_m["nights"].sum()) if not ly_m.empty else 0)
        cy_rev_list.append(float(cy_m["revenue"].sum()) if not cy_m.empty else 0.0)
        ly_rev_list.append(float(ly_m["revenue"].sum()) if not ly_m.empty else 0.0)

    bar_col = BRAND_GREEN
    bar_col_ly = BRAND_LIGHT

    fig_rns = go.Figure([
        go.Bar(name="LY (same point)",  x=chart_months, y=ly_rns_list, marker_color=bar_col_ly),
        go.Bar(name="OTB (this year)",  x=chart_months, y=cy_rns_list, marker_color=bar_col),
    ])
    fig_rns.update_layout(
        barmode="group", title="Room Nights — OTB vs LY",
        yaxis_title="Room Nights", legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=20), height=320,
    )

    fig_rev = go.Figure([
        go.Bar(name="LY (same point)",  x=chart_months, y=ly_rev_list, marker_color=bar_col_ly),
        go.Bar(name="OTB (this year)",  x=chart_months, y=cy_rev_list, marker_color=bar_col),
    ])
    fig_rev.update_layout(
        barmode="group", title="Revenue — OTB vs LY",
        yaxis_title="£", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=20), height=320,
    )

    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_rns, use_container_width=True)
    c2.plotly_chart(fig_rev, use_container_width=True)

    # ── Per property ──────────────────────────────────────────────────────────
    st.markdown("#### By Property")
    for prop in PROP_NAMES:
        df = _pace_month_rows(df_cy_pace, df_ly_pace, [prop])
        signals = df["Signal"].tolist()
        # Show the worst signal in the expander header as a quick flag
        worst = next(
            (s for s in ["🔴 Weak", "🟠 Behind", "🟡 Ahead", "🟢 Strong", "—"]
             if any(s in sig for sig in signals)), "—"
        )
        with st.expander(f"{prop}  —  {worst}", expanded=False):
            st.dataframe(df, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 — Pick-up Report
# ─────────────────────────────────────────────────────────────────────────────

_RATE_LOG_COLS   = ["date", "property", "category", "notes"]
_RATE_LOG_REPO   = "DSturgis23/chickpea-reports"
_RATE_LOG_GHPATH = "data/rate_changes.csv"


def _gh_headers() -> dict:
    token = st.secrets.get("github_token", "")
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}


def _load_rate_log() -> pd.DataFrame:
    import base64, requests as _req
    try:
        resp = _req.get(
            f"https://api.github.com/repos/{_RATE_LOG_REPO}/contents/{_RATE_LOG_GHPATH}",
            headers=_gh_headers(), timeout=15,
        )
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode()
            from io import StringIO
            df = pd.read_csv(StringIO(content), dtype=str).fillna("")
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
            return df.dropna(subset=["date"]).reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame(columns=_RATE_LOG_COLS)


def _save_rate_log(df: pd.DataFrame):
    import base64, requests as _req
    content = df.to_csv(index=False)
    encoded = base64.b64encode(content.encode()).decode()
    # Get current SHA (required for update)
    url = f"https://api.github.com/repos/{_RATE_LOG_REPO}/contents/{_RATE_LOG_GHPATH}"
    get_resp = _req.get(url, headers=_gh_headers(), timeout=15)
    payload = {"message": "Update rate change log", "content": encoded}
    if get_resp.status_code == 200:
        payload["sha"] = get_resp.json()["sha"]
    put_resp = _req.put(url, headers=_gh_headers(), json=payload, timeout=15)
    if put_resp.status_code not in (200, 201):
        st.error(f"Could not save rate log to GitHub ({put_resp.status_code}). Check github_token in secrets.")

with tabs[6]:
    st.subheader("Pick-up Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** For each date in the next 3 months, confirmed rooms in-house that night,
key revenue metrics, average length of stay, and how many rooms were recently picked up.

**Columns explained:**
- **Avail** — total rooms available on that date
- **RNs OTB** — confirmed rooms in-house that night (guests already booked for that stay date)
- **Occ%** — RNs OTB ÷ Avail
- **ADR** — average nightly rate of bookings in-house (revenue ÷ rooms, nightly share only)
- **RevPAR** — nightly revenue ÷ available rooms
- **Avg LOS** — average length of stay of bookings that include this night
- **+1D / +3D / +7D** — rooms added to the books in the last 1, 3 or 7 days for that stay date

**Rate & Restriction Log:** Record rate changes, min LOS updates, promotions, and stop-sells
below. They appear as markers on the charts so you can see whether pick-up responded.
        """)

    # Period: current month + next 3 months
    pu_start = date(TODAY.year, TODAY.month, 1)
    pu_end   = (pu_start + relativedelta(months=3)) - timedelta(days=1)

    df_pu = loading_stay_data(pu_start, pu_end)

    YESTERDAY     = TODAY - timedelta(days=1)
    ref_1d        = TODAY                                                      # today only
    ref_3d        = TODAY - timedelta(days=2)                                  # last 3 days incl. today
    ref_7d        = TODAY - timedelta(days=6)                                  # last 7 days incl. today
    last_wk_start = TODAY - timedelta(days=TODAY.weekday() + 7)                # Mon of last week
    last_wk_end   = TODAY - timedelta(days=TODAY.weekday() + 1)                # Sun of last week
    last_mth_start = (date(TODAY.year, TODAY.month, 1) - relativedelta(months=1))
    last_mth_end   = date(TODAY.year, TODAY.month, 1) - timedelta(days=1)

    view_mode = st.radio("View", ["Group total", "By property"], horizontal=True, key="pu_view")

    def pickup_table(df, props_list):
        rows = []
        d = pu_start
        while d <= pu_end:
            row = {
                "Date": d.strftime("%d %b"),
                "Day":  d.strftime("%a"),
                "Wk":   d.strftime("%V"),
            }
            rns = rev = pu1 = pu_yest = pu3 = pu7 = pu_lwk = pu_lmth = los_nights = los_count = 0
            avail_total = 0
            for prop in props_list:
                p = df[
                    (df["venue_name"] == prop) &
                    (df["checkin"] <= d) & (df["checkout"] > d)
                ] if not df.empty else pd.DataFrame()
                if not p.empty:
                    rns        += int(p["num_rooms"].sum())
                    rev        += (p["revenue"] / p["nights"].replace(0, 1)).sum()
                    pu1        += int(p[p["created"] == ref_1d]["num_rooms"].sum())
                    pu_yest    += int(p[p["created"] == YESTERDAY]["num_rooms"].sum())
                    pu3        += int(p[p["created"] >= ref_3d]["num_rooms"].sum())
                    pu7        += int(p[p["created"] >= ref_7d]["num_rooms"].sum())
                    pu_lwk     += int(p[(p["created"] >= last_wk_start) & (p["created"] <= last_wk_end)]["num_rooms"].sum())
                    pu_lmth    += int(p[(p["created"] >= last_mth_start) & (p["created"] <= last_mth_end)]["num_rooms"].sum())
                    los_nights += p["nights"].sum()
                    los_count  += len(p)
                avail_total += get_room_count(prop, d)
            occ     = rns / avail_total if avail_total else 0
            adr     = rev / rns         if rns         else 0
            avg_los = los_nights / los_count if los_count else 0
            row.update({
                "Avail":    avail_total,
                "RNs OTB":  rns or "—",
                "Occ%":     f"{occ*100:.0f}%",
                "ADR":      fmt_gbp(adr) if adr else "—",
                "RevPAR":   fmt_gbp(rev / avail_total) if avail_total else "—",
                "Avg LOS":  f"{avg_los:.1f}" if avg_los else "—",
                "Today":    f"+{pu1}" if pu1 else "—",
                "Yest":     f"+{pu_yest}" if pu_yest else "—",
                "+3D":      f"+{pu3}" if pu3 else "—",
                "+7D":      f"+{pu7}" if pu7 else "—",
                "Last wk":  f"+{pu_lwk}" if pu_lwk else "—",
                "Last mth": f"+{pu_lmth}" if pu_lmth else "—",
            })
            rows.append(row)
            d += timedelta(days=1)
        return pd.DataFrame(rows)

    if view_mode == "Group total":
        st.dataframe(
            pickup_table(df_pu, PROP_NAMES),
            hide_index=True, use_container_width=True,
        )
    else:
        for prop in PROP_NAMES:
            p = df_pu[df_pu["venue_name"] == prop] if not df_pu.empty else pd.DataFrame()
            with st.expander(prop, expanded=False):
                st.dataframe(
                    pickup_table(p, [prop]),
                    hide_index=True, use_container_width=True,
                )

    # ── Rate & Restriction Change Log ─────────────────────────────────────────
    st.divider()
    st.markdown("#### Rate & Restriction Change Log")
    st.caption(
        "Log rate changes, min LOS updates, promotions, and stop-sells. "
        "Entries are marked on the charts below so you can correlate changes with pick-up."
    )

    rate_log = _load_rate_log()

    with st.expander("➕ Log a change"):
        with st.form("rate_log_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                log_date = st.date_input("Date of change", value=TODAY, key="rl_date")
            with c2:
                log_prop = st.selectbox("Property", ["All properties"] + PROP_NAMES, key="rl_prop")
            with c3:
                log_cat = st.selectbox(
                    "Category",
                    ["Rate change", "Min LOS change", "Promotion", "Stop sell", "Re-open", "Other"],
                    key="rl_cat",
                )
            log_notes = st.text_input(
                "Details  (e.g. 'Weekend rate £150 → £130, min stay reduced to 1 night')",
                key="rl_notes",
            )
            if st.form_submit_button("Save") and log_notes.strip():
                new_row = pd.DataFrame([{
                    "date":     log_date.isoformat(),
                    "property": log_prop,
                    "category": log_cat,
                    "notes":    log_notes.strip(),
                }])
                _save_rate_log(pd.concat([rate_log, new_row], ignore_index=True))
                st.success("Logged.")
                st.rerun()

    if not rate_log.empty:
        with st.expander("🗑 Delete an entry"):
            del_idx = st.selectbox(
                "Select entry to remove",
                options=rate_log.index.tolist(),
                format_func=lambda i: (
                    f"{rate_log.loc[i, 'date']}  —  "
                    f"{rate_log.loc[i, 'category']}  —  "
                    f"{str(rate_log.loc[i, 'notes'])[:60]}"
                ),
            )
            if st.button("Delete selected entry"):
                _save_rate_log(rate_log.drop(index=del_idx).reset_index(drop=True))
                st.rerun()

        disp_log = rate_log.copy()
        disp_log["date"] = disp_log["date"].apply(
            lambda x: x.strftime("%d %b %Y") if hasattr(x, "strftime") else x
        )
        disp_log.columns = ["Date", "Property", "Category", "Notes"]
        st.dataframe(disp_log.sort_values("Date", ascending=False), hide_index=True, use_container_width=True)
    else:
        st.info("No changes logged yet.")

    # ── Charts ────────────────────────────────────────────────────────────────
    import plotly.graph_objects as go
    st.divider()
    st.markdown("#### Charts")

    pu_dates, pu_occ, pu_pu7, pu_adr, pu_los = [], [], [], [], []
    d = pu_start
    while d <= pu_end:
        rns = pu7 = avail_total = 0
        rev_tot = los_nights = los_count = 0
        for prop in PROP_NAMES:
            p = df_pu[
                (df_pu["venue_name"] == prop) &
                (df_pu["checkin"] <= d) & (df_pu["checkout"] > d)
            ] if not df_pu.empty else pd.DataFrame()
            if not p.empty:
                rns        += int(p["num_rooms"].sum())
                pu7        += int(p[p["created"] >= ref_7d]["num_rooms"].sum())  # last 7 days incl. today
                rev_tot    += (p["revenue"] / p["nights"].replace(0, 1)).sum()
                los_nights += p["nights"].sum()
                los_count  += len(p)
            avail_total += get_room_count(prop, d)
        pu_dates.append(d.strftime("%d %b"))
        pu_occ.append(rns / avail_total * 100 if avail_total else 0)
        pu_pu7.append(pu7)
        pu_adr.append(float(rev_tot / rns) if rns else None)
        pu_los.append(float(los_nights / los_count) if los_count else None)
        d += timedelta(days=1)

    _CAT_COLORS = {
        "Rate change":    "#e63946",
        "Min LOS change": "#f4a261",
        "Promotion":      "#2a9d8f",
        "Stop sell":      "#6d6875",
        "Re-open":        "#457b9d",
        "Other":          "#adb5bd",
    }

    def _add_rate_events(fig):
        if rate_log.empty:
            return
        for _, row in rate_log.iterrows():
            label = row["date"].strftime("%d %b") if hasattr(row["date"], "strftime") else ""
            if label not in pu_dates:
                continue
            color = _CAT_COLORS.get(row["category"], "#adb5bd")
            fig.add_vline(
                x=label, line_dash="dot", line_color=color, line_width=1.5,
                annotation_text=row["category"],
                annotation_position="top",
                annotation_font_size=9,
                annotation_font_color=color,
            )

    fig_pu_occ = go.Figure([
        go.Scatter(x=pu_dates, y=pu_occ, name="OTB Occ%", mode="lines",
                   line=dict(color=BRAND_GREEN, width=2), fill="tozeroy",
                   fillcolor="rgba(45,106,79,0.12)"),
    ])
    _add_rate_events(fig_pu_occ)
    fig_pu_occ.update_layout(
        title="Occupancy % — On The Books",
        yaxis_title="Occupancy %", yaxis_ticksuffix="%",
        xaxis_tickangle=-45, legend=dict(orientation="h", y=1.1),
        margin=dict(t=80, b=60), height=360,
    )

    fig_pu7 = go.Figure([
        go.Bar(x=pu_dates, y=pu_pu7, name="7-Day Pick-up",
               marker_color=BRAND_GREEN, opacity=0.8),
    ])
    _add_rate_events(fig_pu7)
    fig_pu7.update_layout(
        title="Rooms Picked Up — Last 7 Days by Stay Date",
        yaxis_title="Room Nights",
        xaxis_tickangle=-45,
        margin=dict(t=80, b=60), height=360,
    )

    fig_adr = go.Figure([
        go.Scatter(x=pu_dates, y=pu_adr, name="ADR", mode="lines",
                   line=dict(color=BRAND_GREEN, width=2), connectgaps=True),
    ])
    _add_rate_events(fig_adr)
    fig_adr.update_layout(
        title="ADR — On The Books by Stay Date",
        yaxis_title="ADR (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
        xaxis_tickangle=-45,
        margin=dict(t=80, b=60), height=320,
    )

    fig_los = go.Figure([
        go.Scatter(x=pu_dates, y=pu_los, name="Avg LOS", mode="lines",
                   line=dict(color=BRAND_LIGHT, width=2), connectgaps=True),
    ])
    _add_rate_events(fig_los)
    fig_los.update_layout(
        title="Average Length of Stay — On The Books by Stay Date",
        yaxis_title="Nights",
        xaxis_tickangle=-45,
        margin=dict(t=80, b=60), height=320,
    )

    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_pu_occ, use_container_width=True)
    c2.plotly_chart(fig_pu7, use_container_width=True)
    c1b, c2b = st.columns(2)
    c1b.plotly_chart(fig_adr, use_container_width=True)
    c2b.plotly_chart(fig_los, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 8 — Booking Window / Lead Time
# ─────────────────────────────────────────────────────────────────────────────
with tabs[7]:
    st.subheader("Booking Window & Lead Time")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** How far in advance guests book — the gap between the booking creation date
and the check-in date (called the *booking window* or *lead time*).

**Why it matters:**
- A long average lead time means guests plan ahead — you have more time to manage pricing and fill gaps.
- A short lead time (lots of last-minute bookings) suggests you may be relying on late demand; yield
  management and early-bird promotions can help shift the mix.
- OTAs typically generate shorter lead times than direct bookings.
- Comparing lead time by property tells you which venues fill early and which need late push.

**Buckets:**
- **Same day** — booked and arriving on the same day
- **1–7 days** — last minute
- **8–30 days** — short notice
- **31–90 days** — planned ahead
- **91–180 days** — booking well in advance
- **181+ days** — very early (often group or special occasion bookings)

Figures are based on check-in date within the selected range.
        """)

    c1, c2 = st.columns(2)
    with c1:
        bw_from = st.date_input("Check-in from", value=date(CY, 1, 1), key="bw_from")
    with c2:
        bw_to   = st.date_input("Check-in to",   value=TODAY,          key="bw_to")

    if bw_from > bw_to:
        st.error("'From' date must be before 'To' date.")
    else:
        df_bw = loading_data(bw_from, bw_to)

        if df_bw.empty:
            st.info("No confirmed bookings in this period.")
        else:
            # Compute lead time (days between booking creation and check-in)
            df_bw = df_bw.copy()
            df_bw["lead_time"] = (
                pd.to_datetime(df_bw["checkin"]) - pd.to_datetime(df_bw["created"])
            ).dt.days
            # Drop rows where lead time can't be computed or is negative (data anomaly)
            df_bw = df_bw[df_bw["lead_time"].notna() & (df_bw["lead_time"] >= 0)]

            BUCKET_ORDER = ["Same day", "1–7 days", "8–30 days", "31–90 days", "91–180 days", "181+ days"]

            def lead_bucket(days):
                if days == 0:   return "Same day"
                if days <= 7:   return "1–7 days"
                if days <= 30:  return "8–30 days"
                if days <= 90:  return "31–90 days"
                if days <= 180: return "91–180 days"
                return "181+ days"

            df_bw["bucket"] = df_bw["lead_time"].apply(lead_bucket)

            avg_lt  = df_bw["lead_time"].mean()
            med_lt  = df_bw["lead_time"].median()
            pct_lm  = (df_bw["lead_time"] <= 7).mean() * 100
            pct_far = (df_bw["lead_time"] >= 91).mean() * 100

            # ── Headline metrics ──────────────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Avg Lead Time",       f"{avg_lt:.0f} days")
            c2.metric("Median Lead Time",    f"{med_lt:.0f} days")
            c3.metric("Last-minute (≤7 days)", f"{pct_lm:.1f}%")
            c4.metric("Far ahead (91+ days)",  f"{pct_far:.1f}%")

            st.divider()

            # ── By bucket table ───────────────────────────────────────────────
            st.markdown("#### By Lead Time Bucket")

            bkt_grp = df_bw.groupby("bucket").agg(
                Bookings=("booking_ref", "count"),
                Room_Nights=("nights", "sum"),
                Revenue=("revenue", "sum"),
                Avg_Lead=("lead_time", "mean"),
            ).reindex(BUCKET_ORDER).dropna(how="all").reset_index()
            bkt_grp["ADR"]   = bkt_grp["Revenue"] / bkt_grp["Room_Nights"].replace(0, np.nan)
            bkt_grp["Bkg %"] = bkt_grp["Bookings"] / bkt_grp["Bookings"].sum() * 100
            bkt_grp["Rev %"] = bkt_grp["Revenue"]  / bkt_grp["Revenue"].sum()  * 100

            disp_bkt = bkt_grp.copy()
            disp_bkt.columns = ["Lead Time", "Bookings", "Room Nights", "Revenue", "Avg Lead (days)", "ADR", "Bkg %", "Rev %"]
            disp_bkt["Revenue"]          = disp_bkt["Revenue"].apply(fmt_gbp)
            disp_bkt["ADR"]              = disp_bkt["ADR"].apply(fmt_gbp)
            disp_bkt["Avg Lead (days)"]  = disp_bkt["Avg Lead (days)"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
            disp_bkt["Bkg %"]            = disp_bkt["Bkg %"].apply(lambda v: f"{v:.1f}%")
            disp_bkt["Rev %"]            = disp_bkt["Rev %"].apply(lambda v: f"{v:.1f}%")
            st.dataframe(disp_bkt, hide_index=True, use_container_width=True)

            # ── Bucket charts ─────────────────────────────────────────────────
            import plotly.graph_objects as go

            fig_bkt_bkg = go.Figure([
                go.Bar(x=bkt_grp["bucket"], y=bkt_grp["Bookings"], marker_color=BRAND_GREEN)
            ])
            fig_bkt_bkg.update_layout(
                title="Bookings by Lead Time Bucket",
                yaxis_title="Bookings",
                margin=dict(t=50, b=20), height=300, showlegend=False,
            )

            fig_bkt_rev = go.Figure([
                go.Bar(x=bkt_grp["bucket"], y=bkt_grp["Revenue"], marker_color=BRAND_LIGHT)
            ])
            fig_bkt_rev.update_layout(
                title="Revenue by Lead Time Bucket",
                yaxis_title="Revenue (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
                margin=dict(t=50, b=20), height=300, showlegend=False,
            )

            c1, c2 = st.columns(2)
            c1.plotly_chart(fig_bkt_bkg, use_container_width=True)
            c2.plotly_chart(fig_bkt_rev, use_container_width=True)

            st.divider()

            # ── Lead time bucket by property ─────────────────────────────────
            st.markdown("#### Lead Time Bucket by Property")

            bkt_prop_counts = (
                df_bw.groupby(["venue_name", "bucket"])["booking_ref"]
                .count()
                .unstack("bucket")
                .reindex(columns=BUCKET_ORDER)
                .reindex(PROP_NAMES)
                .dropna(how="all")
                .fillna(0)
            )
            bkt_prop_pct = bkt_prop_counts.div(bkt_prop_counts.sum(axis=1), axis=0) * 100

            disp_bkt_prop = bkt_prop_counts.astype(int).reset_index()
            disp_bkt_prop = disp_bkt_prop.rename(columns={"venue_name": "Property"})
            st.dataframe(disp_bkt_prop, hide_index=True, use_container_width=True)

            bkt_prop_chart = bkt_prop_pct
            short_prop_names = [p.replace("The ", "") for p in bkt_prop_chart.index]
            green_palette = ["#1b4332", "#2d6a4f", "#40916c", "#52b788", "#95d5b2", "#d8f3dc"]

            fig_bkt_prop = go.Figure()
            for bucket, color in zip(BUCKET_ORDER, green_palette):
                if bucket in bkt_prop_chart.columns:
                    fig_bkt_prop.add_trace(go.Bar(
                        name=bucket,
                        y=short_prop_names,
                        x=bkt_prop_chart[bucket],
                        orientation="h",
                        marker_color=color,
                    ))
            fig_bkt_prop.update_layout(
                barmode="stack",
                title="Lead Time Bucket Mix by Property (% of bookings)",
                xaxis_title="% of Bookings",
                margin=dict(t=50, b=20, l=130, r=20),
                height=max(280, len(bkt_prop_chart) * 52),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_bkt_prop, use_container_width=True)

            st.divider()

            # ── By property ───────────────────────────────────────────────────
            st.markdown("#### By Property")

            prop_grp = df_bw.groupby("venue_name").agg(
                Bookings=("booking_ref", "count"),
                Avg_Lead=("lead_time", "mean"),
                Med_Lead=("lead_time", "median"),
                Pct_LM=("lead_time", lambda x: (x <= 7).mean() * 100),
            ).reindex(PROP_NAMES).dropna(how="all").reset_index()

            disp_prop = prop_grp.copy()
            disp_prop.columns = ["Property", "Bookings", "Avg Lead (days)", "Median Lead (days)", "Last-minute %"]
            disp_prop["Avg Lead (days)"]    = disp_prop["Avg Lead (days)"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
            disp_prop["Median Lead (days)"] = disp_prop["Median Lead (days)"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
            disp_prop["Last-minute %"]      = disp_prop["Last-minute %"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "—")
            st.dataframe(disp_prop, hide_index=True, use_container_width=True)

            prop_grp_clean = prop_grp.dropna(subset=["Avg_Lead"]).sort_values("Avg_Lead", ascending=True)
            short_names = [p.replace("The ", "") for p in prop_grp_clean["venue_name"]]

            fig_prop_lt = go.Figure([
                go.Bar(y=short_names, x=prop_grp_clean["Avg_Lead"],
                       orientation="h", marker_color=BRAND_GREEN,
                       text=prop_grp_clean["Avg_Lead"].apply(lambda v: f"{v:.0f} days"),
                       textposition="outside")
            ])
            fig_prop_lt.update_layout(
                title="Average Lead Time by Property",
                xaxis_title="Days",
                margin=dict(t=50, b=20, l=130, r=80), height=max(280, len(prop_grp_clean) * 52),
                showlegend=False,
            )
            st.plotly_chart(fig_prop_lt, use_container_width=True)

            st.divider()

            # ── By channel ────────────────────────────────────────────────────
            st.markdown("#### By Channel")

            ch_grp = df_bw.groupby("channel").agg(
                Bookings=("booking_ref", "count"),
                Avg_Lead=("lead_time", "mean"),
                Med_Lead=("lead_time", "median"),
                Pct_LM=("lead_time", lambda x: (x <= 7).mean() * 100),
            ).reset_index().sort_values("Avg_Lead", ascending=False)

            disp_ch = ch_grp.copy()
            disp_ch.columns = ["Channel", "Bookings", "Avg Lead (days)", "Median Lead (days)", "Last-minute %"]
            disp_ch["Avg Lead (days)"]    = disp_ch["Avg Lead (days)"].apply(lambda v: f"{v:.0f}")
            disp_ch["Median Lead (days)"] = disp_ch["Median Lead (days)"].apply(lambda v: f"{v:.0f}")
            disp_ch["Last-minute %"]      = disp_ch["Last-minute %"].apply(lambda v: f"{v:.1f}%")
            st.dataframe(disp_ch, hide_index=True, use_container_width=True)

            ch_chart = ch_grp.sort_values("Avg_Lead", ascending=True)
            fig_ch_lt = go.Figure([
                go.Bar(y=ch_chart["channel"], x=ch_chart["Avg_Lead"],
                       orientation="h", marker_color=BRAND_LIGHT,
                       text=ch_chart["Avg_Lead"].apply(lambda v: f"{v:.0f} days"),
                       textposition="outside")
            ])
            fig_ch_lt.update_layout(
                title="Average Lead Time by Channel",
                xaxis_title="Days",
                margin=dict(t=50, b=20, l=160, r=80), height=max(280, len(ch_chart) * 52),
                showlegend=False,
            )
            st.plotly_chart(fig_ch_lt, use_container_width=True)

            # ── Monthly trend (if range spans multiple months) ────────────────
            df_bw["checkin_month"] = pd.to_datetime(df_bw["checkin"]).dt.to_period("M")
            month_count = df_bw["checkin_month"].nunique()
            if month_count > 1:
                st.divider()
                st.markdown("#### Average Lead Time by Check-in Month")
                mo_grp = (
                    df_bw.groupby("checkin_month")["lead_time"]
                    .mean()
                    .reset_index()
                    .sort_values("checkin_month")
                )
                mo_grp["label"] = mo_grp["checkin_month"].dt.strftime("%b %Y")

                fig_mo = go.Figure([
                    go.Scatter(x=mo_grp["label"], y=mo_grp["lead_time"],
                               mode="lines+markers",
                               line=dict(color=BRAND_GREEN, width=2),
                               marker=dict(size=8, color=BRAND_GREEN))
                ])
                fig_mo.update_layout(
                    title="Average Lead Time by Check-in Month",
                    yaxis_title="Avg Lead Time (days)",
                    margin=dict(t=50, b=20), height=300,
                    showlegend=False,
                )
                st.plotly_chart(fig_mo, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 9 — Potential Corporate Guests
# ─────────────────────────────────────────────────────────────────────────────
with tabs[8]:
    st.subheader("Potential Corporate Guests")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** Guests who booked **directly** — via our website, by phone/email to us or
Kirstie, or in person at one of the pubs (i.e. not via an OTA or a corporate travel agency) — and
checked in on a **midweek night (Sunday–Thursday)** more than once since 1 Jan 2026.

**Why it matters:** A guest who keeps coming back midweek on a direct booking is often staying
for work. These are the people worth approaching about our corporate rate.

**How it's built:**
- Confirmed (non-cancelled) bookings only.
- "Direct / Phone" channel only — excludes Booking.com, Expedia, and corporate travel agencies.
- Sunday–Thursday check-ins only.
- Guests are matched by email address, falling back to phone number, then name, if no email is given.
- Shown per pub — a guest only appears once they have 2+ qualifying visits at that property.
        """)

    c1, c2 = st.columns(2)
    with c1:
        cg_from = st.date_input("Check-in from", value=date(2026, 1, 1), key="cg_from")
    with c2:
        cg_to = st.date_input("Check-in to", value=TODAY, key="cg_to")

    if cg_from > cg_to:
        st.error("'From' date must be before 'To' date.")
    else:
        df_cg = loading_data(cg_from, cg_to)

        if df_cg.empty:
            st.info("No confirmed bookings in this period.")
        else:
            df_cg = df_cg[df_cg["channel"] == "Direct / Phone"].copy()
            df_cg["checkin_weekday"] = pd.to_datetime(df_cg["checkin"]).dt.weekday
            df_cg = df_cg[~df_cg["checkin_weekday"].isin([4, 5])]  # drop Fri (4) & Sat (5)

            if df_cg.empty:
                st.info("No direct, midweek bookings in this period.")
            else:
                def guest_key(row):
                    # Eviivo issues a fresh GuestId on every booking even for the same
                    # person, so it can't be used to spot repeat guests — match on
                    # normalised email, then normalised phone, then name instead.
                    email = (row["guest_email"] or "").strip().lower()
                    if email:
                        return f"email:{email}"
                    phone_digits = "".join(ch for ch in (row["guest_phone"] or "") if ch.isdigit())
                    if phone_digits:
                        return f"phone:{phone_digits}"
                    return f"name:{row['guest_name']}"

                df_cg["guest_key"] = df_cg.apply(guest_key, axis=1)

                any_found = False
                for prop in PROP_NAMES:
                    pdf = df_cg[df_cg["venue_name"] == prop]
                    if pdf.empty:
                        continue

                    grp = pdf.groupby("guest_key").agg(
                        Guest=("guest_name", "first"),
                        Email=("guest_email", "first"),
                        Phone=("guest_phone", "first"),
                        Visits=("booking_ref", "count"),
                        Nights=("nights", "sum"),
                        Revenue=("revenue", "sum"),
                        First_Visit=("checkin", "min"),
                        Last_Visit=("checkin", "max"),
                    ).reset_index(drop=True)

                    repeat = grp[grp["Visits"] > 1].sort_values("Visits", ascending=False)
                    if repeat.empty:
                        continue

                    any_found = True
                    st.markdown(f"#### {prop}")
                    disp = repeat.copy()
                    disp["Revenue"]      = disp["Revenue"].apply(fmt_gbp)
                    disp["First_Visit"]  = pd.to_datetime(disp["First_Visit"]).dt.strftime("%d %b %Y")
                    disp["Last_Visit"]   = pd.to_datetime(disp["Last_Visit"]).dt.strftime("%d %b %Y")
                    disp.columns = [
                        "Guest", "Email", "Phone", "Midweek Direct Visits",
                        "Total Nights", "Total Revenue", "First Visit", "Last Visit",
                    ]
                    st.dataframe(disp, hide_index=True, use_container_width=True)

                if not any_found:
                    st.info("No guests with repeat midweek direct bookings found in this period.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 10 — Month-End Report
# ─────────────────────────────────────────────────────────────────────────────
with tabs[9]:
    st.subheader("Month-End Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** A single month's headline KPIs (room revenue, RevPAR, ADR, occupancy),
booking source mix, booking lead time, cancellations, and blocked rooms — each compared
against the same month last year.

**Occupancy/RevPAR/ADR** are calculated on a **stay-date** basis (same method as the
Weekly/MTD tab), so a booking spanning month-end only counts the nights that actually
fall in the selected month. **Source mix and lead time** are calculated on a **check-in
date** basis (same as the Source Report and Pace Report tabs) — the two sections won't
reconcile to the same totals for stays spanning a month boundary; that's expected.

**Properties not yet open** in the current or prior-year month (e.g. Fleur de Lys before
Sep 2025) show "N/A" for that side and are excluded from the like-for-like group total,
but included in the full group total.

**Cancellations** reflect bookings currently marked cancelled as of today — Eviivo
doesn't expose a cancellation date, so this can't show cancellations that happened
during the month itself.

**Blocked rooms** come from the manually-uploaded CSV on the Blocked Rooms tab — if that
export doesn't go back far enough to cover the prior-year month, this will say so rather
than showing a misleading zero.
        """)

    month_opts = _mer_month_options()
    sel_month = st.selectbox(
        "Report month", month_opts, index=1,
        format_func=_mer_month_label, key="mer_month",
    )

    cy_start, cy_end = _mer_bounds(sel_month)
    ly_start, ly_end = cy_start - relativedelta(years=1), cy_end - relativedelta(years=1)
    cy_ym = (cy_start.year, cy_start.month)
    ly_ym = (ly_start.year, ly_start.month)
    is_partial = cy_end >= TODAY

    if is_partial:
        st.warning(
            f"⚠️ {sel_month.strftime('%B %Y')} is not yet complete — figures below are "
            "partial and will change as the month progresses."
        )

    cy_dates = [cy_start + timedelta(days=i) for i in range((cy_end - cy_start).days + 1)]
    ly_dates = [ly_start + timedelta(days=i) for i in range((ly_end - ly_start).days + 1)]

    # ── Fetch: stay-date basis (occupancy / RevPAR / ADR) ───────────────────────
    cy_sdf = expand_stay_dates(loading_stay_data(cy_start, cy_end))
    ly_sdf = expand_stay_dates(loading_stay_data(ly_start, ly_end))
    if not cy_sdf.empty:
        cy_sdf = cy_sdf[(cy_sdf["stay_date"] >= cy_start) & (cy_sdf["stay_date"] <= cy_end)]
    if not ly_sdf.empty:
        ly_sdf = ly_sdf[(ly_sdf["stay_date"] >= ly_start) & (ly_sdf["stay_date"] <= ly_end)]

    # ── Fetch: check-in date basis (source mix / lead time) ─────────────────────
    df_cy_ci = loading_data(cy_start, cy_end)
    df_ly_ci = loading_data(ly_start, ly_end)

    # ── Fetch: raw incl. cancelled (cancellations) ───────────────────────────────
    with st.spinner("Fetching data from Eviivo…"):
        df_cy_all = to_df(fetch_bookings(cy_start, cy_end))
        df_ly_all = to_df(fetch_bookings(ly_start, ly_end))

    # ── Blocked rooms ────────────────────────────────────────────────────────────
    blk_df = _load_blocks_csv(BLOCKS_CSV_PATH)
    block_cy = _mer_blocked_stats(blk_df, cy_start, cy_end)
    block_ly = _mer_blocked_stats(blk_df, ly_start, ly_end)

    # ── Pre-compute lead-time / cancellation stats (needed by the narrative) ────
    lt_cy = _mer_leadtime_stats(df_cy_ci)
    lt_ly = _mer_leadtime_stats(df_ly_ci)
    cancel_cy = _mer_cancellation_stats(df_cy_all)
    cancel_ly = _mer_cancellation_stats(df_ly_all)

    # ── Headline KPIs ────────────────────────────────────────────────────────────
    kpi_rows = _mer_kpi_rows(cy_sdf, ly_sdf, cy_dates, ly_dates, cy_ym, ly_ym)
    kpi_pdf_rows = _mer_kpi_table_rows(kpi_rows)

    # ── Executive summary ────────────────────────────────────────────────────────
    st.markdown("#### Executive Summary")
    narrative = _mer_narrative(kpi_rows, lt_cy, lt_ly, cancel_cy, cancel_ly, cy_start, ly_start)
    st.info(narrative)

    # ── Observations (team notes, persisted to GitHub so they survive Streamlit Cloud restarts) ──
    st.markdown("#### Observations")
    month_key = f"{cy_start:%Y-%m}"
    if "mer_notes_df" not in st.session_state or st.session_state.get("mer_notes_month") != month_key:
        st.session_state.mer_notes_df = _load_mer_notes()
        st.session_state.mer_notes_month = month_key
    existing_note = _mer_get_note(st.session_state.mer_notes_df, month_key)
    notes_text = st.text_area(
        f"Notes for {cy_start:%B %Y} — e.g. weddings, refurbishments, weather, one-off events",
        value=existing_note, height=120, key=f"mer_notes_input_{month_key}",
    )
    if st.button("💾 Save Notes", key=f"mer_notes_save_{month_key}"):
        updated_df = _mer_set_note(st.session_state.mer_notes_df, month_key, notes_text)
        if _save_mer_notes(updated_df):
            st.session_state.mer_notes_df = updated_df
            st.success("Notes saved.")

    st.markdown("#### Headline KPIs")
    kpi_disp = pd.DataFrame(
        kpi_pdf_rows,
        columns=["Property", "RNs", "Revenue", "ADR", "Occ %", "RevPAR", "YoY Rev %", "YoY Occ", "Signal", "Note"],
    )
    st.dataframe(kpi_disp, hide_index=True, use_container_width=True)
    st.caption(
        "Like-for-like total excludes properties not open in both the current and "
        "prior-year month. Signal thresholds match the Pace Report tab: 🟢 ≥+15%, "
        "🟡 0–15%, 🟠 0 to -15%, 🔴 <-15% YoY revenue."
    )
    for prop in PROP_NAMES:
        for n in room_block_notes(prop, cy_dates) + room_block_notes(prop, ly_dates):
            st.info(f"**{prop}**: {n}")

    st.markdown("#### Weekday / Weekend / Sunday Split (group total)")
    daytype_df = _mer_daytype_split(cy_sdf, ly_sdf, cy_dates, ly_dates)
    st.dataframe(daytype_df, hide_index=True, use_container_width=True)
    daytype_pdf_rows = daytype_df.values.tolist()

    st.markdown("#### 6-Month Trend — Occupancy & RevPAR")
    trend_df = _mer_trailing_trend(cy_start, months=6)
    chart_pngs = {}
    if not trend_df.empty:
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["cy_occ"], mode="lines+markers",
                                        name="Occ % (this year)", line=dict(color=BRAND_GREEN)))
        fig_trend.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["ly_occ"], mode="lines+markers",
                                        name="Occ % (last year)", line=dict(color=BRAND_GREEN, dash="dot")))
        fig_trend.update_layout(
            title="Occupancy % — trailing 6 months",
            margin=dict(t=50, b=20), height=320, yaxis_title="Occupancy %",
        )
        st.plotly_chart(fig_trend, use_container_width=True)

        fig_revpar = go.Figure()
        fig_revpar.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["cy_revpar"], mode="lines+markers",
                                         name="RevPAR (this year)", line=dict(color="#40916c")))
        fig_revpar.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["ly_revpar"], mode="lines+markers",
                                         name="RevPAR (last year)", line=dict(color="#40916c", dash="dot")))
        fig_revpar.update_layout(
            title="RevPAR — trailing 6 months",
            margin=dict(t=50, b=20), height=320, yaxis_title="RevPAR (£)", yaxis_tickprefix="£",
        )
        st.plotly_chart(fig_revpar, use_container_width=True)
        st.caption(
            "Group totals here include all trading properties each month (like the Headline "
            "KPIs' 'all trading properties' row, not the like-for-like row) — for months before "
            "a newly-opened property existed, its full room count still counts as available "
            "with zero revenue, which can understate that month's LY occupancy/RevPAR."
        )

        try:
            chart_pngs["trend_occ"] = _mer_chart_png(fig_trend)
            chart_pngs["trend_revpar"] = _mer_chart_png(fig_revpar)
        except Exception:
            pass

    st.divider()

    # ── Source mix ───────────────────────────────────────────────────────────────
    st.markdown(f"#### Booking Source Mix — {cy_start:%B %Y} vs {ly_start:%B %Y} (LY)")
    source_cy = _mer_source_mix(df_cy_ci)
    source_ly = _mer_source_mix(df_ly_ci)
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"{cy_start:%B %Y}")
        disp_cy = source_cy.copy()
        if not disp_cy.empty:
            disp_cy["Revenue"] = disp_cy["Revenue"].apply(fmt_gbp)
            disp_cy["ADR"] = disp_cy["ADR"].apply(lambda v: fmt_gbp(v) if not pd.isna(v) else "—")
            disp_cy["Rev %"] = disp_cy["Rev %"].apply(lambda v: f"{v:.1f}%")
        st.dataframe(
            disp_cy.rename(columns={"channel": "Channel", "Room_Nights": "Room Nights"}),
            hide_index=True, use_container_width=True,
        )
    with c2:
        st.caption(f"{ly_start:%B %Y} (LY)")
        disp_ly = source_ly.copy()
        if not disp_ly.empty:
            disp_ly["Revenue"] = disp_ly["Revenue"].apply(fmt_gbp)
            disp_ly["ADR"] = disp_ly["ADR"].apply(lambda v: fmt_gbp(v) if not pd.isna(v) else "—")
            disp_ly["Rev %"] = disp_ly["Rev %"].apply(lambda v: f"{v:.1f}%")
        st.dataframe(
            disp_ly.rename(columns={"channel": "Channel", "Room_Nights": "Room Nights"}),
            hide_index=True, use_container_width=True,
        )

    green_palette = ["#1b4332", "#2d6a4f", "#40916c", "#52b788", "#74c69d", "#95d5b2", "#b7e4c7", "#d8f3dc"]
    c1, c2 = st.columns(2)
    if not source_cy.empty:
        fig_pie_cy = go.Figure([go.Pie(
            labels=source_cy["channel"], values=source_cy["Revenue"], hole=0.45,
            marker=dict(colors=green_palette[:len(source_cy)]), textinfo="label+percent",
        )])
        fig_pie_cy.update_layout(title=f"Revenue Share — {cy_start:%B %Y}", margin=dict(t=50, b=20), height=320, showlegend=False)
        c1.plotly_chart(fig_pie_cy, use_container_width=True)
        try:
            chart_pngs["source_pie_cy"] = _mer_chart_png(fig_pie_cy)
        except Exception:
            pass
    if not source_ly.empty:
        fig_pie_ly = go.Figure([go.Pie(
            labels=source_ly["channel"], values=source_ly["Revenue"], hole=0.45,
            marker=dict(colors=green_palette[:len(source_ly)]), textinfo="label+percent",
        )])
        fig_pie_ly.update_layout(title=f"Revenue Share — {ly_start:%B %Y} (LY)", margin=dict(t=50, b=20), height=320, showlegend=False)
        c2.plotly_chart(fig_pie_ly, use_container_width=True)
        try:
            chart_pngs["source_pie_ly"] = _mer_chart_png(fig_pie_ly)
        except Exception:
            pass

    st.caption(
        "Source mix is based on check-in date, unlike the stay-date-based KPIs above — "
        "the two sections will not reconcile exactly for stays spanning month-end."
    )

    st.divider()

    # ── Booking lead time ────────────────────────────────────────────────────────
    st.markdown(f"#### Booking Lead Time — {cy_start:%B %Y} vs {ly_start:%B %Y} (LY)")
    # lt_cy/lt_ly already computed above (needed by the executive summary)

    def _mer_fmt_lt(v, pct=False):
        if v is None:
            return "—"
        return f"{v:.1f}%" if pct else f"{v:.1f} days"

    lt_table = pd.DataFrame([
        {"Metric": "Avg lead time", f"{cy_start:%b %Y}": _mer_fmt_lt(lt_cy["avg"]), "LY": _mer_fmt_lt(lt_ly["avg"])},
        {"Metric": "Median lead time", f"{cy_start:%b %Y}": _mer_fmt_lt(lt_cy["median"]), "LY": _mer_fmt_lt(lt_ly["median"])},
        {"Metric": "Booked within 7 days", f"{cy_start:%b %Y}": _mer_fmt_lt(lt_cy["pct_7"], True), "LY": _mer_fmt_lt(lt_ly["pct_7"], True)},
        {"Metric": "Booked within 14 days", f"{cy_start:%b %Y}": _mer_fmt_lt(lt_cy["pct_14"], True), "LY": _mer_fmt_lt(lt_ly["pct_14"], True)},
        {"Metric": "Booked within 30 days", f"{cy_start:%b %Y}": _mer_fmt_lt(lt_cy["pct_30"], True), "LY": _mer_fmt_lt(lt_ly["pct_30"], True)},
    ])
    st.dataframe(lt_table, hide_index=True, use_container_width=True)

    with st.expander("Lead-time distribution"):
        c1, c2 = st.columns(2)
        with c1:
            st.caption(f"{cy_start:%B %Y}")
            st.dataframe(_mer_leadtime_buckets(df_cy_ci), hide_index=True, use_container_width=True)
        with c2:
            st.caption(f"{ly_start:%B %Y} (LY)")
            st.dataframe(_mer_leadtime_buckets(df_ly_ci), hide_index=True, use_container_width=True)

    bkt_order = ["Same day", "1–7 days", "8–14 days", "15–30 days", "31+ days"]
    bkt_cy = _mer_leadtime_buckets(df_cy_ci).set_index("Lead Time").reindex(bkt_order, fill_value=0)
    bkt_ly = _mer_leadtime_buckets(df_ly_ci).set_index("Lead Time").reindex(bkt_order, fill_value=0)
    fig_lt = go.Figure([
        go.Bar(name=f"{cy_start:%b %Y}", x=bkt_order, y=bkt_cy["Bookings"], marker_color=BRAND_GREEN),
        go.Bar(name=f"{ly_start:%b %Y} (LY)", x=bkt_order, y=bkt_ly["Bookings"], marker_color=BRAND_LIGHT),
    ])
    fig_lt.update_layout(
        title="Booking Lead-Time Distribution", barmode="group",
        margin=dict(t=50, b=20), height=320, yaxis_title="Bookings",
    )
    st.plotly_chart(fig_lt, use_container_width=True)
    try:
        chart_pngs["leadtime"] = _mer_chart_png(fig_lt)
    except Exception:
        pass

    st.divider()

    # ── Cancellations ────────────────────────────────────────────────────────────
    st.markdown(f"#### Cancellations — {cy_start:%B %Y} vs {ly_start:%B %Y} (LY)")
    # cancel_cy/cancel_ly already computed above (needed by the executive summary)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cancelled Bookings", cancel_cy["n"], delta=cancel_cy["n"] - cancel_ly["n"])
    c2.metric("Room-Nights Lost", cancel_cy["rns"], delta=cancel_cy["rns"] - cancel_ly["rns"])
    c3.metric("Revenue Lost", fmt_gbp(cancel_cy["rev"]))
    c4.metric(
        "% of Bookings",
        f"{cancel_cy['pct']:.1f}%" if cancel_cy["pct"] is not None else "—",
        delta=(f"{cancel_cy['pct'] - cancel_ly['pct']:.1f}pp"
               if cancel_cy["pct"] is not None and cancel_ly["pct"] is not None else None),
    )
    with st.expander("Cancellations by channel and property"):
        c1, c2 = st.columns(2)
        with c1:
            st.caption(f"By channel — {cy_start:%B %Y}")
            if cancel_cy["by_channel"].empty:
                st.caption("No cancellations this month.")
            else:
                disp = cancel_cy["by_channel"].rename(columns={"group": "Channel"}).copy()
                disp["Revenue"] = disp["Revenue"].apply(fmt_gbp)
                st.dataframe(disp, hide_index=True, use_container_width=True)
        with c2:
            st.caption(f"By property — {cy_start:%B %Y}")
            if cancel_cy["by_property"].empty:
                st.caption("No cancellations this month.")
            else:
                disp = cancel_cy["by_property"].rename(columns={"group": "Property"}).copy()
                disp["Revenue"] = disp["Revenue"].apply(fmt_gbp)
                st.dataframe(disp, hide_index=True, use_container_width=True)

    st.caption(
        "Reflects bookings for check-ins in this month currently marked cancelled as of "
        "today — Eviivo does not expose a cancellation date, so this cannot show "
        "cancellations that occurred during the month itself, and may differ from a "
        "report run on a different day."
    )

    st.divider()

    # ── Blocked rooms ────────────────────────────────────────────────────────────
    st.markdown(f"#### Blocked Rooms — {cy_start:%B %Y} vs {ly_start:%B %Y} (LY)")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"{cy_start:%B %Y}")
        if block_cy["available"]:
            st.metric("Blocks", block_cy["n"])
            st.metric("Room-Nights Blocked", block_cy["rns"])
        else:
            st.info("No blocked-rooms data available for this month.")
    with c2:
        st.caption(f"{ly_start:%B %Y} (LY)")
        if block_ly["available"]:
            st.metric("Blocks", block_ly["n"])
            st.metric("Room-Nights Blocked", block_ly["rns"])
        else:
            st.info(
                "No blocked-rooms data available for this month — the uploaded CSV "
                "export does not appear to cover this period."
            )

    st.divider()

    # ── PDF export ───────────────────────────────────────────────────────────────
    pdf_bytes = _mer_build_pdf(
        cy_start, ly_start, is_partial, narrative, notes_text,
        kpi_pdf_rows, daytype_pdf_rows,
        source_cy, source_ly,
        lt_cy, lt_ly, cancel_cy, cancel_ly, block_cy, block_ly,
        chart_pngs,
    )
    st.download_button(
        "⬇️ Download Month-End Report (PDF)",
        data=pdf_bytes,
        file_name=f"month_end_report_{cy_start:%Y_%m}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
