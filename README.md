# Chickpea — Hotel Performance Reports

Standalone Streamlit app pulling accommodation data from the Eviivo PMS API.
Runs separately from the SevenRooms reservations dashboard on port **8502**.

## Running the app

```
cd ~/Documents/Chickpea/chickpea_reports
.venv/bin/streamlit run dashboard.py --server.port 8502
```

Open http://localhost:8502 — password: see `.streamlit/secrets.toml`

To refresh data manually, click **Refresh all data** in the sidebar. Data is otherwise cached for 1 hour.

---

## Reports

### 1. Monthly Revenue
Gross accommodation revenue (inc. VAT) by property and month, with year-on-year variance.
- Future months in the current year show forward bookings already on the books (marked `*`)
- YoY comparison excludes pre-opening months, full closures, and partial closures to keep the variance meaningful
- Known closures and openings are flagged inline (Dog & Gun Jan 2026 refurbishment, Queen's Head Jan–Mar 2026, Manor House Inn opened Feb 2025, Fleur de Lys opened Sep 2025)

### 2. Weekly / MTD Performance
ADR, occupancy, and RevPAR for a chosen date range, split by day type (Weekday / Weekend / Sunday).
Follows the sidebar **Date basis** switch (see below).

### 3. Source Report
Booking channel breakdown (Booking.com, Expedia, Agoda, direct, corporate travel, etc.) for a chosen date range.
Channel is detected from guest email domain — OTAs use anonymised addresses (e.g. `@guest.booking.com`).

### 4. Rate Plans
Revenue and booking volume by rate plan (House Rate, Landmarc, B&B, Room Only) for a chosen date range.
Toggle buttons filter rate plans in and out — select one plan to see it by property, or several to see the mix.
- **Landmarc** is detected from the booking email (`@landmarcsolutions.com`) — fixed £110/night corporate rate
- **B&B / Room Only** are detected from booking note keywords (Dinner B&B and Half Board count as B&B)
- **House Rate** is the default for anything else, including bookings with no descriptive note

**Pending:** Eviivo to grant `r:ApiRatePlans` API scope — once done, all bookings will classify correctly
regardless of whether a note was entered. Raise with Eviivo support.

### 5. Blocked Rooms
Rooms taken out of sale — maintenance, owner use, scheduled closures, group holds.
Data comes from a CSV export from Eviivo (not the API, which doesn't expose this data yet).

**How to update:** In Eviivo go to Dashboard → Standard Reports → History Blocks → Export.
Upload the CSV in the Blocked Rooms tab. The file is saved locally so you only need to re-upload when you want fresher data.

**Pending:** Eviivo to grant blocked rooms / stop-sell API scope — once done this will update automatically.
Raise with Eviivo support alongside the rate plans request.

### 6. Pace Report
Forward booking pace vs same point last year — by month, for the group and each property individually.
- **Signal:** 🟢 Strong (>15% ahead of LY), 🟡 Ahead (0–15%), 🟠 Behind (0–15% behind), 🔴 Weak (>15% behind)
- LY figures count only bookings made on or before the equivalent date last year (fair like-for-like)
- Per-property panels show the worst signal in the header so weak months are visible without opening each panel

### 7. Pick-up Report
Current occupancy on the books vs last year for each date in the next two months.
Follows the sidebar **Date basis** switch: arrivals per date (check-in) or rooms in house per night (stay).
Modelled on Lighthouse report format. Shows today's position only — daily snapshot storage would be
needed to show pick-up vs 7/14/28 days ago (future enhancement).

### 8. Booking Window
How far in advance guests book (lead time between booking creation and check-in), bucketed
from same-day to 181+ days. Shown by property so you can see which venues fill early and which
need a late push. OTAs typically show shorter lead times than direct bookings.

### 9. Potential Corporate Guests
Direct/phone guests (not OTA, not corporate travel agency) who've checked in midweek
(Sun–Thu) more than once since 1 Jan 2026 — a proxy for guests staying on business, worth
approaching about a corporate rate. Guests are matched by email, falling back to phone, then
name (Eviivo issues a fresh guest ID per booking so it can't be used to spot repeats).

### 10. Month-End Report
Single-month headline KPIs (revenue, RevPAR, ADR, occupancy), source mix, lead time,
cancellations, and blocked rooms, each vs the same month last year. Occupancy/RevPAR/ADR follow
the sidebar **Date basis** switch; source mix/lead time are always check-in-date based, so on stay
date the two sections won't reconcile for stays spanning a month boundary — that's expected. Exportable as a PDF via the
download button at the bottom of the tab.

### Date basis
The sidebar switch sets how Weekly / MTD, Pick-up and Month-End credit a booking:
- **Check-in date** (default) — all of a booking's room-nights and revenue count on its arrival date.
  Occupancy for a single day or short range can exceed 100%.
- **Stay date** — revenue is split evenly across each night in house.

---

## Properties

| Property | Eviivo Shortname | Rooms |
|---|---|---|
| The Bell & Crown | TheBellBA121 | 6 |
| The Dog & Gun | DogandGunSP4 | 6 |
| The Fleur de Lys | TheFleurdeLysInnBH21 | 9 |
| The Great Decoy | TheGreatDecoySP11 | 8 (opened 10 Jun 2026) |
| The Grosvenor Arms | TheGrosvenorArmsSP3 | 9 |
| The Manor House Inn | TheManorHouseInnBA4 | 9 |
| The Pembroke Arms | PembrokeSP2 | 9 |
| The Queen's Head | TheQueensHeadSP5 | 9 (4 before 1 Apr 2026) |

---

## Files

| File | Purpose |
|---|---|
| `dashboard.py` | Main Streamlit app |
| `eviivo.py` | Eviivo API client — auth, fetching, parsing |
| `config.py` | Credentials, property config, room counts, opening dates, closures |
| `data/blocks.csv` | Blocked rooms data (manual export from Eviivo) |

---

## Streamlit Cloud gotchas

**Python version is a dashboard setting, not a repo file.** `runtime.txt` is ignored by Streamlit
Community Cloud — it does not control the interpreter version. If a deploy starts crashing with a
Python version in the dependency-resolution log that doesn't match local dev (currently 3.12), fix
it in the app's **Settings → General → Python version** dropdown in the Streamlit Cloud UI, then
save to trigger a reboot.

**Pin every dependency version** in `requirements.txt` — an unpinned package (or the interpreter
itself) drifting forward on a routine redeploy has caused real outages here before (kaleido
`>=0.2.1` silently pulling a version that launches headless Chrome and segfaults in the sandbox).

---

## Pending Eviivo API items

Both of these require a single conversation with Eviivo support:

1. **Rate plan names** — `RatePlanId` always returns 0 in the API; need `r:ApiRatePlans` scope or the booking response fixed
2. **Blocked rooms** — stop-sell / block data not exposed via API; need `r:ApiInventory` or `r:ApiAvailability` scope

Once granted, both reports will populate automatically with no further code changes needed.
