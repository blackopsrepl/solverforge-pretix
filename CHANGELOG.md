# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-09-26

First public release.

### Features

- Event-level pretix plugin (`pretix_solverforge_seating`) that proposes
  concrete seat assignments for tickets sold without seat selection.
- In-process native solving through the `solverforge` Python API and the
  `solverforge._native` PyO3 extension; no web service, subprocess, wrapper,
  or fallback optimizer.
- Organizer workflow: generate a proposal, review the visual seat map, lock
  placements, replan, and commit explicitly.
- Transactional commit through pretix's supported `OrderChangeManager.change_seat`
  service, with stale-input rejection, hard-score gating, and idempotent
  re-commit.
- Support for ordinary events and event-series dates, wheelchair and companion
  requirements, aisle and zone preferences, blocked and occupied seats,
  organizer locks, and pretix minimum-seat-distance settings.
- Deterministic demo (`solverforge_seating_demo`) with a fixed seed.

### Notes

- Requires CPython 3.14, pretix 2026.6.1 or later, and `solverforge` 0.6.6.
- Horizontal row seating with concrete coordinates is supported; tables and
  arbitrary free-form adjacency are rejected.
- Licensed AGPL-3.0-or-later, matching the pretix integration boundary.
