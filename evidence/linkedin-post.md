# LinkedIn launch post

Before posting, turn the first `@pretix` into a real LinkedIn company-page
mention. The repository URL declared by the package is not public yet, so this
copy deliberately does not send readers to a dead link.

## Post copy

Assigned seating should not require a spreadsheet round-trip.

I built SolverForge Seat Planner as a standard event-level @pretix plugin
for events where customers buy tickets without choosing their own seats.

The plugin reads the real pretix event: its seating plan, concrete seats,
orders, order positions, attendee answers, blocked and occupied seats, existing
assignments, and organizer locks. SolverForge then assigns each compatible
party to an exact contiguous seat block, directly inside the pretix process.

The organizer workflow stays in pretix:

→ Generate a proposal  
→ Preview every placement on the visual seat map  
→ Lock a party and replan  
→ Commit only after explicit confirmation

Nothing is written during solving or preview. At commit time, the plugin checks
that orders, seats, configuration, and locks still match the proposal snapshot;
rejects stale or infeasible plans; and applies the changes transactionally
through pretix's supported OrderChangeManager.change_seat service.

The planning model treats:

• party size, product/category, accessibility and companion needs,
blocked/occupied seats, minimum seat distance, overlap, and locks as hard
constraints  
• front/rear/aisle/zone preferences, seat quality, disruption to existing
assignments, isolated-seat risk, and fairness as soft objectives

pretix remains the source of truth. There is no parallel seating database, CSV
round-trip, HTTP solver, subprocess, or second optimizer. The execution path is:

pretix → solverforge Python → solverforge._native → SolverForge's Rust engine

The deterministic proof uses 48 real pretix seats, 5 parties and 15 sold ticket
positions, with party sizes 1–5, two zones, aisles, blocked seats, wheelchair
and companion requirements, preferences, and a deliberately poor initial
arrangement.

Result:

• 0 hard violations  
• all 15 positions assigned  
• organizer locks preserved  
• 8 positions changed through pretix  
• seats visible in the ordinary pretix order view  
• assignments still present after restart

Built and verified against Python 3.14.6, pretix 2026.6.1, solverforge 0.6.4,
and SolverForge core 0.19.2.

@pretix team: I would genuinely value your feedback. Does organizer-side
automatic seating fit the workflows you see in the field? Which venue layouts
should be supported next?

For everyone else: this is how I want SolverForge integrations to work—planning
inside the product, where people can review, constrain, and safely apply the
result.

#pretix #EventTech #ConstraintSolving #OpenSource #Rust

## Recommended image order

1. [`after-solve.png`](screenshots/after-solve.png) — Lead image. It shows the
   proposal inside native pretix, the visual seat map, the preview-only notice,
   the complete placement table, and the zero-hard score.
2. [`after-lock-replan.png`](screenshots/after-lock-replan.png) — It proves
   organizer control: the wheelchair placement and the newly chosen party
   placement remain locked after replanning.
3. [`after-commit.png`](screenshots/after-commit.png) — It shows the explicit
   commit result, all 15 committed seats, 8 changed positions, and zero hard
   violations.
4. [`pretix-order-after-commit.png`](screenshots/pretix-order-after-commit.png)
   — Close with the strongest host-integration proof: the ordinary pretix order
   page shows seats A-07, A-08, and A-09 plus pretix's own order-change history.

## Image alt text

1. SolverForge Seat Planner inside the pretix control panel, showing a
   preview-only proposal for 15 ticket positions on a 48-seat map, with native
   score 0 hard and 237 soft.
2. Replanned pretix seat proposal with two organizer locks preserved, including
   an accessible two-seat placement and a locked three-person party.
3. Committed SolverForge proposal in pretix, showing 15 committed seats, 8
   changed order positions, and zero hard violations.
4. Standard pretix order detail for order SF003, showing three assigned seats
   in Stalls row A and the corresponding seat changes in pretix's order history.
