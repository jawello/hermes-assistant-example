# Box Now lockers (Cyprus) — receive & send via locker

Box Now (boxnow.cy) is a 24/7 locker parcel network on Cyprus. Parcels are
collected (or dropped off) with a **6-digit PIN** sent by SMS/email. Used for
merchant orders delivered to a locker.

## Locker finder

- `https://boxnow.cy/en/locker-finder` is an **embedded OpenStreetMap widget** —
  a `web_extract` of that page returns only tile-image URLs, not locker names.
  The human-readable locker list comes from that page's search-results
  endpoints or from aggregators (search "<street>, box now locker <area>").
- The owner's usual lockers live in memory, not here: `hindsight_recall`
  "box now locker".

## Pitfalls

- **Retention is short** (typically ~24–48 h, extendable in the app). If the
  pickup is planned several days after arrival (e.g. "on Monday"), flag that
  the PIN may expire and that the app can extend it — don't silently schedule
  it.
- **Two PINs = two compartments**, often two parts of one order — list both.

## TickTick task convention

The errand's content carries the locker address + 🗺️ Google Maps link + one
`- [ ]` checklist line per PIN / tracking number.
