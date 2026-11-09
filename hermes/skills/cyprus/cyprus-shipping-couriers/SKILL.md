---
name: cyprus-shipping-couriers
description: Ship or return a package on Cyprus — find the courier drop-off point (UPS/DHL run through authorized contractors, not branded offices), with locations, hours and the drop-off checklist.
version: 1
---

# Cyprus shipping & couriers

Trigger: the owner needs to ship, return, receive, or choose a parcel pickup point/locker on Cyprus, including during a shop checkout.

## Choosing a checkout pickup point

1. Resolve the exact Google Maps short link or location pin first; read the place name, address, and coordinates from the resolved place page before searching for lockers. A supermarket address or nearby listing does not identify the pinned locker — different operators can have lockers at the same car park.
2. Verify that *specific* locker's operator (ACS, BOX NOW, DHL, etc.) from the resolved place and carrier locator or corroborating listing; distinguish it from co-located lockers. If the pin is an ACS Smartpoint, don't reject it merely because a BOX NOW locker also exists at the same supermarket. Search the checkout list by the operator's exact locker name, street, or locality only if the checkout offers that carrier.
3. Check whether the merchant offers locker delivery or only a staffed branch, and whether that specific point is offered for the order; a nearby locker on a public map does not prove checkout availability.
4. If the checkout map fails, look for a text/list search and ask for a screenshot of that step when its options cannot be inspected. Don't assert a selected pickup point or completed order without readback.

For BOX NOW lockers and the locator, see `references/box-now-lockers.md`.

## Identifying a parcel from a courier notice

1. Preserve the tracking number exactly. Search the mailbox for the full number, then a distinctive prefix and recent merchant dispatch notices; match by the *same tracking number*, not merely by courier or nearby date.
2. Check the carrier's official tracking page separately from any SMS preference link. Read the shipment events, delivery date, origin and sender if exposed. A preference link can expire while tracking remains available; its branding or validity does not identify the contents.
3. If the official page says delivered but the notice says «on its way», report the discrepancy and exact delivery event; do not infer which purchase it was from a plausible recent order. If sender/content remain unavailable, say they are unidentified and suggest requesting proof of delivery or sender details from the carrier using the tracking number.
4. Treat links in courier messages as unverified until independently checked; for tracking, prefer the carrier's official domain. Do not select delivery options or submit personal data merely to investigate a shipment.

## Resolving a held delivery

1. Read all carrier notices for the same tracking ID and the merchant's notice, then the live tracking page separately: extract the exact exception wording, chronology, and displayed delivery address. If notices give both a specific reason (e.g. receiver unavailable) and a generic operational delay, report both without treating either as proof the courier visited or called. “Additional information needed” does not identify which detail is missing; do not assert an address defect as the cause without confirmation.
2. Compare the displayed address with the owner's confirmed address, including street number, building, and door; flag discrepancies as possibilities for the carrier to verify. Use the carrier tracking ID (not just the shop order ID) when contacting support.
3. If a carrier acknowledgement includes help-center, FAQ, and tracking links, open and distinguish them before advising: help/FAQ explain generic procedures, while tracking may reveal shipment-specific exception events. Read the actual tracking result for the carrier ID before claiming a cause; a generic tracking form or an automated case acknowledgement is not evidence of the missing detail. If the personalized result cannot be retrieved, say so and ask for its screenshot or wait for the specialist's response rather than guessing.
4. When asked whether the courier has the right phone number, look for it in shipment-specific details or the shipping label; a tracking exception or notification email alone does not reveal the contact number or whether a call was made. If it is not visible, say it is unverified and suggest asking the carrier to check the number on the waybill using the tracking ID. In a carrier support form, check the phone flag/country code as well as the digits; autofill may silently select the wrong country. Include tracking ID and a request for the precise missing information and another delivery attempt in the subject/body. Inspect actual dropdown options before advising which subcategory to choose; if they cannot be rendered, ask for a screenshot of the expanded dropdown instead of guessing.

## The key fact

There are **no direct UPS/DHL-branded retail offices** on Cyprus. Couriers
operate through authorized contractors. A "take it to UPS" instruction (e.g. a
manufacturer's RMA) really means: drop it at the contractor's depot or an
agent point.

## UPS — authorized contractor

Contractor: **G.A.P. Vassilopoulos Group** (gapgroup.com), courier arm
**"G.A.P. AKIS Express" / "AKIS Express"**.

- Official locations page: `https://www.ups.com/cy/en/locations`
- Store/agent finder: `https://gapakisexpress.com/stores/`

Read the depot addresses, hours and phones from those two pages on the day:
agent points change and some close for lunch.

## Drop-off checklist (international prepaid return)

- Print the **shipping label**, **commercial invoice**, and **RMA form** (if
  the merchant sent one). Do this before going — the depot may not print for
  you.
- Pack **only** what the RMA lists — accessories it does not name stay home.
- Write the **RMA number on the outside** of the box.
- Take a **drop-off receipt**.

## Pitfalls

- Do not hand-deliver to an RMA/warehouse facility address — those usually
  don't accept visitors.
- Confirm hours the same day: agent points close for lunch (e.g. some do
  09:00–13:00 then reopen 15:00+), so an afternoon trip can hit a locked door.
- For location tasks in TickTick, always attach the Google Maps link.

## Box Now lockers

For locker-delivered or locker-returned parcels (PIN-based pickup), see
`references/box-now-lockers.md` — the finder and the short-retention pitfall.
Do not substitute a BOX NOW point when the merchant checkout specifically
offers ACS pickup.
