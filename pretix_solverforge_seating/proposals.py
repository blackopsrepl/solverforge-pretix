from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.db import transaction
from pretix.base.models import Event

from .domain import (
    InfeasiblePlanError,
    PlanningSnapshot,
    SeatBlock,
    StaleProposalError,
    find_exact_block,
    generate_contiguous_blocks,
)
from .extraction import load_planning_snapshot
from .models import PlacementLock, PlannerConfiguration, SeatingProposal
from .planning import (
    SeatPlanningSolution,
    assignment_payload,
    build_solver_plan,
    explain_score,
    position_seat_assignments,
    solve_seating_plan,
    solver_configuration,
)


@dataclass(frozen=True, slots=True)
class ProposalMaterialization:
    snapshot: PlanningSnapshot
    blocks: tuple[SeatBlock, ...]
    plan: SeatPlanningSolution


def generate_proposal(event: Event, user: object | None) -> SeatingProposal:
    configuration, _ = PlannerConfiguration.objects.get_or_create(event=event)
    snapshot = load_planning_snapshot(event, configuration)
    blocks = generate_contiguous_blocks(
        snapshot.seats,
        {party.size for party in snapshot.parties},
        {
            str(zone): int(quality)
            for zone, quality in configuration.zone_quality.items()
        },
    )
    plan = build_solver_plan(
        snapshot.parties,
        blocks,
        random_seed=configuration.random_seed,
    )
    solved = solve_seating_plan(
        plan,
        random_seed=configuration.random_seed,
        step_count_limit=configuration.step_count_limit,
    )
    explanation = explain_score(solved)
    score = solved.score
    assert score is not None
    if explanation["hard_violations"] != 0:
        raise InfeasiblePlanError("The generated proposal contains hard violations.")
    if int(score["levels"][1]) != explanation["soft_score"]:
        raise RuntimeError(
            "SolverForge score explanation does not match the native score."
        )

    current = load_planning_snapshot(event, configuration)
    if current.fingerprint != snapshot.fingerprint:
        raise StaleProposalError(
            "Orders, seats, reservations, answers, configuration, or locks changed "
            "while SolverForge was planning. Generate a fresh proposal."
        )

    config_payload = solver_configuration(
        random_seed=configuration.random_seed,
        step_count_limit=configuration.step_count_limit,
    )
    with transaction.atomic():
        SeatingProposal.objects.filter(
            event=event,
            status=SeatingProposal.Status.PROPOSED,
        ).update(status=SeatingProposal.Status.STALE)
        proposal = SeatingProposal.objects.create(
            event=event,
            status=SeatingProposal.Status.PROPOSED,
            snapshot_fingerprint=snapshot.fingerprint,
            assignments=assignment_payload(solved),
            input_summary=snapshot.summary,
            solver_config=config_payload,
            score=score,
            score_explanation=explanation,
            created_by=user if getattr(user, "pk", None) else None,
        )
        event.log_action(
            "pretix.solverforge_seating.proposal.generated",
            user=user,
            data={
                "proposal": proposal.pk,
                "parties": snapshot.summary["parties"],
                "positions": snapshot.summary["positions"],
                "score": score,
            },
        )
    return proposal


def materialize_proposal(
    event: Event,
    proposal: SeatingProposal,
    snapshot: PlanningSnapshot,
    configuration: PlannerConfiguration,
) -> ProposalMaterialization:
    if proposal.event_id != event.pk:
        raise InfeasiblePlanError("The proposal belongs to a different event.")
    blocks = generate_contiguous_blocks(
        snapshot.seats,
        {party.size for party in snapshot.parties},
        {
            str(zone): int(quality)
            for zone, quality in configuration.zone_quality.items()
        },
    )
    plan = build_solver_plan(
        snapshot.parties,
        blocks,
        random_seed=configuration.random_seed,
        preserve_existing_initially=False,
    )
    payload_by_key = {
        str(assignment["party_key"]): assignment
        for assignment in proposal.assignments
    }
    expected_keys = {party.party_key for party in plan.party_assignments}
    if set(payload_by_key) != expected_keys:
        raise InfeasiblePlanError(
            "The proposal does not contain exactly the current event parties."
        )

    seen_position_ids: set[int] = set()
    seen_seat_guids: set[str] = set()
    for party in plan.party_assignments:
        payload = payload_by_key[party.party_key]
        payload_position_ids = tuple(int(value) for value in payload["position_ids"])
        if payload_position_ids != tuple(party.position_ids):
            raise InfeasiblePlanError(
                f"Proposal party {party.party_key} has a changed position set."
            )
        if seen_position_ids.intersection(payload_position_ids):
            raise InfeasiblePlanError("A ticket position appears in multiple proposal parties.")
        seen_position_ids.update(payload_position_ids)

        seat_guids = tuple(str(value) for value in payload["seat_guids"])
        if seen_seat_guids.intersection(seat_guids):
            raise InfeasiblePlanError("A concrete seat appears in multiple proposal blocks.")
        seen_seat_guids.update(seat_guids)
        block = find_exact_block(blocks, seat_guids)
        if block is None:
            raise InfeasiblePlanError(
                f"Proposal party {party.party_key} no longer maps to a contiguous block."
            )
        party.seat_block_idx = block.idx
        expected_assignments = position_seat_assignments(party)
        expected_seat_ids = tuple(
            seat_id for _position_id, seat_id, _seat_guid in expected_assignments
        )
        expected_seat_guids = tuple(
            seat_guid for _position_id, _seat_id, seat_guid in expected_assignments
        )
        payload_seat_ids = tuple(int(value) for value in payload["seat_ids"])
        payload_wheelchair_ids = tuple(
            int(value) for value in payload.get("wheelchair_position_ids", ())
        )
        if (
            payload_seat_ids != expected_seat_ids
            or seat_guids != expected_seat_guids
            or payload_wheelchair_ids != tuple(party.wheelchair_position_ids)
        ):
            raise InfeasiblePlanError(
                f"Proposal party {party.party_key} has a changed position-to-seat mapping."
            )

    from solverforge import Solver

    score = cast(dict[str, Any], Solver.analyze(plan))
    explanation = explain_score(plan)
    if int(score["levels"][0]) != 0 or explanation["hard_violations"] != 0:
        raise InfeasiblePlanError(
            "The persisted proposal does not have zero hard violations."
        )
    return ProposalMaterialization(snapshot=snapshot, blocks=blocks, plan=plan)


def lock_proposed_placement(
    proposal: SeatingProposal,
    party_key: str,
    user: object | None,
) -> PlacementLock:
    assignment = next(
        (
            value
            for value in proposal.assignments
            if str(value["party_key"]) == party_key
        ),
        None,
    )
    if assignment is None:
        raise InfeasiblePlanError("The selected party is not part of this proposal.")
    with transaction.atomic():
        locked_proposal = SeatingProposal.objects.select_for_update().get(
            pk=proposal.pk
        )
        if locked_proposal.status != SeatingProposal.Status.PROPOSED:
            raise StaleProposalError("Only a current proposal can be locked.")
        placement_lock, _ = PlacementLock.objects.update_or_create(
            event=locked_proposal.event,
            party_key=party_key,
            defaults={
                "position_ids": list(assignment["position_ids"]),
                "seat_guids": list(assignment["seat_guids"]),
                "created_by": user if getattr(user, "pk", None) else None,
            },
        )
        locked_proposal.status = SeatingProposal.Status.STALE
        locked_proposal.save(update_fields=("status",))
        locked_proposal.event.log_action(
            "pretix.solverforge_seating.lock.changed",
            user=user,
            data={
                "proposal": locked_proposal.pk,
                "party_key": party_key,
                "locked": True,
                "seat_guids": list(assignment["seat_guids"]),
            },
        )
    return placement_lock


def lock_existing_placement(
    event: Event,
    party_key: str,
    user: object | None,
) -> PlacementLock:
    configuration, _ = PlannerConfiguration.objects.get_or_create(event=event)
    snapshot = load_planning_snapshot(event, configuration)
    party = next(
        (value for value in snapshot.parties if value.key == party_key),
        None,
    )
    if party is None:
        raise InfeasiblePlanError("The selected party is not part of this event.")
    if len(party.current_seat_guids) != party.size:
        raise InfeasiblePlanError(
            "The existing assignment is incomplete and cannot be locked as a block."
        )
    blocks = generate_contiguous_blocks(
        snapshot.seats,
        {party.size},
        {
            str(zone): int(quality)
            for zone, quality in configuration.zone_quality.items()
        },
    )
    block = find_exact_block(blocks, party.current_seat_guids)
    accessible_guids = (
        {
            guid
            for seat_id, guid in zip(
                block.seat_ids,
                block.seat_guids,
                strict=True,
            )
            if seat_id in set(block.accessible_seat_ids)
        }
        if block is not None
        else set()
    )
    wheelchair_mapping_is_accessible = all(
        position_id not in set(party.wheelchair_position_ids)
        or seat_guid in accessible_guids
        for position_id, seat_guid in zip(
            party.position_ids,
            party.current_seat_guids,
            strict=True,
        )
    )
    if (
        block is None
        or not block.available
        or block.product_id != party.product_id
        or (
            party.requires_wheelchair
            and block.accessible_count < party.wheelchair_count
        )
        or (
            party.requires_companion
            and (party.size < 2 or block.accessible_count < 1)
        )
        or not wheelchair_mapping_is_accessible
    ):
        raise InfeasiblePlanError(
            "The existing assignment is not one valid contiguous, compatible block."
        )
    with transaction.atomic():
        placement_lock, _ = PlacementLock.objects.update_or_create(
            event=event,
            party_key=party.key,
            defaults={
                "position_ids": list(party.position_ids),
                "seat_guids": list(party.current_seat_guids),
                "created_by": user if getattr(user, "pk", None) else None,
            },
        )
        SeatingProposal.objects.filter(
            event=event,
            status=SeatingProposal.Status.PROPOSED,
        ).update(status=SeatingProposal.Status.STALE)
        event.log_action(
            "pretix.solverforge_seating.lock.changed",
            user=user,
            data={
                "party_key": party.key,
                "locked": True,
                "source": "existing_assignment",
                "seat_guids": list(party.current_seat_guids),
            },
        )
    return placement_lock


def unlock_placement(
    event: Event,
    *,
    party_key: str | None = None,
    lock_id: int | None = None,
    user: object | None,
) -> bool:
    if party_key is None and lock_id is None:
        raise ValueError("party_key or lock_id is required")
    with transaction.atomic():
        locks = PlacementLock.objects.select_for_update().filter(event=event)
        locks = locks.filter(pk=lock_id) if lock_id is not None else locks.filter(
            party_key=party_key
        )
        lock = locks.first()
        if lock is None:
            return False
        event.log_action(
            "pretix.solverforge_seating.lock.changed",
            user=user,
            data={
                "party_key": lock.party_key,
                "locked": False,
                "seat_guids": list(lock.seat_guids),
            },
        )
        lock.delete()
        SeatingProposal.objects.filter(
            event=event,
            status=SeatingProposal.Status.PROPOSED,
        ).update(status=SeatingProposal.Status.STALE)
    return True


def discard_proposal(proposal: SeatingProposal, user: object | None) -> None:
    with transaction.atomic():
        locked = SeatingProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status in (
            SeatingProposal.Status.DISCARDED,
            SeatingProposal.Status.COMMITTED,
        ):
            return
        locked.status = SeatingProposal.Status.DISCARDED
        locked.save(update_fields=("status",))
        locked.event.log_action(
            "pretix.solverforge_seating.proposal.discarded",
            user=user,
            data={"proposal": locked.pk},
        )


def seat_map_payload(
    snapshot: PlanningSnapshot,
    proposal: SeatingProposal | None,
) -> list[dict[str, Any]]:
    assignments = proposal.assignments if proposal is not None else []
    committed = bool(
        proposal is not None
        and proposal.status == SeatingProposal.Status.COMMITTED
    )
    proposed_by_guid = {
        str(guid): {
            "party_key": str(assignment["party_key"]),
            "order_code": str(assignment["order_code"]),
            "committed": committed,
        }
        for assignment in assignments
        for guid in assignment["seat_guids"]
    }
    existing_by_guid = {
        guid: party.order_code
        for party in snapshot.parties
        for guid in party.current_seat_guids
    }
    locked_guids = {
        guid
        for party in snapshot.parties
        for guid in party.locked_seat_guids
    }
    return [
        {
            "id": seat.id,
            "guid": seat.guid,
            "zone": seat.zone,
            "row": seat.row,
            "number": seat.seat_number,
            "x": seat.x,
            "y": seat.y,
            "blocked": seat.blocked,
            "unavailable": seat.occupied,
            "existing_order": existing_by_guid.get(seat.guid),
            "proposed": proposed_by_guid.get(seat.guid),
            "locked": seat.guid in locked_guids,
            "accessible": seat.accessible,
            "aisle": seat.aisle,
        }
        for seat in snapshot.seats
    ]
