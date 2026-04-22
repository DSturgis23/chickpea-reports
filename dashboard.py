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
)
from eviivo import fetch_bookings

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

    df_wk = loading_data(wk_from, wk_to)

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

            def kpi_table(subset_df, dates_list):
                results = []
                for prop in PROP_NAMES:
                    pdf   = subset_df[subset_df["venue_name"] == prop]
                    avail = avail_nights_in_range(prop, dates_list)
                    rns   = pdf["num_rooms"].sum()
                    rev   = pdf["nightly_revenue"].sum()
                    adr   = rev / rns  if rns  else 0
                    occ   = rns / avail if avail else 0
                    revpar = rev / avail if avail else 0
                    results.append({
                        "Property":        prop,
                        "Room Nights Sold": int(rns),
                        "Revenue":         fmt_gbp(rev),
                        "ADR":             fmt_gbp(adr),
                        "Occupancy":       f"{occ*100:.1f}%",
                        "RevPAR":          fmt_gbp(revpar),
                    })
                # Group total
                avail_total = sum(avail_nights_in_range(p, dates_list) for p in PROP_NAMES)
                rns_t  = subset_df["num_rooms"].sum()
                rev_t  = subset_df["nightly_revenue"].sum()
                adr_t  = rev_t / rns_t  if rns_t  else 0
                occ_t  = rns_t / avail_total if avail_total else 0
                rp_t   = rev_t / avail_total if avail_total else 0
                results.append({
                    "Property":        "GROUP TOTAL",
                    "Room Nights Sold": int(rns_t),
                    "Revenue":         fmt_gbp(rev_t),
                    "ADR":             fmt_gbp(adr_t),
                    "Occupancy":       f"{occ_t*100:.1f}%",
                    "RevPAR":          fmt_gbp(rp_t),
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
with tabs[6]:
    st.subheader("Occupancy Pick-up Report")
    with st.expander("ℹ️ How to read this report", expanded=False):
        st.markdown("""
**What it shows:** For each future date in the next two months, how many rooms are currently
on the books — compared to last year's equivalent position. Modelled on the Lighthouse report format.

**Columns explained:**
- **Avail** — total rooms available across all properties on that date (accounts for Queen's Head expansion)
- **RNs OTB** — Room Nights On The Books: how many rooms are currently confirmed for that date
- **Occ%** — current on-the-books occupancy: RNs OTB ÷ Avail
- **ADR** — average rate of bookings currently on the books for that date
- **RevPAR** — Revenue per Available Room based on current bookings
- **LY RNs / LY Occ% / LY ADR** — last year's equivalent figures, counting only bookings that
  had been made by the same calendar date last year
- **Δ Occ pts** — occupancy point difference vs last year (e.g. +5 means 5 percentage points ahead)

**Why it matters:** Pick-up shows where you have gaps to fill before arrival. A date showing
low occupancy OTB with plenty of time to go is an opportunity for targeted promotions or
last-minute rate adjustments. A date already at high occupancy may support a rate increase.

**Note on historical pick-up (7/14/28 days ago):** To show how bookings have moved in the
past week or fortnight, the app would need to store a daily snapshot of the OTB position.
This can be added — speak to your developer to set up daily snapshot storage.
        """)
    st.info(
        "📸 **Snapshot tracking not yet active.** This report currently shows today's position vs. "
        "last year. To also show pick-up vs. 7, 14 and 28 days ago (as in the full Lighthouse format), "
        "daily snapshots need to be stored. This can be added as a next step."
    )

    # Period: current + next 2 months
    pu_start = date(TODAY.year, TODAY.month, 1)
    pu_end   = (pu_start + relativedelta(months=2)) - timedelta(days=1)
    ly_pu_start = pu_start - relativedelta(years=1)
    ly_pu_end   = pu_end   - relativedelta(years=1)
    ly_ref_date = TODAY - relativedelta(years=1)

    df_cy_pu     = loading_data(pu_start, pu_end)
    df_ly_pu_all = loading_data(ly_pu_start, ly_pu_end)

    def filter_ly_pu(df):
        if df.empty:
            return df
        mask = df["created"].apply(lambda d: d <= ly_ref_date if d else False)
        return df[mask]

    df_ly_pu = filter_ly_pu(df_ly_pu_all)

    view_mode = st.radio("View", ["Group total", "By property"], horizontal=True, key="pu_view")

    def pickup_table(cy_df, ly_df, props_list):
        rows = []
        d = pu_start
        while d <= pu_end:
            ly_d = d - relativedelta(years=1)
            row = {
                "Date":    d.strftime("%d %b"),
                "Day":     d.strftime("%a"),
                "Wk":      d.strftime("%V"),
            }
            cy_rns = ly_rns = cy_rev = ly_rev = 0
            avail_total = 0
            for prop in props_list:
                cy_p = cy_df[(cy_df["venue_name"] == prop) & (cy_df["checkin"] == d)] if not cy_df.empty else pd.DataFrame()
                ly_p = ly_df[(ly_df["venue_name"] == prop) & (ly_df["checkin"] == ly_d)] if not ly_df.empty else pd.DataFrame()
                cy_rns += int(cy_p["num_rooms"].sum()) if not cy_p.empty else 0
                ly_rns += int(ly_p["num_rooms"].sum()) if not ly_p.empty else 0
                cy_rev += cy_p["revenue"].sum() if not cy_p.empty else 0
                ly_rev += ly_p["revenue"].sum() if not ly_p.empty else 0
                avail_total += get_room_count(prop, d)

            occ_cy  = cy_rns / avail_total if avail_total else 0
            occ_ly  = ly_rns / avail_total if avail_total else 0
            adr_cy  = cy_rev / cy_rns if cy_rns else 0
            adr_ly  = ly_rev / ly_rns if ly_rns else 0
            var_occ = (occ_cy - occ_ly) * 100

            row.update({
                "Avail":      avail_total,
                "RNs OTB":    cy_rns or "—",
                "Occ%":       f"{occ_cy*100:.0f}%",
                "ADR":        fmt_gbp(adr_cy) if adr_cy else "—",
                "RevPAR":     fmt_gbp(cy_rev / avail_total) if avail_total else "—",
                "LY RNs":     ly_rns or "—",
                "LY Occ%":    f"{occ_ly*100:.0f}%",
                "LY ADR":     fmt_gbp(adr_ly) if adr_ly else "—",
                "Δ Occ pts":  f"{'+' if var_occ >= 0 else ''}{var_occ:.0f}",
            })
            rows.append(row)
            d += timedelta(days=1)
        return pd.DataFrame(rows)

    if view_mode == "Group total":
        st.dataframe(
            pickup_table(df_cy_pu, df_ly_pu, PROP_NAMES),
            hide_index=True, use_container_width=True,
        )
    else:
        for prop in PROP_NAMES:
            cy_p  = df_cy_pu[df_cy_pu["venue_name"] == prop]  if not df_cy_pu.empty  else pd.DataFrame()
            ly_p  = df_ly_pu[df_ly_pu["venue_name"] == prop]  if not df_ly_pu.empty  else pd.DataFrame()
            with st.expander(prop, expanded=False):
                st.dataframe(
                    pickup_table(cy_p, ly_p, [prop]),
                    hide_index=True, use_container_width=True,
                )

    # ── Charts ────────────────────────────────────────────────────────────────
    import plotly.graph_objects as go
    st.divider()
    st.markdown("#### Charts — Group Occupancy & ADR by Date")

    pu_dates, pu_occ_cy, pu_occ_ly, pu_adr_cy, pu_adr_ly = [], [], [], [], []
    d = pu_start
    while d <= pu_end:
        ly_d = d - relativedelta(years=1)
        cy_rns = ly_rns = cy_rev = ly_rev = avail_total = 0
        for prop in PROP_NAMES:
            cy_p = df_cy_pu[(df_cy_pu["venue_name"] == prop) & (df_cy_pu["checkin"] == d)] if not df_cy_pu.empty else pd.DataFrame()
            ly_p = df_ly_pu[(df_ly_pu["venue_name"] == prop) & (df_ly_pu["checkin"] == ly_d)] if not df_ly_pu.empty else pd.DataFrame()
            cy_rns += int(cy_p["num_rooms"].sum()) if not cy_p.empty else 0
            ly_rns += int(ly_p["num_rooms"].sum()) if not ly_p.empty else 0
            cy_rev += cy_p["revenue"].sum() if not cy_p.empty else 0
            ly_rev += ly_p["revenue"].sum() if not ly_p.empty else 0
            avail_total += get_room_count(prop, d)
        pu_dates.append(d.strftime("%d %b"))
        pu_occ_cy.append(cy_rns / avail_total * 100 if avail_total else 0)
        pu_occ_ly.append(ly_rns / avail_total * 100 if avail_total else 0)
        pu_adr_cy.append(cy_rev / cy_rns if cy_rns else None)
        pu_adr_ly.append(ly_rev / ly_rns if ly_rns else None)
        d += timedelta(days=1)

    fig_pu_occ = go.Figure([
        go.Scatter(x=pu_dates, y=pu_occ_ly, name="LY Occ%", mode="lines",
                   line=dict(color=BRAND_LIGHT, width=2, dash="dot")),
        go.Scatter(x=pu_dates, y=pu_occ_cy, name="OTB Occ%", mode="lines",
                   line=dict(color=BRAND_GREEN, width=2), fill="tozeroy",
                   fillcolor=f"rgba(45,106,79,0.12)"),
    ])
    fig_pu_occ.update_layout(
        title="Occupancy % — OTB vs Last Year",
        yaxis_title="Occupancy %", yaxis_ticksuffix="%",
        xaxis_tickangle=-45,
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=60), height=340,
    )

    fig_pu_adr = go.Figure([
        go.Scatter(x=pu_dates, y=pu_adr_ly, name="LY ADR", mode="lines",
                   line=dict(color=BRAND_LIGHT, width=2, dash="dot")),
        go.Scatter(x=pu_dates, y=pu_adr_cy, name="OTB ADR", mode="lines",
                   line=dict(color=BRAND_GREEN, width=2)),
    ])
    fig_pu_adr.update_layout(
        title="ADR — OTB vs Last Year",
        yaxis_title="ADR (£)", yaxis_tickprefix="£", yaxis_tickformat=",.0f",
        xaxis_tickangle=-45,
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=60, b=60), height=340,
    )

    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_pu_occ, use_container_width=True)
    c2.plotly_chart(fig_pu_adr, use_container_width=True)


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
