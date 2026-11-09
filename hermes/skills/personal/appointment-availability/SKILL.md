---
name: appointment-availability
description: Use when checking available appointment slots at salons.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [appointments, salons, booking]
    related_skills: []
---

# Appointment availability

## When to Use

Use for checking open times at a salon, barbershop or other booking venue without necessarily booking.

## Procedure

1. Resolve the user's usual venue and specialist from current context or memory; confirm the local date and timezone with a live clock before interpreting “tomorrow” or relative dates.
2. Find the venue's official booking link, then open the live booking widget. If a legacy widget link redirects poorly, look for the provider's current booking domain and the same venue identifier; confirm the venue name and address in the widget before relying on it.
3. Select the actual requested service and, when relevant, the user's preferred specialist before reading the calendar. A specialist-list teaser may show generic next-available times that do not apply to the requested service; label teaser-only results provisional if the service cannot be selected.
4. Inspect the requested date and time window. Distinguish availability with the usual specialist from alternative specialists; report names and exact local times, with the booking page link if useful. Never infer that no other slots exist solely from the few teaser chips shown for each specialist.
5. Viewing slots is not booking. Stop before submitting an appointment or changing account state unless the user explicitly authorizes that action; report that no booking was made. When booking is explicitly requested, choose the specialist and provisional time, then select the actual service; verify the final date, start/end, duration and price before filling the customer's known contact details. Check the consent box, submit once, and read the confirmation page before claiming success. If an offscreen booking chip or consent checkbox reports a click without changing state, scroll it into view and inspect/click the actual DOM element, then verify the selected state or changed URL before continuing; an acknowledged click alone is not proof. Do not infer payment status from a listed price.
6. When also asked for a TickTick reminder, use the confirmed service's actual duration (not a generic default), requested priority and tag, include the verified venue address and direct Maps place link, then read the task back.

Booking widgets are often white-label: a salon's site may still link a legacy widget host while the live calendar sits on the provider's newer domain under the same venue number. Treat the widget as a discovery path, not a guarantee that a teaser slot is bookable for every service.
