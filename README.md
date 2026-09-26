# solverforge-pretix

Native SolverForge seat planning for pretix events where customers buy tickets
without selecting concrete seats.

## Run the deterministic demo

The shortest reproducible packaged demo path is:

```bash
uv build
uv venv --python /usr/bin/python3.14 .venv
export SOLVERFORGE_WHEEL="${SOLVERFORGE_WHEEL:-/srv/lab/dev/solverforge/solverforge-py/dist/solverforge-0.6.6-cp314-cp314-manylinux_2_34_x86_64.whl}"
uv pip install --python .venv/bin/python \
  "$SOLVERFORGE_WHEEL" \
  dist/solverforge_pretix-0.1.0-py3-none-any.whl \
  playwright==1.57.0
export PRETIX_CONFIG_FILE="$PWD/dev/pretix.cfg"
.venv/bin/python -m pretix migrate
.venv/bin/python -m pretix solverforge_seating_demo
.venv/bin/python -m pretix runserver 127.0.0.1:8345 --noreload
```

Open
<http://127.0.0.1:8345/control/event/solverforge-demo/assigned-seating/solverforge-seat-planner/>
and log in with:

- Email: `admin@solverforge.invalid`
- Password: `solverforge-demo`

The demo command only creates the reserved `solverforge-demo/assigned-seating`
event when it does not exist. It never deletes or resets other pretix data.

## Exact runtime contract

| Component | Required version |
| --- | --- |
| Python | 3.14 |
| pretix | 2026.6.1 |
| `solverforge` Python package | 0.6.6 |
| SolverForge core crates | 0.19.4 exactly |
| Native module | `solverforge._native` |

`pyproject.toml` pins the host and Python binding exactly. The recorded proof
used the pre-existing local CPython wheel whose embedded metadata identifies
`solverforge` 0.6.6. The allowed local binding source declares version 0.6.6,
and its six SolverForge Cargo dependencies and lockfile resolve to 0.19.4.

If that exact CPython 3.14 wheel is missing, build it without changing the
binding checkout:

```bash
cd /srv/lab/dev/solverforge/solverforge-py
make build-release
```

Do not use a free-threaded `cp314t` interpreter for the provided `cp314` wheel.
Select `/usr/bin/python3.14` explicitly.

Verify the loaded runtime:

```bash
.venv/bin/python - <<'PY'
import pretix
import solverforge
import solverforge._native as native

print(pretix.__version__)
print(solverforge.__version__)
print(native.__name__)
print(native.native_version())
PY
```

## Organizer workflow

1. Disable customer seat selection in the pretix event and sell normal
   admission positions.
2. Open **Orders → SolverForge Seat Planner**.
3. For an event series, select the concrete event date.
4. Review the live party, seat, blocked, occupied, and lock counts.
5. Select **Generate proposal**. This runs SolverForge in the pretix process;
   it does not write any seat assignment.
6. Review the visual map, hard/soft score explanation, and every proposed
   change.
7. Lock a proposed placement, or promote a valid existing pretix assignment to
   a hard lock, then select **Replan**.
8. Select **Commit proposal…**, review the validation contract, and tick the
   explicit confirmation checkbox.
9. The plugin recomputes the live snapshot under database locks, rescores the
   persisted blocks with native SolverForge, and applies every change through
   pretix `OrderChangeManager.change_seat`.

A stale input, nonzero final hard score, unsupported layout, or service failure
rejects the commit. The outer transaction rolls back all seat changes.
Recommitting the same committed proposal is idempotent.

## Architecture

This is a standard event-level pretix Django plugin:

- Package and pretix entry point: `pretix_solverforge_seating`
- Event navigation, permissions, views, templates, and static assets use the
  pretix control-panel stack.
- `PlannerConfiguration`, `PlacementLock`, and `SeatingProposal` are the only
  plugin-owned database models. Locks and proposals are scoped to a concrete
  subevent date when used in an event series.
- `extraction.py` reads live `Seat`, `Order`, `OrderPosition`,
  `QuestionAnswer`, cart reservation, voucher reservation, and lock records.
- `domain.py` validates row geometry and generates exact contiguous
  `SeatBlock` facts.
- `planning.py` declares the SolverForge planning model and calls
  `Solver.solve` in-process. The Solver Python API invokes
  `solverforge._native`; there is no service, subprocess, CLI solver, wrapper,
  fallback optimizer, or second planning engine.
- `proposals.py` persists an immutable proposal plus its input fingerprint.
  Raw attendee answers and personal data are not stored in proposals or sent
  outside the process.
- `commit.py` locks the event, proposal, configuration, organizer locks,
  orders, positions, and target seats; validates freshness and hard
  feasibility; then performs a transactional detach/attach sequence through
  pretix's supported order-change service.

Solve and preview never modify `OrderPosition.seat`.

## Planning model

### Facts

- Every concrete pretix seat: database ID, stable seat GUID, zone, row, seat
  number, coordinates, sorting rank, mapped product, blocked state, occupancy,
  accessibility marker, aisle marker, and live pretix distance conflicts.
- Active paid and pending admission positions.
- Attendee requirements and preferences derived from configured question
  identifiers, including the exact order positions carrying wheelchair answers.
- Existing assignments and organizer locks.
- Every feasible contiguous row block for each party size present in the event.

Rows are ordered by x-coordinate. A coordinate gap greater than 1.75 times the
ordinary row pitch splits a physical run, so blocks never cross an aisle.

### Entity and variable

`PartyAssignment` is the only planning entity. A party groups compatible
positions from one order; positions mapped to different pretix products are
split deterministically.

Its single scalar planning variable, `seat_block_idx`, selects one
`SeatBlock`. A block contains the exact pretix seat IDs it allocates. There are
no quantity, integer-programming, or mixed-integer variables.

### Hard constraints

- Every party has a block of exactly its size.
- Every seat in the block maps to the party's pretix product.
- Blocked, reserved, and immutable occupied seats are excluded.
- Every wheelchair-marked order position receives its own configured accessible
  seat inside the selected block.
- A companion requirement requires a party of at least two in the same
  contiguous block.
- Organizer locks preserve the exact position-to-seat mapping, not only the
  selected seat set.
- Selected blocks do not overlap.
- Blocks assigned to different orders satisfy pretix's strict Euclidean
  minimum-seat-distance threshold. Positions from one order retain pretix's
  same-order exemption, and the within-row setting is honored exactly.
- The persisted proposal is not committable unless native SolverForge reports
  zero hard violations and the independent explanation also totals zero.

### Soft constraints

- Front, rear, aisle, and zone preferences.
- Configured zone-quality rewards.
- A penalty for changing existing but unlocked assignments.
- Exact isolated-single-seat penalties only when the event contains no
  one-person party for that product.
- Pairwise satisfaction balancing so high-quality seats are not assigned only
  according to order chronology.

Parties already keep same-order compatible positions together because they are
one entity. Incompatible product parties cannot be falsely treated as one
block.

### Construction and local search

The deterministic solver configuration uses:

1. `cheapest_insertion` construction; then
2. scalar change and compatible scalar swap selectors; with
3. hill-climbing acceptance, best-score foraging, a fixed seed, and a bounded
   step count.

The regression suite starts with overlapping blocks at a negative hard score
and proves actual SolverForge local search reaches zero hard violations.

## Configuration

Open **Settings → SolverForge Seat Planner**.

### Questions

Set pretix question identifiers for:

- **Wheelchair question**: normally a Boolean question. Each nonempty truthy
  position answer requires one accessible seat, mapped back to that exact
  order position.
- **Companion question**: normally a Boolean question. A truthy answer requires
  a contiguous party of at least two including an accessible seat.
- **Seat preference question**: text, single-choice, or multiple-choice.

Supported preference values or pretix option identifiers are:

- `front`
- `rear`
- `aisle`
- `zone-ZONE-SLUG`, for example `zone-main-balcony`

pretix option identifiers cannot contain spaces or colons. Zone slugs are
matched case-insensitively after punctuation and whitespace normalization.

### Seat and zone configuration

- **Accessible seat IDs**: pretix `seat_guid` values, separated by whitespace,
  commas, semicolons, or newlines.
- **Aisle seat IDs**: seats adjacent to an aisle, in the same format.
- **Zone quality**: a JSON object with integer values from 0 through 100, for
  example `{"Stalls": 90, "Balcony": 45}`.
- **Deterministic random seed** and **local-search step limit** control
  reproducibility and bounded solve effort.

Any configuration change makes a current proposal stale.

For event series, this configuration is event-wide while locks, snapshots,
proposals, reservations, seat records, and commits are scoped to the selected
date.

## Deterministic demo data

`solverforge_seating_demo` creates:

- 48 real pretix seats across Stalls and Balcony zones;
- four horizontal rows, each split by a physical aisle;
- Standard and Premium product/category mappings;
- five real paid orders with parties of sizes 1, 2, 3, 4, and 5;
- wheelchair and companion answers;
- front, rear, aisle, and Stalls-zone preferences;
- blocked seats `B-04` and `D-08`;
- a nonzero 31-coordinate minimum distance enforced within each row;
- a hard-locked wheelchair pair at `A-01` and `A-02`;
- fragmented or low-quality existing assignments for visible improvement.

The fixed solve moves SF003 from fragmented Balcony seats to
`A-07`–`A-09`, while both the original wheelchair lock and a lock added during
preview remain fixed.

## Tests and quality gates

```bash
uv pip install --python .venv/bin/python '.[dev]'
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m pytest -q
PRETIX_CONFIG_FILE="$PWD/dev/pretix.cfg" \
  .venv/bin/python -m pretix makemigrations \
  pretix_solverforge_seating --check --dry-run
PRETIX_CONFIG_FILE="$PWD/dev/pretix.cfg" \
  .venv/bin/python -m pretix check
node --check pretix_solverforge_seating/static/pretix_solverforge_seating/planner.js
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

The focused suite covers block generation, product and size compatibility,
overlap, accessibility/companion requirements, blocked and occupied seats,
locks, pretix minimum distance, event-series date isolation, preference
scoring, change and swap moves, fixed-seed determinism, staleness, idempotency,
rollback, real order-position service updates, plugin discovery/navigation,
and permissions.

## Browser proof

Using the packaged environment above and a fresh browser-proof database, start
pretix in one terminal:

```bash
export PRETIX_CONFIG_FILE="$PWD/dev/pretix-e2e.cfg"
.venv/bin/python -m pretix migrate
.venv/bin/python -m pretix solverforge_seating_demo
.venv/bin/python -m pretix runserver 127.0.0.1:8345 --noreload
```

Then run:

```bash
.venv/bin/python scripts/browser_proof.py
```

The script drives the actual control panel through login, initial map, native
solve, lock, replan, explicit confirmation, commit, and the ordinary pretix
order detail. It saves screenshots under `evidence/screenshots/` and fails if
the final order does not show the committed seats.

For the persistence check, stop the server with `Ctrl-C`, run the same
`pretix runserver` command again, and reopen:

<http://127.0.0.1:8345/control/event/solverforge-demo/assigned-seating/orders/SF003/>

The recorded evidence and its interpretation are documented in
[`evidence/README.md`](evidence/README.md).

## Supported scope

The planner supports both ordinary events and event-series dates, including
pretix's nonzero minimum-seat-distance settings. Its deliberate topology
boundary is horizontal row seating with concrete coordinates. Tables,
nonhorizontal rows, and arbitrary free-form adjacency are rejected because
pretix does not provide an unambiguous contiguous-block graph for them.

A party is one order plus one compatible pretix product; custom cross-order
household identifiers are not exposed. Accessibility and aisle markers are
configured by seat GUID because pretix has no universal native flags for those
semantics. The isolated-seat score remains conservative and only penalizes
provable one-seat gaps.

Solves run synchronously in the pretix process with a configured step bound.
The included SQLite configuration is for development; production deployments
should use a pretix-supported transactional database so row locks provide the
intended concurrent-commit behavior.

## License

AGPL-3.0-or-later. See [`LICENSE`](LICENSE).

## Upstream references

- [pretix plugin API](https://docs.pretix.eu/dev/development/api/plugins.html)
- [pretix Seats API](https://docs.pretix.eu/dev/api/resources/seats.html)
- [pretix Orders API](https://docs.pretix.eu/dev/api/resources/orders.html)
- [pretix 2026.6.1 source](https://github.com/pretix/pretix/tree/v2026.6.1)
