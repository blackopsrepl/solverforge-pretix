# Marketplace submission

This file is the text to send to the pretix team when proposing
`solverforge-pretix` for listing in the pretix plugin marketplace
(**Settings → Plugins**). pretix maintains the marketplace list itself;
there is no self-serve upload.

## Package

- PyPI: `solverforge-pretix`
- Source: https://github.com/blackopsrepl/solverforge-pretix
- License: AGPL-3.0-or-later
- Plugin app label: `pretix_solverforge_seating`
- Plugin level: event
- Category: `FEATURE`
- Compatibility: `pretix>=2026.6.0`

## Proposed listing text

**Name:** SolverForge Seat Planner

**Description:**

> For events that sell ticket categories without seat selection, an organizer
> still has to decide who sits where. SolverForge Seat Planner turns the
> event's own seats, orders, and attendee answers into a seating proposal the
> organizer can inspect and control.
>
> Open **Orders → SolverForge Seat Planner** to generate a proposal, review
> every party on a visual seat map, compare it with the current arrangement,
> lock any placement and replan the rest, then commit explicitly. Nothing
> changes until the organizer confirms; after commit the assigned seats appear
> directly on the real pretix orders.
>
> It respects wheelchair accessibility, companion requirements, aisle and zone
> preferences, blocked and occupied seats, organizer locks, and pretix's
> minimum-seat-distance settings. Solving runs natively inside the pretix
> process; no external service is required.

## Notes for reviewers

- The planner is implemented as a standard event-level Django plugin using the
  documented plugin API (`Plugins →` signals, `OrderChangeManager.change_seat`
  for commits).
- Solving uses the native `solverforge` Python package in-process. It is a
  normal Python dependency; there is no web service, subprocess, or solver
  binary.
- Commits are transactional and reject stale input, nonzero hard score, or
  unsupported layouts; the outer transaction rolls back all seat changes.
- Requires CPython 3.14 and `solverforge` 0.6.6.
