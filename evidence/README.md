# Runtime evidence

These images were captured on 2026-07-29 from a real pretix 2026.6.1 process.
The process ran outside the source tree from a clean CPython 3.14.6 virtual
environment containing the built `solverforge-pretix` wheel and the exact
`solverforge` 0.6.4 CPython wheel. The loaded native module was
`solverforge._native`; the binding source pins SolverForge core 0.19.2 exactly.
The demo used pretix minimum seat distance `31` with distance enforced within
each row.

| Evidence | What it proves |
| --- | --- |
| [`before-solve.png`](screenshots/before-solve.png) | Native pretix navigation, 48-seat map, live counts, blocked/occupied seats, poor existing assignments, and the original organizer lock before any proposal exists. |
| [`after-solve.png`](screenshots/after-solve.png) | Preview-only proposal for all 15 positions, party colors, change explanations, zero minimum-distance violations, and native score `0 hard / 237 soft`. |
| [`after-lock-replan.png`](screenshots/after-lock-replan.png) | SF003 locked at `A-07`–`A-09`, replanned without moving it, alongside the original wheelchair lock. |
| [`after-commit.png`](screenshots/after-commit.png) | Explicit commit success for 8 changed positions, all 15 committed seats, zero hard violations, and persisted pretix assignments on the same map. |
| [`pretix-order-after-commit.png`](screenshots/pretix-order-after-commit.png) | Ordinary pretix order detail for SF003 showing `Stalls, Row A, Seat 7`, `8`, and `9`, plus pretix's own order-change history. |
| [`after-restart.png`](screenshots/after-restart.png) | The same ordinary pretix order detail loaded after stopping and restarting the web process. |

The browser workflow checked that no console errors were emitted. An ORM
comparison matched all 15 proposal position/seat pairs after commit. It also
verified that the wheelchair-marked SF002 position remained at accessible seat
`A-01`, the original SF002 lock remained `A-01`–`A-02`, and the newly added
SF003 lock remained `A-07`–`A-09`.

The pretix process was then stopped and started again from the same packaged
environment. Both the ordinary order UI and a new ORM process still reported
SF003 at `A-07`, `A-08`, and `A-09`.

Regenerate the evidence on a fresh demo database with:

```bash
.venv/bin/python scripts/browser_proof.py
```

See the **Browser proof** section in the project README for the fresh database,
packaged install, server, and restart commands.
