from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.utils.timezone import now
from pretix.base.models import Event, Order, OrderPosition, Seat
from pretix.base.services.orders import OrderChangeManager

from .domain import CommitError, StaleProposalError
from .extraction import load_planning_snapshot
from .models import PlacementLock, PlannerConfiguration, SeatingProposal
from .planning import SeatPlanningSolution, position_seat_assignments
from .proposals import materialize_proposal


@dataclass(frozen=True, slots=True)
class CommitResult:
    changed_positions: int
    already_committed: bool


def commit_proposal(
    proposal_id: int,
    *,
    event: Event,
    user: object | None,
) -> CommitResult:
    try:
        return _commit_proposal_atomic(proposal_id, event=event, user=user)
    except StaleProposalError:
        SeatingProposal.objects.filter(
            pk=proposal_id,
            status=SeatingProposal.Status.PROPOSED,
        ).update(status=SeatingProposal.Status.STALE)
        raise


@transaction.atomic
def _commit_proposal_atomic(
    proposal_id: int,
    *,
    event: Event,
    user: object | None,
) -> CommitResult:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    proposal = (
        SeatingProposal.objects.select_for_update()
        .select_related("event")
        .get(pk=proposal_id, event=locked_event)
    )
    if proposal.status == SeatingProposal.Status.COMMITTED:
        _verify_committed_assignments(proposal, locked_event)
        return CommitResult(changed_positions=0, already_committed=True)
    if proposal.status != SeatingProposal.Status.PROPOSED:
        raise CommitError(
            f"A {proposal.get_status_display().lower()} proposal cannot be committed."
        )

    configuration = PlannerConfiguration.objects.select_for_update().get(
        event=locked_event
    )
    list(
        PlacementLock.objects.select_for_update()
        .filter(event=locked_event)
        .values_list("pk", flat=True)
    )
    position_ids, seat_ids, order_ids = _proposal_ids(proposal)
    orders = {
        order.pk: order
        for order in Order.objects.select_for_update()
        .filter(pk__in=order_ids, event=locked_event)
        .order_by("pk")
    }
    positions = {
        position.pk: position
        for position in OrderPosition.objects.select_for_update()
        .select_related("order", "seat")
        .filter(pk__in=position_ids, order__event=locked_event)
        .order_by("order_id", "positionid", "pk")
    }
    seats = {
        seat.pk: seat
        for seat in Seat.objects.select_for_update()
        .filter(pk__in=seat_ids, event=locked_event)
        .order_by("pk")
    }
    if (
        len(positions) != len(position_ids)
        or len(seats) != len(seat_ids)
        or len(orders) != len(order_ids)
    ):
        raise StaleProposalError(
            "A proposed order position, order, or seat no longer exists."
        )

    snapshot = load_planning_snapshot(locked_event, configuration)
    if snapshot.fingerprint != proposal.snapshot_fingerprint:
        raise StaleProposalError(
            "Orders, seats, reservations, answers, configuration, or locks changed "
            "after this proposal was generated. Replan before committing."
        )
    materialized = materialize_proposal(
        locked_event,
        proposal,
        snapshot,
        configuration,
    )
    target_by_position = _target_seats(materialized.plan, seats)
    changed_positions = [
        positions[position_id]
        for position_id, target in target_by_position.items()
        if positions[position_id].seat_id != target.pk
    ]

    _detach_changed_positions(changed_positions, orders, user)
    _attach_target_positions(
        changed_positions,
        target_by_position,
        orders,
        user,
    )
    _verify_target_mapping(positions, target_by_position)

    proposal.status = SeatingProposal.Status.COMMITTED
    proposal.committed_at = now()
    proposal.save(update_fields=("status", "committed_at"))
    locked_event.log_action(
        "pretix.solverforge_seating.proposal.committed",
        user=user,
        data={
            "proposal": proposal.pk,
            "changed_positions": len(changed_positions),
            "score": proposal.score,
        },
    )
    return CommitResult(
        changed_positions=len(changed_positions),
        already_committed=False,
    )


def _proposal_ids(
    proposal: SeatingProposal,
) -> tuple[set[int], set[int], set[int]]:
    position_ids = {
        int(position_id)
        for assignment in proposal.assignments
        for position_id in assignment["position_ids"]
    }
    seat_ids = {
        int(seat_id)
        for assignment in proposal.assignments
        for seat_id in assignment["seat_ids"]
    }
    order_ids = {
        int(assignment["order_id"]) for assignment in proposal.assignments
    }
    return position_ids, seat_ids, order_ids


def _target_seats(
    plan: SeatPlanningSolution,
    seats: dict[int, Seat],
) -> dict[int, Seat]:
    target_by_position: dict[int, Seat] = {}
    for party in plan.party_assignments:
        for position_id, seat_id, _seat_guid in position_seat_assignments(party):
            target_by_position[int(position_id)] = seats[int(seat_id)]
    return target_by_position


def _detach_changed_positions(
    positions: list[OrderPosition],
    orders: dict[int, Order],
    user: object | None,
) -> None:
    by_order: dict[int, list[OrderPosition]] = defaultdict(list)
    for position in positions:
        if position.seat_id is not None:
            by_order[position.order_id].append(position)
    for order_id in sorted(by_order):
        manager = OrderChangeManager(
            orders[order_id],
            user=user,
            notify=False,
            reissue_invoice=False,
        )
        for position in by_order[order_id]:
            manager.change_seat(position, None)
        manager.commit()


def _attach_target_positions(
    positions: list[OrderPosition],
    targets: dict[int, Seat],
    orders: dict[int, Order],
    user: object | None,
) -> None:
    by_order: dict[int, list[OrderPosition]] = defaultdict(list)
    for position in positions:
        by_order[position.order_id].append(position)
    for order_id in sorted(by_order):
        manager = OrderChangeManager(
            orders[order_id],
            user=user,
            notify=False,
            reissue_invoice=False,
        )
        for position in by_order[order_id]:
            manager.change_seat(position, targets[position.pk])
        manager.commit()


def _verify_target_mapping(
    positions: dict[int, OrderPosition],
    targets: dict[int, Seat],
) -> None:
    refreshed = {
        position.pk: position.seat_id
        for position in OrderPosition.objects.filter(pk__in=positions).only(
            "pk",
            "seat_id",
        )
    }
    expected = {
        position_id: seat.pk for position_id, seat in targets.items()
    }
    if refreshed != expected:
        raise CommitError(
            "pretix did not persist the complete proposed seat mapping; "
            "the transaction was rolled back."
        )


def _verify_committed_assignments(
    proposal: SeatingProposal,
    event: Event,
) -> None:
    expected = {
        int(position_id): int(seat_id)
        for assignment in proposal.assignments
        for position_id, seat_id in zip(
            assignment["position_ids"],
            assignment["seat_ids"],
            strict=True,
        )
    }
    actual = dict(
        OrderPosition.objects.filter(
            pk__in=expected,
            order__event=event,
        ).values_list("pk", "seat_id")
    )
    if actual != expected:
        raise CommitError(
            "This proposal is marked committed, but pretix no longer contains "
            "the committed seat mapping."
        )
