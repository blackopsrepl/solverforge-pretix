from __future__ import annotations

from collections.abc import Iterable

from pretix_solverforge_seating.domain import PartySpec, SeatFact


def row_seats(
    *,
    count: int = 8,
    row: str = "A",
    zone: str = "Main",
    product_id: int = 1,
    blocked: Iterable[int] = (),
    occupied: Iterable[int] = (),
    accessible: Iterable[int] = (),
    aisle: Iterable[int] = (),
    y: float = 1,
    id_offset: int = 0,
) -> tuple[SeatFact, ...]:
    blocked_indices = set(blocked)
    occupied_indices = set(occupied)
    accessible_indices = set(accessible)
    aisle_indices = set(aisle)
    return tuple(
        SeatFact(
            id=id_offset + index,
            guid=f"{row}-{index}",
            zone=zone,
            row=row,
            seat_number=str(index),
            product_id=product_id,
            blocked=index in blocked_indices,
            occupied=index in occupied_indices,
            x=float(index - 1),
            y=y,
            sorting_rank=id_offset + index,
            accessible=index in accessible_indices,
            aisle=index in aisle_indices,
        )
        for index in range(1, count + 1)
    )


def party(
    key: str,
    *,
    size: int,
    product_id: int = 1,
    order_id: int = 1,
    preferences: tuple[str, ...] = (),
    wheelchair: bool = False,
    companion: bool = False,
    current: tuple[str, ...] = (),
    locked: tuple[str, ...] = (),
) -> PartySpec:
    position_ids = tuple(range(order_id * 100, order_id * 100 + size))
    return PartySpec(
        key=key,
        order_id=order_id,
        order_code=key.upper(),
        position_ids=position_ids,
        product_id=product_id,
        product_name=f"Product {product_id}",
        preferences=preferences,
        wheelchair_position_ids=position_ids[:1] if wheelchair else (),
        requires_companion=companion,
        current_seat_guids=current,
        locked_seat_guids=locked,
    )
