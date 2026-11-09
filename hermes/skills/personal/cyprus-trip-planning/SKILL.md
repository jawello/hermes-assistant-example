---
name: cyprus-trip-planning
version: 1.0.1
description: Plan multi-stop car trips on Cyprus — resolve Google Maps links, calculate driving times with buffers, build departure schedules.
triggers:
  - User asks to plan a route, trip, or drive between locations on Cyprus
  - User shares Google Maps links and wants a schedule with time calculations
  - Multi-stop trips with pickups/dropoffs before appointments
---

# Cyprus Trip Planning

Plan car trips on Cyprus: resolve locations, calculate driving times, build schedules with buffers.

## Step 1 — Resolve locations

User may share:
- **Shortened Google Maps links** (`https://maps.app.goo.gl/...`): use `web_extract` to resolve. The title/coordinates/plus-code appear in the extracted content.
- **Raw coordinates**: use as-is.
- **Named places**: search or use known addresses from Hindsight. For an exact Google Maps point, open the named place's Maps result, confirm its title and locality, then read the place URL (e.g. `browser_console` with `location.href`). The `!3dLAT!4dLON` pair identifies the place; the earlier `@LAT,LON,ZOOM` segment is the viewport centre and may differ. Share a point link such as `https://www.google.com/maps/search/?api=1&query=LAT%2CLON` rather than an unverified search URL.

When asked to add a destination to an existing places-to-visit list, identify that list's actual storage and read its entries before writing; a map point alone is not an addition. If the target list remains ambiguous after checking relevant notes/tasks and saved context, ask which list instead of creating a parallel one. Verify the new entry by reading the target back.

Home base: `hindsight_recall "домашний адрес"`. These facts live in Hindsight. Do not copy them into this skill.

## Step 2 — Get driving times

Use `browser_navigate` to Google Maps directions URLs:

```
https://www.google.com/maps/dir/ORIGIN/DESTINATION/
```

For multi-stop (A → B → C):
```
https://www.google.com/maps/dir/A/B/C/
```

URL-encode addresses. Coordinates can be used directly (Google Maps accepts them).

**Pitfall**: multi-stop combined view may show only total time in snapshot, not per-leg breakdown. If you need per-leg times, do separate two-point queries.

**Pitfall**: browser_navigate sometimes times out on directions pages. If it does, compute the missing leg from the total minus known legs.

**Pitfall**: Google Maps UI language may be random (Finnish, Greek, etc.) — read the numbers (e.g., "24 min", "21,5 km"), ignore the surrounding text.

## Step 3 — Build schedule

1. Start from the **fixed appointment time** and work backwards.
2. For each leg: driving time from Google Maps.
3. Add **stop time** for pickups/dropoffs (default 10 min unless user says otherwise).
4. Add **buffer**: aim for 10–15 min at the final destination before the appointment.
5. Present as a table:

```
| Время | Событие |
|-------|---------|
| 08:50 | 🚗 Выезд из дома |
| 09:14 | 📍 Прибытие к точке Б (XX мин, YY км) |
| 09:25 | 🚗 Выезд от точки Б (остановка ~10 мин) |
| 10:47 | 📍 Прибытие в точку В (XX мин, YY км) |
| 11:00 | 📌 Встреча (запас 13 мин) |
```

## Step 4 — Ask clarifying questions

Before finalising, ask about:
- Who/what is being picked up (for Hindsight memory)
- Any special timing constraints
- Whether the appointment is confirmed or needs booking
- **Locker / PIN pickups** (Skroutz Point, Box Now, ACS lockers, etc.): extract the locker number, PIN, and any hard deadline (e.g. «до субботы, до 18:00»). Surface the deadline *before* the schedule — it is a hard constraint, not a suggestion.

## Pitfalls

- **Short links**: `web_extract` resolves goo.gl/maps links to coordinates; content includes title and plus-code.
- **Browser timeouts**: if browser_navigate times out on complex directions, fall back to arithmetic from a successful combined query.
- **Time zones**: all times are Cyprus local (UTC+3 in summer, UTC+2 in winter). TickTick stores in UTC — the API auto-converts when you pass `+03:00` offsets.
- **Optimal stop order (round-trip)**: to compare `home→A→B→home` vs `home→B→A→home`, query each two-point leg separately (home↔A, home↔B, A↔B) and sum per order — the combined multi-stop snapshot often shows only the total, not per-leg. Rule of thumb: the stop closest to home goes *last* on the way back; the far stop goes first.
