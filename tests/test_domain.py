from __future__ import annotations

import pytest

from pretix_solverforge_seating.domain import (
    SeatFact,
    UnsupportedLayoutError,
    find_exact_block,
    generate_contiguous_blocks,
    with_distance_conflicts,
)

from .helpers import row_seats


def test_contiguous_blocks_cover_every_window_and_split_at_aisles() -> None:
    seats = list(row_seats(count=8))
    seats[6] = SeatFact(
        **{
            field: getattr(seats[6], field)
            for field in SeatFact.__dataclass_fields__
            if field != "x"
        },
        x=10.0,
    )
    seats[7] = SeatFact(
        **{
            field: getattr(seats[7], field)
            for field in SeatFact.__dataclass_fields__
            if field != "x"
        },
        x=11.0,
    )

    blocks = generate_contiguous_blocks(seats, {2, 3})

    pairs = {block.seat_guids for block in blocks if block.size == 2}
    triples = {block.seat_guids for block in blocks if block.size == 3}
    assert ("A-6", "A-7") not in pairs
    assert ("A-5", "A-6", "A-7") not in triples
    assert ("A-1", "A-2") in pairs
    assert ("A-7", "A-8") in pairs
    assert all(block.row == "A" and block.zone == "Main" for block in blocks)


def test_blocks_preserve_party_size_product_and_availability_facts() -> None:
    seats = (
        *row_seats(count=3, product_id=1, blocked={2}),
        *row_seats(
            count=3,
            row="B",
            product_id=2,
            occupied={3},
            y=2,
            id_offset=10,
        ),
    )

    blocks = generate_contiguous_blocks(seats, {1, 2, 3})

    assert {block.size for block in blocks} == {1, 2, 3}
    assert all(len(set(block.product_ids)) == 1 for block in blocks)
    assert find_exact_block(blocks, ("A-1", "A-2")).blocked_count == 1
    assert find_exact_block(blocks, ("B-2", "B-3")).occupied_count == 1


def test_distance_conflicts_match_pretix_order_and_row_semantics() -> None:
    seats = (
        *row_seats(count=3),
        *row_seats(count=3, row="B", y=1.5, id_offset=10),
    )

    all_rows = with_distance_conflicts(
        seats,
        occupied_order_ids_by_seat={2: {9}},
        reserved_seat_ids={3},
        minimum_distance=1.2,
        within_row=False,
    )
    within_rows = with_distance_conflicts(
        seats,
        occupied_order_ids_by_seat={2: {9}},
        reserved_seat_ids={3},
        minimum_distance=1.2,
        within_row=True,
    )

    assert all_rows[0].distance_conflicting_order_ids == (9,)
    assert all_rows[1].distance_conflicting_reservation is True
    assert all_rows[3].distance_conflicting_order_ids == (9,)
    assert within_rows[3].distance_conflicting_order_ids == ()


@pytest.mark.parametrize(
    "seats, message",
    [
        (
            (
                SeatFact(
                    id=1,
                    guid="free",
                    zone="Main",
                    row="",
                    seat_number="1",
                    product_id=1,
                    blocked=False,
                    occupied=False,
                    x=0,
                    y=0,
                    sorting_rank=1,
                ),
            ),
            "no row identifier",
        ),
        (
            (
                SeatFact(
                    id=1,
                    guid="table-1",
                    zone="Tables",
                    row="T1",
                    seat_number="1",
                    product_id=1,
                    blocked=False,
                    occupied=False,
                    x=0,
                    y=0,
                    sorting_rank=1,
                ),
                SeatFact(
                    id=2,
                    guid="table-2",
                    zone="Tables",
                    row="T1",
                    seat_number="2",
                    product_id=1,
                    blocked=False,
                    occupied=False,
                    x=1,
                    y=2,
                    sorting_rank=2,
                ),
            ),
            "not horizontal",
        ),
    ],
)
def test_unsupported_arbitrary_layouts_are_rejected(
    seats: tuple[SeatFact, ...],
    message: str,
) -> None:
    with pytest.raises(UnsupportedLayoutError, match=message):
        generate_contiguous_blocks(seats, {1})
