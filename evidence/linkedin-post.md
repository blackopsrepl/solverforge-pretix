# LinkedIn launch post

Before posting, turn the first `@pretix` into a real LinkedIn company-page
mention. The repository URL declared by the package is not public yet, so this
copy deliberately does not send readers to a dead link.

## Post copy

People bought their tickets. Now someone still has to decide where everyone
sits.

For events where guests choose a ticket category but not a specific seat, that
can mean hours of manual work: keeping families and groups together, placing
wheelchair users and companions correctly, avoiding blocked seats, respecting
preferences, and preserving placements that must not move.

pretix already knows the venue and the orders. I built SolverForge Seat Planner
to turn that information into a seating proposal the organizer can actually
control.

Inside the pretix control panel, the organizer can:

→ Generate a complete seating proposal

→ Review every party on a visual seat map

→ Compare proposed seats with the current arrangement

→ Lock any placement and replan everything else

→ Commit only when the result looks right

The important part is that automation does not take control away.

Nothing changes while the organizer is reviewing the plan. After commit, the
assigned seats appear directly on the real pretix orders, exactly where the
event team already works.

The demo seats 15 ticket holders in parties of 1–5, including a wheelchair user
and companion. It respects blocked seats and organizer locks while improving a
deliberately poor starting arrangement.

No spreadsheet shuffle. No separate plan to reconcile later. The entire
workflow stays inside pretix.

@pretix team: I would genuinely value your feedback. Would an organizer-side
automatic seat planner be useful for the events you see? Which venue layouts
and organizer controls would matter most?

For everyone building operational software: this is the promise of
SolverForge—turning real-world rules and human choices into plans people can
inspect, adjust, and trust.

#pretix #EventTech #OpenSource #Operations #ConstraintSolving

## Recommended image order

Both images are ready at LinkedIn's 4:5 maximum ratio and 1080 × 1350 pixels:

1. [`01-proposal-4x5.png`](linkedin/01-proposal-4x5.png) — Lead image. It shows
   the complete proposal inside pretix, with a clear preview-only notice and
   every party visible on the seat map before anything changes.
2. [`02-pretix-order-4x5.png`](linkedin/02-pretix-order-4x5.png) — Close with
   the strongest host-integration proof: the ordinary pretix order page shows
   seats A-07, A-08, and A-09 plus pretix's own order-change history.

## Image alt text

1. SolverForge Seat Planner inside the pretix control panel, showing a
   preview-only proposal for 15 ticket holders on a visual seat map before any
   assignment is committed.
2. Standard pretix order detail for order SF003, showing assigned seats 7, 8,
   and 9 in Stalls row A and the matching entries in pretix's order history.
