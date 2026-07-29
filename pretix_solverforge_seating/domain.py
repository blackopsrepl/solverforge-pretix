from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Any


class PlanningInputError(ValueError):
    """The live pretix event cannot be represented by the supported planning model."""


class UnsupportedLayoutError(PlanningInputError):
    """The seating layout is not an unambiguous row-based layout."""


class InfeasiblePlanError(PlanningInputError):
    """The current hard requirements do not have an assignable candidate."""


class StaleProposalError(RuntimeError):
    """The proposal no longer matches the live pretix inputs."""


class CommitError(RuntimeError):
    """A proposal cannot be committed safely."""


@dataclass(frozen=True, slots=True)
class SeatFact:
    id: int
    guid: str
    zone: str
    row: str
    seat_number: str
    product_id: int | None
    blocked: bool
    occupied: bool
    x: float | None
    y: float | None
    sorting_rank: int
    accessible: bool = False
    aisle: bool = False

    @property
    def available(self) -> bool:
        return not self.blocked and not self.occupied


@dataclass(frozen=True, slots=True)
class SeatBlock:
    idx: int
    seat_ids: tuple[int, ...]
    seat_guids: tuple[str, ...]
    product_ids: tuple[int | None, ...]
    zone: str
    row: str
    run_id: str
    start: int
    end: int
    run_length: int
    average_x: float
    average_y: float
    front_rank: int
    rear_rank: int
    quality: int
    accessible_seat_ids: tuple[int, ...]
    accessible_count: int
    aisle_count: int
    blocked_count: int
    occupied_count: int
    usable_segment_id: str | None
    segment_start: int | None
    segment_end: int | None
    segment_length: int | None

    @property
    def size(self) -> int:
        return len(self.seat_ids)

    @property
    def product_id(self) -> int | None:
        first = self.product_ids[0]
        return first if all(product_id == first for product_id in self.product_ids) else None

    @property
    def available(self) -> bool:
        return self.blocked_count == 0 and self.occupied_count == 0


@dataclass(frozen=True, slots=True)
class PartySpec:
    key: str
    order_id: int
    order_code: str
    position_ids: tuple[int, ...]
    product_id: int
    product_name: str
    preferences: tuple[str, ...] = ()
    wheelchair_position_ids: tuple[int, ...] = ()
    requires_companion: bool = False
    current_seat_guids: tuple[str, ...] = ()
    locked_seat_guids: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return len(self.position_ids)

    @property
    def requires_wheelchair(self) -> bool:
        return bool(self.wheelchair_position_ids)

    @property
    def wheelchair_count(self) -> int:
        return len(self.wheelchair_position_ids)


@dataclass(frozen=True, slots=True)
class PlanningSnapshot:
    event_id: int
    seats: tuple[SeatFact, ...]
    parties: tuple[PartySpec, ...]
    fingerprint: str
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PhysicalRun:
    run_id: str
    zone: str
    row: str
    seats: tuple[SeatFact, ...]


def generate_contiguous_blocks(
    seats: tuple[SeatFact, ...] | list[SeatFact],
    party_sizes: set[int] | frozenset[int],
    zone_quality: dict[str, int] | None = None,
) -> tuple[SeatBlock, ...]:
    """Generate every contiguous row block for the requested party sizes."""

    sizes = sorted(size for size in party_sizes if size > 0)
    if not sizes:
        return ()
    runs = _validated_physical_runs(tuple(seats))
    if not runs:
        raise UnsupportedLayoutError("The event seating plan does not contain concrete row seats.")

    row_order = {
        row_key: rank
        for rank, row_key in enumerate(
            sorted(
                {(run.zone, run.row, _row_y(run)) for run in runs},
                key=lambda item: (item[2], item[0], item[1]),
            )
        )
    }
    max_row_rank = max(row_order.values(), default=0)
    quality_by_zone = zone_quality or {}
    blocks: list[SeatBlock] = []

    for run in runs:
        segment_by_seat = _usable_segments(run)
        row_rank = row_order[(run.zone, run.row, _row_y(run))]
        for size in sizes:
            for start in range(0, len(run.seats) - size + 1):
                window = run.seats[start : start + size]
                segment_ids = {segment_by_seat[seat.id][0] for seat in window}
                segment_id: str | None = None
                segment_start: int | None = None
                segment_end: int | None = None
                segment_length: int | None = None
                if len(segment_ids) == 1 and None not in segment_ids:
                    segment_id = next(iter(segment_ids))
                    first_meta = segment_by_seat[window[0].id]
                    last_meta = segment_by_seat[window[-1].id]
                    segment_start = first_meta[1]
                    segment_end = last_meta[1]
                    segment_length = first_meta[2]

                blocks.append(
                    SeatBlock(
                        idx=len(blocks),
                        seat_ids=tuple(seat.id for seat in window),
                        seat_guids=tuple(seat.guid for seat in window),
                        product_ids=tuple(seat.product_id for seat in window),
                        zone=run.zone,
                        row=run.row,
                        run_id=run.run_id,
                        start=start,
                        end=start + size - 1,
                        run_length=len(run.seats),
                        average_x=sum(_coordinate(seat.x) for seat in window) / size,
                        average_y=sum(_coordinate(seat.y) for seat in window) / size,
                        front_rank=row_rank,
                        rear_rank=max_row_rank - row_rank,
                        quality=int(quality_by_zone.get(run.zone, 0)),
                        accessible_seat_ids=tuple(
                            seat.id for seat in window if seat.accessible
                        ),
                        accessible_count=sum(int(seat.accessible) for seat in window),
                        aisle_count=sum(int(seat.aisle) for seat in window),
                        blocked_count=sum(int(seat.blocked) for seat in window),
                        occupied_count=sum(int(seat.occupied) for seat in window),
                        usable_segment_id=segment_id,
                        segment_start=segment_start,
                        segment_end=segment_end,
                        segment_length=segment_length,
                    )
                )
    return tuple(blocks)


def with_seat_configuration(
    seats: tuple[SeatFact, ...],
    *,
    accessible_guids: set[str] | frozenset[str],
    aisle_guids: set[str] | frozenset[str],
) -> tuple[SeatFact, ...]:
    return tuple(
        replace(
            seat,
            accessible=seat.guid in accessible_guids,
            aisle=seat.guid in aisle_guids,
        )
        for seat in seats
    )


def find_exact_block(
    blocks: tuple[SeatBlock, ...],
    seat_guids: tuple[str, ...] | list[str],
) -> SeatBlock | None:
    target = frozenset(seat_guids)
    if not target:
        return None
    return next(
        (block for block in blocks if frozenset(block.seat_guids) == target),
        None,
    )


def _validated_physical_runs(seats: tuple[SeatFact, ...]) -> tuple[_PhysicalRun, ...]:
    grouped: dict[tuple[str, str], list[SeatFact]] = {}
    for seat in seats:
        if not seat.row.strip():
            raise UnsupportedLayoutError(
                f'Seat "{seat.guid}" has no row identifier; '
                "table/free-form layouts are unsupported."
            )
        if seat.x is None or seat.y is None:
            raise UnsupportedLayoutError(
                f'Seat "{seat.guid}" has no coordinates; row adjacency cannot be inferred safely.'
            )
        grouped.setdefault((seat.zone, seat.row), []).append(seat)

    runs: list[_PhysicalRun] = []
    for (zone, row), row_seats in sorted(grouped.items()):
        y_values = [_coordinate(seat.y) for seat in row_seats]
        if max(y_values) - min(y_values) > 0.5:
            raise UnsupportedLayoutError(
                f'Row "{row}" in zone "{zone}" is not horizontal; '
                "arbitrary/table layouts are unsupported."
            )
        ordered = sorted(
            row_seats,
            key=lambda seat: (_coordinate(seat.x), seat.sorting_rank, seat.guid),
        )
        x_values = [_coordinate(seat.x) for seat in ordered]
        if len(x_values) != len(set(x_values)):
            raise UnsupportedLayoutError(
                f'Row "{row}" in zone "{zone}" has duplicate x-coordinates; adjacency is ambiguous.'
            )

        split_after = _aisle_splits(x_values)
        run_start = 0
        run_number = 0
        for index in range(len(ordered)):
            is_last = index == len(ordered) - 1
            if not is_last and index not in split_after:
                continue
            run_seats = tuple(ordered[run_start : index + 1])
            runs.append(
                _PhysicalRun(
                    run_id=f"{zone}\x1f{row}\x1f{run_number}",
                    zone=zone,
                    row=row,
                    seats=run_seats,
                )
            )
            run_number += 1
            run_start = index + 1
    return tuple(runs)


def _aisle_splits(x_values: list[float]) -> set[int]:
    if len(x_values) < 3:
        return set()
    gaps = [
        right - left
        for left, right in zip(x_values, x_values[1:], strict=False)
    ]
    positive_gaps = [gap for gap in gaps if gap > 0]
    if not positive_gaps:
        return set()
    ordinary_pitch = min(median(positive_gaps), min(positive_gaps) * 1.25)
    return {
        index
        for index, gap in enumerate(gaps)
        if gap > ordinary_pitch * 1.75
    }


def _usable_segments(
    run: _PhysicalRun,
) -> dict[int, tuple[str | None, int | None, int | None]]:
    result: dict[int, tuple[str | None, int | None, int | None]] = {}
    current: list[SeatFact] = []
    current_product: int | None = None
    segment_number = 0

    def flush() -> None:
        nonlocal segment_number
        if not current:
            return
        segment_id = f"{run.run_id}\x1f{current_product}\x1f{segment_number}"
        for index, seat in enumerate(current):
            result[seat.id] = (segment_id, index, len(current))
        current.clear()
        segment_number += 1

    for seat in run.seats:
        if not seat.available or seat.product_id is None:
            flush()
            result[seat.id] = (None, None, None)
            current_product = None
            continue
        if current and seat.product_id != current_product:
            flush()
        current_product = seat.product_id
        current.append(seat)
    flush()
    return result


def _row_y(run: _PhysicalRun) -> float:
    return sum(_coordinate(seat.y) for seat in run.seats) / len(run.seats)


def _coordinate(value: float | None) -> float:
    if value is None:
        raise AssertionError("validated physical runs always have coordinates")
    return value
