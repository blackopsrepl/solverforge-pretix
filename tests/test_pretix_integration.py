from __future__ import annotations

from unittest.mock import patch

import pytest
from pretix.base.models import OrderPosition
from pretix.base.services.orders import OrderChangeManager, OrderError

from pretix_solverforge_seating.commit import commit_proposal
from pretix_solverforge_seating.demo import create_demo
from pretix_solverforge_seating.domain import StaleProposalError
from pretix_solverforge_seating.extraction import load_planning_snapshot
from pretix_solverforge_seating.models import (
    PlannerConfiguration,
    SeatingProposal,
)
from pretix_solverforge_seating.proposals import (
    generate_proposal,
    lock_existing_placement,
)


def _fast_proposal(demo: object) -> SeatingProposal:
    configuration = PlannerConfiguration.objects.get(event=demo.event)
    configuration.step_count_limit = 80
    configuration.save(update_fields=("step_count_limit", "updated_at"))
    return generate_proposal(demo.event, demo.user)


@pytest.mark.django_db
def test_demo_uses_real_pretix_seats_orders_answers_blocks_and_locks(demo: object) -> None:
    configuration = PlannerConfiguration.objects.get(event=demo.event)
    snapshot = load_planning_snapshot(demo.event, configuration)

    assert snapshot.summary == {
        "parties": 5,
        "positions": 15,
        "seats": 48,
        "available": 36,
        "blocked": 2,
        "occupied": 10,
        "locked_parties": 1,
        "orphan_locks": 0,
    }
    assert {party.size for party in snapshot.parties} == {1, 2, 3, 4, 5}
    assert any(
        party.requires_wheelchair
        and party.requires_companion
        and party.locked_seat_guids == ("A-01", "A-02")
        for party in snapshot.parties
    )
    assert any("zone:stalls" in party.preferences for party in snapshot.parties)
    assert demo.event.settings.seating_choice is False


@pytest.mark.django_db
def test_nonzero_minimum_seat_distance_is_planned_natively(demo: object) -> None:
    configuration = PlannerConfiguration.objects.get(event=demo.event)
    demo.event.settings.set("seating_minimal_distance", 31)
    demo.event.settings.set("seating_distance_within_row", True)

    snapshot = load_planning_snapshot(demo.event, configuration)
    proposal = generate_proposal(demo.event, demo.user)

    assert snapshot.minimum_seat_distance == 31
    assert snapshot.distance_within_row is True
    assert proposal.score["levels"][0] == 0
    assert (
        proposal.score_explanation["hard"]["minimum_distance_between_parties"]
        == 0
    )


@pytest.mark.django_db
def test_commit_updates_real_order_positions_through_pretix_and_is_idempotent(
    demo: object,
) -> None:
    proposal = _fast_proposal(demo)
    expected = {
        int(position_id): int(seat_id)
        for assignment in proposal.assignments
        for position_id, seat_id in zip(
            assignment["position_ids"],
            assignment["seat_ids"],
            strict=True,
        )
    }
    before = dict(
        OrderPosition.objects.filter(pk__in=expected).values_list("pk", "seat_id")
    )
    assert before != expected
    service_calls: list[tuple[int, int | None]] = []
    original_change_seat = OrderChangeManager.change_seat

    def record_change_seat(
        manager: OrderChangeManager,
        position: OrderPosition,
        seat: object | None,
    ) -> object:
        service_calls.append((position.pk, getattr(seat, "pk", None)))
        return original_change_seat(manager, position, seat)

    with patch.object(
        OrderChangeManager,
        "change_seat",
        new=record_change_seat,
    ):
        first = commit_proposal(proposal.pk, event=demo.event, user=demo.user)
    actual = dict(
        OrderPosition.objects.filter(pk__in=expected).values_list("pk", "seat_id")
    )
    second = commit_proposal(proposal.pk, event=demo.event, user=demo.user)
    expected_changed = {
        position_id: seat_id
        for position_id, seat_id in expected.items()
        if before[position_id] != seat_id
    }
    attached_through_service = {
        position_id: seat_id
        for position_id, seat_id in service_calls
        if seat_id is not None
    }

    assert first.changed_positions > 0
    assert first.already_committed is False
    assert attached_through_service == expected_changed
    assert actual == expected
    assert second.changed_positions == 0
    assert second.already_committed is True
    proposal.refresh_from_db()
    assert proposal.status == SeatingProposal.Status.COMMITTED


@pytest.mark.django_db
def test_demo_rerun_does_not_reset_the_existing_demo_user(demo: object) -> None:
    demo.user.set_password("locally-changed-password")
    demo.user.save(update_fields=("password",))

    repeated = create_demo(password="replacement-password")

    demo.user.refresh_from_db()
    assert repeated.created is False
    assert repeated.event == demo.event
    assert demo.user.check_password("locally-changed-password")


@pytest.mark.django_db
def test_commit_rejects_and_marks_a_stale_proposal(demo: object) -> None:
    proposal = _fast_proposal(demo)
    changed_seat = demo.event.seats.filter(blocked=False).order_by("-pk").first()
    changed_seat.blocked = True
    changed_seat.save(update_fields=("blocked",))

    with pytest.raises(StaleProposalError, match="changed"):
        commit_proposal(proposal.pk, event=demo.event, user=demo.user)

    proposal.refresh_from_db()
    assert proposal.status == SeatingProposal.Status.STALE


@pytest.mark.django_db
def test_existing_assignment_can_be_selected_as_a_hard_lock(demo: object) -> None:
    configuration = PlannerConfiguration.objects.get(event=demo.event)
    snapshot = load_planning_snapshot(demo.event, configuration)
    premium_party = next(
        party for party in snapshot.parties if party.order_code == "SF001"
    )

    lock = lock_existing_placement(demo.event, premium_party.key, demo.user)
    refreshed = load_planning_snapshot(demo.event, configuration)
    refreshed_party = next(
        party for party in refreshed.parties if party.order_code == "SF001"
    )

    assert lock.seat_guids == ["C-12"]
    assert refreshed_party.locked_seat_guids == ("C-12",)


@pytest.mark.django_db(transaction=True)
def test_assignment_failure_rolls_back_every_seat_change(demo: object) -> None:
    proposal = _fast_proposal(demo)
    position_ids = {
        int(position_id)
        for assignment in proposal.assignments
        for position_id in assignment["position_ids"]
    }
    before = dict(
        OrderPosition.objects.filter(pk__in=position_ids).values_list("pk", "seat_id")
    )
    original_commit = OrderChangeManager.commit
    call_count = 0

    def fail_during_second_order(manager: OrderChangeManager) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OrderError("deterministic injected assignment failure")
        return original_commit(manager)

    with patch.object(OrderChangeManager, "commit", new=fail_during_second_order):
        with pytest.raises(OrderError, match="injected assignment failure"):
            commit_proposal(proposal.pk, event=demo.event, user=demo.user)

    after = dict(
        OrderPosition.objects.filter(pk__in=position_ids).values_list("pk", "seat_id")
    )
    proposal.refresh_from_db()
    assert call_count == 2
    assert after == before
    assert proposal.status == SeatingProposal.Status.PROPOSED
