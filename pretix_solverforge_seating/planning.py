from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any, cast

import solverforge._native as native_solverforge
from solverforge import (
    ConstraintFactory,
    HardSoftScore,
    Solver,
    constraint_provider,
    planning_entity,
    planning_id,
    planning_solution,
    planning_variable,
    problem_fact,
)

from .domain import InfeasiblePlanError, PartySpec, SeatBlock, find_exact_block

NATIVE_MODULE_NAME = native_solverforge.__name__
CHANGE_PENALTY = 20
AISLE_PREFERENCE_PENALTY = 12
ZONE_PREFERENCE_PENALTY = 20
ORPHAN_SEAT_PENALTY = 8

problem_fact(SeatBlock)


def _candidate_blocks(party: Any) -> list[int]:
    return list(party.candidate_block_indices)


@planning_entity
class PartyAssignment:
    party_key = planning_id()
    seat_block_idx = planning_variable(
        value_range_provider="seat_block_indices",
        candidate_values=_candidate_blocks,
    )

    def __init__(
        self,
        spec: PartySpec,
        blocks: tuple[SeatBlock, ...],
        candidate_block_indices: list[int],
        minimum_party_size_for_product: int,
        seat_block_idx: int | None = None,
    ) -> None:
        self.party_key = spec.key
        self.order_id = spec.order_id
        self.order_code = spec.order_code
        self.position_ids = spec.position_ids
        self.product_id = spec.product_id
        self.product_name = spec.product_name
        self.party_size = spec.size
        self.preferences = spec.preferences
        self.wheelchair_position_ids = spec.wheelchair_position_ids
        self.requires_companion = spec.requires_companion
        self.current_seat_guids = spec.current_seat_guids
        self.locked_seat_guids = spec.locked_seat_guids
        self.minimum_party_size_for_product = minimum_party_size_for_product
        self.blocks = blocks
        self.candidate_block_indices = candidate_block_indices
        self.seat_block_idx = seat_block_idx

    @property
    def selected_block(self) -> SeatBlock | None:
        value = self.seat_block_idx
        if not isinstance(value, int) or value < 0 or value >= len(self.blocks):
            return None
        return self.blocks[value]

    @property
    def requires_wheelchair(self) -> bool:
        return bool(self.wheelchair_position_ids)

    @property
    def wheelchair_count(self) -> int:
        return len(self.wheelchair_position_ids)


def _missing_block(party: PartyAssignment) -> bool:
    return party.selected_block is None


def _wrong_size(party: PartyAssignment) -> bool:
    block = party.selected_block
    return block is not None and block.size != party.party_size


def _size_violation_weight(party: PartyAssignment) -> HardSoftScore:
    block = party.selected_block
    if block is None:
        return HardSoftScore.ZERO
    return HardSoftScore.of_hard(abs(block.size - party.party_size))


def _wrong_product(party: PartyAssignment) -> bool:
    block = party.selected_block
    return block is not None and block.product_id != party.product_id


def _unavailable(party: PartyAssignment) -> bool:
    block = party.selected_block
    return block is not None and not block.available


def _unavailable_weight(party: PartyAssignment) -> HardSoftScore:
    block = party.selected_block
    if block is None:
        return HardSoftScore.ZERO
    return HardSoftScore.of_hard(block.blocked_count + block.occupied_count)


def _wheelchair_violation(party: PartyAssignment) -> bool:
    block = party.selected_block
    return (
        block is not None
        and party.requires_wheelchair
        and block.accessible_count < party.wheelchair_count
    )


def _companion_violation(party: PartyAssignment) -> bool:
    block = party.selected_block
    return (
        block is not None
        and party.requires_companion
        and (party.party_size < 2 or block.accessible_count < 1)
    )


def _lock_violation(party: PartyAssignment) -> bool:
    block = party.selected_block
    return (
        block is not None
        and bool(party.locked_seat_guids)
        and frozenset(block.seat_guids) != frozenset(party.locked_seat_guids)
    )


def _overlap(left: PartyAssignment, right: PartyAssignment) -> bool:
    left_block = left.selected_block
    right_block = right.selected_block
    return (
        left.party_key < right.party_key
        and left_block is not None
        and right_block is not None
        and not frozenset(left_block.seat_ids).isdisjoint(right_block.seat_ids)
    )


def _overlap_weight(
    left: PartyAssignment,
    right: PartyAssignment,
) -> HardSoftScore:
    left_block = left.selected_block
    right_block = right.selected_block
    if left_block is None or right_block is None:
        return HardSoftScore.ZERO
    count = len(frozenset(left_block.seat_ids).intersection(right_block.seat_ids))
    return HardSoftScore.of_hard(count)


def _soft_eligible(party: PartyAssignment) -> bool:
    return not (
        _missing_block(party)
        or _wrong_size(party)
        or _wrong_product(party)
        or _unavailable(party)
        or _wheelchair_violation(party)
        or _companion_violation(party)
        or _lock_violation(party)
    )


def preference_penalty(party: PartyAssignment) -> int:
    block = party.selected_block
    if block is None:
        return 0
    penalty = 0
    for preference in party.preferences:
        normalized = preference.casefold()
        if normalized == "front":
            penalty += block.front_rank * 4
        elif normalized == "rear":
            penalty += block.rear_rank * 4
        elif normalized == "aisle" and block.aisle_count == 0:
            penalty += AISLE_PREFERENCE_PENALTY
        elif normalized.startswith("zone:"):
            preferred_zone = preference.split(":", 1)[1].strip()
            if _zone_slug(block.zone) != _zone_slug(preferred_zone):
                penalty += ZONE_PREFERENCE_PENALTY
    return penalty


def _has_preference_penalty(party: PartyAssignment) -> bool:
    return _soft_eligible(party) and preference_penalty(party) > 0


def _preference_weight(party: PartyAssignment) -> HardSoftScore:
    return HardSoftScore.of_soft(preference_penalty(party))


def _has_quality_reward(party: PartyAssignment) -> bool:
    block = party.selected_block
    return _soft_eligible(party) and block is not None and block.quality > 0


def _quality_weight(party: PartyAssignment) -> HardSoftScore:
    block = party.selected_block
    return HardSoftScore.of_soft(block.quality if block is not None else 0)


def _changed_existing_assignment(party: PartyAssignment) -> bool:
    block = party.selected_block
    return (
        _soft_eligible(party)
        and block is not None
        and bool(party.current_seat_guids)
        and frozenset(block.seat_guids) != frozenset(party.current_seat_guids)
    )


def _change_weight(_party: PartyAssignment) -> HardSoftScore:
    return HardSoftScore.of_soft(CHANGE_PENALTY)


def boundary_orphan_count(party: PartyAssignment) -> int:
    block = party.selected_block
    if (
        block is None
        or party.minimum_party_size_for_product <= 1
        or block.usable_segment_id is None
        or block.segment_start is None
        or block.segment_end is None
        or block.segment_length is None
    ):
        return 0
    return int(block.segment_start == 1) + int(
        block.segment_end == block.segment_length - 2
    )


def _has_boundary_orphan(party: PartyAssignment) -> bool:
    return _soft_eligible(party) and boundary_orphan_count(party) > 0


def _boundary_orphan_weight(party: PartyAssignment) -> HardSoftScore:
    return HardSoftScore.of_soft(boundary_orphan_count(party) * ORPHAN_SEAT_PENALTY)


def _interior_orphan(left: PartyAssignment, right: PartyAssignment) -> bool:
    left_block = left.selected_block
    right_block = right.selected_block
    if (
        left.party_key >= right.party_key
        or left.product_id != right.product_id
        or left.minimum_party_size_for_product <= 1
        or left_block is None
        or right_block is None
        or left_block.usable_segment_id is None
        or left_block.usable_segment_id != right_block.usable_segment_id
        or left_block.segment_start is None
        or left_block.segment_end is None
        or right_block.segment_start is None
        or right_block.segment_end is None
    ):
        return False
    left_start = left_block.segment_start
    right_start = right_block.segment_start
    first, second = (
        (left_block, right_block)
        if left_start <= right_start
        else (right_block, left_block)
    )
    return cast(int, second.segment_start) - cast(int, first.segment_end) == 2


def _interior_orphan_weight(
    _left: PartyAssignment,
    _right: PartyAssignment,
) -> HardSoftScore:
    return HardSoftScore.of_soft(ORPHAN_SEAT_PENALTY)


def satisfaction(party: PartyAssignment) -> int:
    block = party.selected_block
    if block is None:
        return 0
    return block.quality - preference_penalty(party)


def fairness_penalty(left: PartyAssignment, right: PartyAssignment) -> int:
    return max(0, abs(satisfaction(left) - satisfaction(right)) - 10) // 5


def _unfair_pair(left: PartyAssignment, right: PartyAssignment) -> bool:
    return (
        left.party_key < right.party_key
        and left.product_id == right.product_id
        and _soft_eligible(left)
        and _soft_eligible(right)
        and fairness_penalty(left, right) > 0
    )


def _fairness_weight(
    left: PartyAssignment,
    right: PartyAssignment,
) -> HardSoftScore:
    return HardSoftScore.of_soft(fairness_penalty(left, right))


@constraint_provider
def seating_constraints(factory: ConstraintFactory) -> list[object]:
    parties = factory.for_each(PartyAssignment)
    return [
        parties.filter(_missing_block)
        .penalize(HardSoftScore.ONE_HARD)
        .named("party has a seat block"),
        factory.for_each(PartyAssignment)
        .filter(_wrong_size)
        .penalize(_size_violation_weight)
        .named("party size matches block size"),
        factory.for_each(PartyAssignment)
        .filter(_wrong_product)
        .penalize(HardSoftScore.ONE_HARD)
        .named("ticket product matches every seat"),
        factory.for_each(PartyAssignment)
        .filter(_unavailable)
        .penalize(_unavailable_weight)
        .named("block excludes blocked or occupied seats"),
        factory.for_each(PartyAssignment)
        .filter(_wheelchair_violation)
        .penalize(HardSoftScore.ONE_HARD)
        .named("wheelchair party receives an accessible seat"),
        factory.for_each(PartyAssignment)
        .filter(_companion_violation)
        .penalize(HardSoftScore.ONE_HARD)
        .named("accessibility companion remains adjacent"),
        factory.for_each(PartyAssignment)
        .filter(_lock_violation)
        .penalize(HardSoftScore.ONE_HARD)
        .named("organizer lock remains fixed"),
        factory.for_each(PartyAssignment)
        .join(PartyAssignment)
        .filter(_overlap)
        .penalize(_overlap_weight)
        .named("selected blocks do not overlap"),
        factory.for_each(PartyAssignment)
        .filter(_has_preference_penalty)
        .penalize(_preference_weight)
        .named("front rear aisle and zone preferences"),
        factory.for_each(PartyAssignment)
        .filter(_has_quality_reward)
        .reward(_quality_weight)
        .named("configured zone quality"),
        factory.for_each(PartyAssignment)
        .filter(_changed_existing_assignment)
        .penalize(_change_weight)
        .named("minimize unlocked assignment changes"),
        factory.for_each(PartyAssignment)
        .filter(_has_boundary_orphan)
        .penalize(_boundary_orphan_weight)
        .named("avoid unusable boundary single seats"),
        factory.for_each(PartyAssignment)
        .join(PartyAssignment)
        .filter(_interior_orphan)
        .penalize(_interior_orphan_weight)
        .named("avoid unusable interior single seats"),
        factory.for_each(PartyAssignment)
        .join(PartyAssignment)
        .filter(_unfair_pair)
        .penalize(_fairness_weight)
        .named("balance seat satisfaction fairly"),
    ]


@planning_solution(score=HardSoftScore, constraints=seating_constraints)
class SeatPlanningSolution:
    party_assignments: list[PartyAssignment]
    seat_blocks: list[SeatBlock]

    def __init__(
        self,
        party_assignments: list[PartyAssignment],
        seat_blocks: tuple[SeatBlock, ...],
    ) -> None:
        self.party_assignments = party_assignments
        self.seat_blocks = list(seat_blocks)
        self.seat_block_indices = [block.idx for block in seat_blocks]
        self.score: dict[str, Any] | None = None


def build_solver_plan(
    parties: Iterable[PartySpec],
    blocks: tuple[SeatBlock, ...],
    *,
    random_seed: int,
    preserve_existing_initially: bool = True,
) -> SeatPlanningSolution:
    specs = tuple(parties)
    minimum_sizes: dict[int, int] = {}
    for spec in specs:
        minimum_sizes[spec.product_id] = min(
            minimum_sizes.get(spec.product_id, spec.size),
            spec.size,
        )

    assignments: list[PartyAssignment] = []
    for spec in specs:
        if (
            len(set(spec.wheelchair_position_ids))
            != len(spec.wheelchair_position_ids)
            or not set(spec.wheelchair_position_ids).issubset(spec.position_ids)
        ):
            raise InfeasiblePlanError(
                f"Party {spec.order_code} contains an invalid wheelchair-position mapping."
            )
        candidates = [
            block.idx
            for block in blocks
            if _block_is_candidate(spec, block)
        ]
        if not candidates:
            requirement = []
            if spec.requires_wheelchair:
                requirement.append("wheelchair-accessible")
            if spec.requires_companion:
                requirement.append("companion-adjacent")
            qualifier = f" ({', '.join(requirement)})" if requirement else ""
            raise InfeasiblePlanError(
                f'Party {spec.order_code} / {spec.product_name} needs {spec.size} '
                f"contiguous compatible seats{qualifier}, but no candidate block exists."
            )

        initial: int | None = None
        if spec.locked_seat_guids:
            locked_block = find_exact_block(blocks, spec.locked_seat_guids)
            if locked_block is None or locked_block.idx not in candidates:
                raise InfeasiblePlanError(
                    f'Locked party {spec.order_code} no longer maps to one valid contiguous block.'
                )
            initial = locked_block.idx
            candidates = [locked_block.idx]
        elif preserve_existing_initially and spec.current_seat_guids:
            current_block = find_exact_block(blocks, spec.current_seat_guids)
            if current_block is not None and current_block.idx in candidates:
                initial = current_block.idx

        assignments.append(
            PartyAssignment(
                spec,
                blocks,
                candidates,
                minimum_sizes[spec.product_id],
                initial,
            )
        )

    assignments.sort(
        key=lambda party: hashlib.sha256(
            f"{random_seed}:{party.party_key}".encode()
        ).digest()
    )
    return SeatPlanningSolution(assignments, blocks)


def solver_configuration(
    *,
    random_seed: int,
    step_count_limit: int,
    include_construction: bool = True,
) -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    if include_construction:
        phases.append(
            {
                "type": "construction_heuristic",
                "construction_heuristic_type": "cheapest_insertion",
                "construction_obligation": "assign_when_candidate_exists",
            }
        )
    phases.append(
        {
            "type": "local_search",
            "local_search_type": "acceptor_forager",
            "move_selector": {
                "type": "union_move_selector",
                "selection_order": "sequential",
                "selectors": [
                    {
                        "type": "change_move_selector",
                        "selection_order": "original",
                        "entity_class": "PartyAssignment",
                        "variable_name": "seat_block_idx",
                    },
                    {
                        "type": "swap_move_selector",
                        "selection_order": "original",
                        "entity_class": "PartyAssignment",
                        "variable_name": "seat_block_idx",
                    },
                ],
            },
            "acceptor": {"type": "hill_climbing"},
            "forager": {"type": "best_score"},
            "score_tie_break": "first",
            "termination": {"step_count_limit": step_count_limit},
        }
    )
    return {"random_seed": random_seed, "phases": phases}


def solve_seating_plan(
    plan: SeatPlanningSolution,
    *,
    random_seed: int,
    step_count_limit: int,
    include_construction: bool = True,
) -> SeatPlanningSolution:
    config = solver_configuration(
        random_seed=random_seed,
        step_count_limit=step_count_limit,
        include_construction=include_construction,
    )
    solved = Solver.solve(plan, config)
    if not isinstance(solved, SeatPlanningSolution):
        raise RuntimeError("SolverForge returned an unexpected planning solution type.")
    analyzed = cast(dict[str, Any], Solver.analyze(solved))
    if solved.score != analyzed:
        raise RuntimeError("SolverForge solve/analyze score mismatch.")
    if int(analyzed["levels"][0]) != 0:
        explanation = explain_score(solved)
        raise InfeasiblePlanError(
            "SolverForge did not find a hard-feasible proposal: "
            f"{explanation['hard_violations']} hard violation(s) remain."
        )
    return solved


def explain_score(plan: SeatPlanningSolution) -> dict[str, Any]:
    hard: dict[str, int] = {
        "missing_block": 0,
        "party_size": 0,
        "product": 0,
        "blocked_or_occupied": 0,
        "wheelchair": 0,
        "companion": 0,
        "lock": 0,
        "overlap": 0,
    }
    soft_penalties: dict[str, int] = {
        "preferences": 0,
        "existing_assignment_changes": 0,
        "isolated_seat_risk": 0,
        "fairness": 0,
    }
    soft_rewards: dict[str, int] = {"zone_quality": 0}

    for party in plan.party_assignments:
        block = party.selected_block
        hard["missing_block"] += int(block is None)
        if block is None:
            continue
        hard["party_size"] += abs(block.size - party.party_size)
        hard["product"] += int(block.product_id != party.product_id)
        hard["blocked_or_occupied"] += block.blocked_count + block.occupied_count
        hard["wheelchair"] += int(
            party.requires_wheelchair
            and block.accessible_count < party.wheelchair_count
        )
        hard["companion"] += int(
            party.requires_companion
            and (party.party_size < 2 or block.accessible_count < 1)
        )
        hard["lock"] += int(
            bool(party.locked_seat_guids)
            and frozenset(block.seat_guids) != frozenset(party.locked_seat_guids)
        )
        if _soft_eligible(party):
            soft_penalties["preferences"] += preference_penalty(party)
            soft_penalties["existing_assignment_changes"] += (
                CHANGE_PENALTY if _changed_existing_assignment(party) else 0
            )
            soft_penalties["isolated_seat_risk"] += (
                boundary_orphan_count(party) * ORPHAN_SEAT_PENALTY
            )
            soft_rewards["zone_quality"] += max(0, block.quality)

    for index, left in enumerate(plan.party_assignments):
        for right in plan.party_assignments[index + 1 :]:
            if _overlap(left, right) or _overlap(right, left):
                left_block = left.selected_block
                right_block = right.selected_block
                assert left_block is not None and right_block is not None
                hard["overlap"] += len(
                    frozenset(left_block.seat_ids).intersection(right_block.seat_ids)
                )
            if _interior_orphan(left, right) or _interior_orphan(right, left):
                soft_penalties["isolated_seat_risk"] += ORPHAN_SEAT_PENALTY
            if _unfair_pair(left, right) or _unfair_pair(right, left):
                soft_penalties["fairness"] += fairness_penalty(left, right)

    hard_violations = sum(hard.values())
    soft_score = sum(soft_rewards.values()) - sum(soft_penalties.values())
    return {
        "hard": hard,
        "soft_penalties": soft_penalties,
        "soft_rewards": soft_rewards,
        "hard_violations": hard_violations,
        "soft_score": soft_score,
    }


def assignment_payload(plan: SeatPlanningSolution) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for party in sorted(plan.party_assignments, key=lambda value: value.party_key):
        block = party.selected_block
        if block is None:
            raise InfeasiblePlanError(f"Party {party.party_key} has no selected block.")
        assigned_seats = position_seat_assignments(party)
        payload.append(
            {
                "party_key": party.party_key,
                "order_id": party.order_id,
                "order_code": party.order_code,
                "position_ids": [
                    position_id for position_id, _seat_id, _seat_guid in assigned_seats
                ],
                "product_id": party.product_id,
                "product_name": party.product_name,
                "preferences": list(party.preferences),
                "requires_wheelchair": party.requires_wheelchair,
                "wheelchair_position_ids": list(party.wheelchair_position_ids),
                "requires_companion": party.requires_companion,
                "current_seat_guids": list(party.current_seat_guids),
                "locked": bool(party.locked_seat_guids),
                "block_idx": block.idx,
                "seat_ids": [
                    seat_id for _position_id, seat_id, _seat_guid in assigned_seats
                ],
                "seat_guids": [
                    seat_guid for _position_id, _seat_id, seat_guid in assigned_seats
                ],
                "zone": block.zone,
                "row": block.row,
                "quality": block.quality,
            }
        )
    return payload


def position_seat_assignments(
    party: PartyAssignment,
) -> tuple[tuple[int, int, str], ...]:
    """Preserve locks/current seats and map wheelchair positions accessibly."""

    block = party.selected_block
    if block is None:
        raise InfeasiblePlanError(f"Party {party.party_key} has no selected block.")
    if len(party.position_ids) != len(block.seat_ids):
        raise InfeasiblePlanError(
            f"Party {party.party_key} does not match its block size."
        )
    seat_pairs = list(zip(block.seat_ids, block.seat_guids, strict=True))
    seat_by_guid = {
        seat_guid: (seat_id, seat_guid)
        for seat_id, seat_guid in seat_pairs
    }
    accessible_ids = set(block.accessible_seat_ids)
    wheelchair_ids = set(party.wheelchair_position_ids)
    if len(accessible_ids) < len(wheelchair_ids):
        raise InfeasiblePlanError(
            f"Party {party.order_code} does not have enough accessible seats."
        )

    if party.locked_seat_guids:
        if (
            len(party.locked_seat_guids) != len(party.position_ids)
            or frozenset(party.locked_seat_guids) != frozenset(block.seat_guids)
        ):
            raise InfeasiblePlanError(
                f"Locked party {party.order_code} has a changed position-to-seat mapping."
            )
        locked = tuple(
            (
                position_id,
                *seat_by_guid[seat_guid],
            )
            for position_id, seat_guid in zip(
                party.position_ids,
                party.locked_seat_guids,
                strict=True,
            )
        )
        if any(
            position_id in wheelchair_ids and seat_id not in accessible_ids
            for position_id, seat_id, _seat_guid in locked
        ):
            raise InfeasiblePlanError(
                f"Locked party {party.order_code} maps a wheelchair position "
                "to a seat that is not configured as accessible."
            )
        return locked

    current_by_position = (
        dict(
            zip(
                party.position_ids,
                party.current_seat_guids,
                strict=True,
            )
        )
        if len(party.current_seat_guids) == len(party.position_ids)
        else {}
    )
    seat_by_position: dict[int, tuple[int, str]] = {}
    used_seat_ids: set[int] = set()

    def preserve_current(position_id: int, *, accessible: bool) -> bool:
        current_guid = current_by_position.get(position_id)
        current_pair = seat_by_guid.get(current_guid or "")
        if (
            current_pair is None
            or current_pair[0] in used_seat_ids
            or (accessible and current_pair[0] not in accessible_ids)
        ):
            return False
        seat_by_position[position_id] = current_pair
        used_seat_ids.add(current_pair[0])
        return True

    for position_id in party.position_ids:
        if position_id not in wheelchair_ids:
            continue
        if preserve_current(position_id, accessible=True):
            continue
        seat_id, seat_guid = next(
            pair
            for pair in seat_pairs
            if pair[0] in accessible_ids and pair[0] not in used_seat_ids
        )
        used_seat_ids.add(seat_id)
        seat_by_position[position_id] = (seat_id, seat_guid)

    for position_id in party.position_ids:
        if position_id in seat_by_position:
            continue
        preserve_current(position_id, accessible=False)

    remaining_seats = iter(
        pair for pair in seat_pairs if pair[0] not in used_seat_ids
    )
    for position_id in party.position_ids:
        if position_id in seat_by_position:
            continue
        seat_id, seat_guid = next(remaining_seats)
        used_seat_ids.add(seat_id)
        seat_by_position[position_id] = (seat_id, seat_guid)

    return tuple(
        (position_id, *seat_by_position[position_id])
        for position_id in party.position_ids
    )


def _block_is_candidate(spec: PartySpec, block: SeatBlock) -> bool:
    if block.size != spec.size or block.product_id != spec.product_id:
        return False
    if not block.available:
        return False
    if (
        spec.requires_wheelchair
        and block.accessible_count < spec.wheelchair_count
    ):
        return False
    if spec.requires_companion and (spec.size < 2 or block.accessible_count < 1):
        return False
    if spec.locked_seat_guids:
        return frozenset(block.seat_guids) == frozenset(spec.locked_seat_guids)
    return True


def _zone_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
