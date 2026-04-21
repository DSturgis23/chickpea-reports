# Chickpea — Hotel Performance Reports

Standalone Streamlit app pulling accommodation data from the Eviivo PMS API.
Runs separately from the SevenRooms reservations dashboard on port **8502**.

## Running the app

```
cd "C:\Users\Delilah Sturgis\chickpea_reports"
streamlit run dashboard.py --server.port 8502
```

Open http://localhost:8502 — password: `chickpea2024`

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
Revenue is allocated proportionally across each night of a stay rather than attributed entirely to check-in date.

### 3. Source Report
Booking channel breakdown (Booking.com, Expedia, Agoda, direct, corporate travel, etc.) for a chosen date range.
Channel is detected from guest email domain — OTAs use anonymised addresses (e.g. `@guest.booking.com`).

### 4. Rate Plans
Revenue and booking volume by rate plan (B&B, Room Only, Dinner B&B, Half Board) for a chosen date range.
Rate plan is detected from the booking note field. OTA bookings classify reliably (~100%); direct bookings
classify where staff have entered a rate plan in the Eviivo booking note.

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
Modelled on Lighthouse report format. Shows today's position only — daily snapshot storage would be
needed to show pick-up vs 7/14/28 days ago (future enhancement).

---

## Properties

| Property | Eviivo Shortname | Rooms |
|---|---|---|
| The Bell & Crown | TheBellBA121 | 6 |
| The Dog & Gun | DogandGunSP4 | 6 |
| The Fleur de Lys | TheFleurdeLysInnBH21 | 9 |
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

## Pending Eviivo API items

Both of these require a single conversation with Eviivo support:

1. **Rate plan names** — `RatePlanId` always returns 0 in the API; need `r:ApiRatePlans` scope or the booking response fixed
2. **Blocked rooms** — stop-sell / block data not exposed via API; need `r:ApiInventory` or `r:ApiAvailability` scope

Once granted, both reports will populate automatically with no further code changes needed.
