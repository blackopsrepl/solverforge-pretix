from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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


def convert_demo_to_series(demo: Any) -> Any:
    """Move the deterministic demo into one real pretix subevent."""

    from pretix.base.models import Order, OrderPosition, SubEvent
    from pretix.base.services.orders import OrderChangeManager
    from pretix.base.services.seating import generate_seats

    from pretix_solverforge_seating.extraction import load_planning_snapshot
    from pretix_solverforge_seating.models import (
        PlacementLock,
        PlannerConfiguration,
    )

    event = demo.event
    plan = event.seating_plan
    assert plan is not None
    assigned_guids = {
        position.pk: position.seat.seat_guid
        for position in OrderPosition.objects.filter(
            order__event=event,
            seat__isnull=False,
        ).select_related("seat")
    }
    products_by_guid = {
        seat.seat_guid: seat.product
        for seat in event.seats.filter(subevent=None).select_related("product")
    }
    category_products = {
        plan_seat.category: products_by_guid[plan_seat.guid]
        for plan_seat in plan.iter_all_seats()
    }

    for order in event.orders.prefetch_related("positions").order_by("pk"):
        assigned = [
            position for position in order.positions.all() if position.seat_id
        ]
        if not assigned:
            continue
        manager = OrderChangeManager(
            order,
            user=demo.user,
            notify=False,
            reissue_invoice=False,
        )
        for position in assigned:
            manager.change_seat(position, None)
        manager.commit()

    PlacementLock.objects.filter(event=event).delete()
    event.seat_category_mappings.filter(subevent=None).delete()
    event.seats.filter(subevent=None).delete()
    event.has_subevents = True
    event.seating_plan = None
    event.save(update_fields=("has_subevents", "seating_plan"))
    subevent = SubEvent.objects.create(
        event=event,
        name="Opening night",
        date_from=event.date_from,
        date_to=event.date_to,
        active=True,
        is_public=True,
        seating_plan=plan,
    )
    event.quotas.update(subevent=subevent)
    generate_seats(
        event,
        subevent,
        plan,
        category_products,
        blocked_guids={"B-04", "D-08"},
    )

    positions = list(
        OrderPosition.objects.filter(order__event=event)
        .select_related("order")
        .order_by("order_id", "positionid", "pk")
    )
    for position in positions:
        position.subevent = subevent
        position.save(update_fields=("subevent",))
    seats_by_guid = {
        seat.seat_guid: seat
        for seat in subevent.seats.order_by("pk")
    }
    for order_id in sorted({position.order_id for position in positions}):
        order_positions = list(
            OrderPosition.objects.filter(
                order_id=order_id,
                subevent=subevent,
            ).order_by("positionid", "pk")
        )
        targets = [
            (position, assigned_guids[position.pk])
            for position in order_positions
            if position.pk in assigned_guids
        ]
        if not targets:
            continue
        manager = OrderChangeManager(
            Order.objects.get(pk=order_id),
            user=demo.user,
            notify=False,
            reissue_invoice=False,
        )
        for position, guid in targets:
            manager.change_seat(position, seats_by_guid[guid])
        manager.commit()

    configuration = PlannerConfiguration.objects.get(event=event)
    snapshot = load_planning_snapshot(
        event,
        configuration,
        subevent=subevent,
    )
    wheelchair_party = next(
        party for party in snapshot.parties if party.order_code == "SF002"
    )
    PlacementLock.objects.create(
        event=event,
        subevent=subevent,
        party_key=wheelchair_party.key,
        position_ids=list(wheelchair_party.position_ids),
        seat_guids=["A-01", "A-02"],
        created_by=demo.user,
    )
    return subevent
