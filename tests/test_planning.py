from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from solverforge import Solver

from pretix_solverforge_seating.domain import (
    InfeasiblePlanError,
    generate_contiguous_blocks,
)
from pretix_solverforge_seating.planning import (
    NATIVE_MODULE_NAME,
    assignment_payload,
    build_solver_plan,
    explain_score,
    position_seat_assignments,
    preference_penalty,
    solve_seating_plan,
)

from .helpers import party, row_seats


def test_loaded_module_is_the_native_extension() -> None:
    assert NATIVE_MODULE_NAME == "solverforge._native"


def test_hard_constraints_detect_size_product_blocked_occupied_and_accessibility() -> None:
    seats = (
        *row_seats(count=5, product_id=1, blocked={2}, occupied={5}),
        *row_seats(
            count=4,
            row="B",
            product_id=2,
            accessible={1},
            y=2,
            id_offset=10,
        ),
    )
    blocks = generate_contiguous_blocks(seats, {1, 2, 3})
    spec = party("wheelchair", size=2, product_id=1)
    plan = build_solver_plan((spec,), blocks, random_seed=7)
    assignment = plan.party_assignments[0]
    assignment.wheelchair_position_ids = (assignment.position_ids[0],)
    assignment.requires_companion = True

    assignment.seat_block_idx = next(
        block.idx
        for block in blocks
        if block.size == 3 and block.product_id == 1
    )
    explanation = explain_score(plan)
    assert explanation["hard"]["party_size"] == 1

    assignment.seat_block_idx = next(
        block.idx
        for block in blocks
        if block.size == 2 and block.product_id == 2
    )
    explanation = explain_score(plan)
    assert explanation["hard"]["product"] == 1

    assignment.seat_block_idx = next(
        block.idx
        for block in blocks
        if block.size == 2 and block.product_id == 1 and block.blocked_count
    )
    explanation = explain_score(plan)
    assert explanation["hard"]["blocked_or_occupied"] >= 1
    assert explanation["hard"]["wheelchair"] == 1
    assert explanation["hard"]["companion"] == 1


def test_accessible_companion_candidate_requires_adjacency_and_accessible_seat() -> None:
    blocks = generate_contiguous_blocks(
        row_seats(count=5, accessible={3}),
        {1, 2},
    )
    with pytest.raises(InfeasiblePlanError, match="companion-adjacent"):
        build_solver_plan(
            (party("solo", size=1, wheelchair=True, companion=True),),
            blocks,
            random_seed=1,
        )

    plan = build_solver_plan(
        (party("pair", size=2, wheelchair=True, companion=True),),
        blocks,
        random_seed=1,
    )
    assert {
        blocks[index].seat_guids
        for index in plan.party_assignments[0].candidate_block_indices
    } == {("A-2", "A-3"), ("A-3", "A-4")}


def test_wheelchair_positions_receive_accessible_seats_and_require_enough() -> None:
    blocks = generate_contiguous_blocks(
        row_seats(count=4, accessible={2}),
        {3},
    )
    base = party("accessible-mapping", size=3)
    wheelchair_position = base.position_ids[1]
    spec = replace(
        base,
        wheelchair_position_ids=(wheelchair_position,),
    )
    plan = build_solver_plan((spec,), blocks, random_seed=1)
    assignment = plan.party_assignments[0]
    assignment.seat_block_idx = next(
        block.idx
        for block in blocks
        if block.seat_guids == ("A-1", "A-2", "A-3")
    )

    mapping = {
        position_id: seat_id
        for position_id, seat_id, _seat_guid in position_seat_assignments(assignment)
    }

    assert mapping[wheelchair_position] == 2
    with pytest.raises(InfeasiblePlanError, match="wheelchair-accessible"):
        build_solver_plan(
            (
                replace(
                    base,
                    wheelchair_position_ids=base.position_ids[:2],
                ),
            ),
            blocks,
            random_seed=1,
        )


def test_locked_position_mapping_is_preserved_and_accessibility_checked() -> None:
    blocks = generate_contiguous_blocks(
        row_seats(count=3, accessible={2}),
        {3},
    )
    base = party("locked-accessible", size=3)
    wheelchair_position = base.position_ids[1]
    locked = replace(
        base,
        wheelchair_position_ids=(wheelchair_position,),
        current_seat_guids=("A-3", "A-2", "A-1"),
        locked_seat_guids=("A-3", "A-2", "A-1"),
    )
    plan = build_solver_plan((locked,), blocks, random_seed=1)
    assignment = plan.party_assignments[0]

    assert [
        seat_guid
        for _position_id, _seat_id, seat_guid in position_seat_assignments(
            assignment
        )
    ] == ["A-3", "A-2", "A-1"]

    invalid = replace(
        locked,
        locked_seat_guids=("A-1", "A-3", "A-2"),
    )
    invalid_plan = build_solver_plan((invalid,), blocks, random_seed=1)
    with pytest.raises(InfeasiblePlanError, match="not configured as accessible"):
        position_seat_assignments(invalid_plan.party_assignments[0])


def test_overlap_and_organizer_lock_are_hard_constraints() -> None:
    blocks = generate_contiguous_blocks(row_seats(count=6), {2})
    plan = build_solver_plan(
        (
            party("locked", size=2, order_id=1, locked=("A-1", "A-2")),
            party("other", size=2, order_id=2),
        ),
        blocks,
        random_seed=1,
    )
    locked, other = sorted(plan.party_assignments, key=lambda value: value.party_key)
    locked.seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("A-3", "A-4")
    )
    other.seat_block_idx = locked.seat_block_idx

    explanation = explain_score(plan)

    assert explanation["hard"]["lock"] == 1
    assert explanation["hard"]["overlap"] == 2
    assert Solver.analyze(plan)["levels"][0] == -3


def test_minimum_distance_separates_different_orders_but_not_one_order() -> None:
    blocks = generate_contiguous_blocks(row_seats(count=7), {2})
    different_orders = build_solver_plan(
        (
            party("left", size=2, order_id=1),
            party("right", size=2, order_id=2),
        ),
        blocks,
        random_seed=4,
        minimum_seat_distance=2,
        distance_within_row=True,
    )
    assignments = sorted(
        different_orders.party_assignments,
        key=lambda assignment: assignment.party_key,
    )
    assignments[0].seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("A-1", "A-2")
    )
    assignments[1].seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("A-3", "A-4")
    )

    assert (
        explain_score(different_orders)["hard"][
            "minimum_distance_between_parties"
        ]
        > 0
    )
    assert Solver.analyze(different_orders)["levels"][0] < 0

    solved = solve_seating_plan(
        different_orders,
        random_seed=4,
        step_count_limit=40,
        include_construction=False,
    )
    assert solved.score["levels"][0] == 0

    one_order = build_solver_plan(
        (
            party("category-one", size=2, product_id=1, order_id=7),
            party("category-two", size=2, product_id=1, order_id=7),
        ),
        blocks,
        random_seed=4,
        minimum_seat_distance=2,
        distance_within_row=True,
    )
    same_order_assignments = sorted(
        one_order.party_assignments,
        key=lambda assignment: assignment.party_key,
    )
    same_order_assignments[0].seat_block_idx = assignments[0].seat_block_idx
    same_order_assignments[1].seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("A-3", "A-4")
    )
    assert (
        explain_score(one_order)["hard"]["minimum_distance_between_parties"]
        == 0
    )


def test_preference_scoring_honors_front_rear_aisle_and_zone_slug() -> None:
    seats = (
        *row_seats(count=4, row="A", zone="Main Floor", aisle={4}, y=1),
        *row_seats(
            count=4,
            row="B",
            zone="Rear Balcony",
            aisle={1},
            y=10,
            id_offset=10,
        ),
    )
    blocks = generate_contiguous_blocks(seats, {1}, {"Main Floor": 80})
    spec = party(
        "preference",
        size=1,
        preferences=("front", "aisle", "zone:main-floor"),
    )
    plan = build_solver_plan((spec,), blocks, random_seed=1)
    assignment = plan.party_assignments[0]
    assignment.seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("A-4",)
    )
    assert preference_penalty(assignment) == 0
    assert explain_score(plan)["soft_rewards"]["zone_quality"] == 80

    assignment.seat_block_idx = next(
        block.idx for block in blocks if block.seat_guids == ("B-2",)
    )
    assert preference_penalty(assignment) > 20


def test_change_move_repairs_negative_hard_score_with_actual_local_search() -> None:
    blocks = generate_contiguous_blocks(row_seats(count=6), {2})
    plan = build_solver_plan(
        (
            party("alpha", size=2, order_id=1),
            party("beta", size=2, order_id=2),
        ),
        blocks,
        random_seed=19,
    )
    overlapping = next(
        block.idx for block in blocks if block.seat_guids == ("A-1", "A-2")
    )
    for assignment in plan.party_assignments:
        assignment.seat_block_idx = overlapping
    before = Solver.analyze(plan)
    assert before["levels"][0] == -2

    solved = solve_seating_plan(
        plan,
        random_seed=19,
        step_count_limit=30,
        include_construction=False,
    )

    assert solved.score["levels"][0] == 0
    assert len(
        {
            seat_id
            for assignment in solved.party_assignments
            for seat_id in assignment.selected_block.seat_ids
        }
    ) == 4


def test_swap_move_exchanges_compatible_parties_to_improve_preferences() -> None:
    seats = (
        *row_seats(count=2, row="A", zone="Front", y=1),
        *row_seats(count=2, row="B", zone="Rear", y=8, id_offset=10),
    )
    blocks = generate_contiguous_blocks(seats, {2})
    plan = build_solver_plan(
        (
            party(
                "front-party",
                size=2,
                order_id=1,
                preferences=("zone:front",),
            ),
            party(
                "rear-party",
                size=2,
                order_id=2,
                preferences=("zone:rear",),
            ),
        ),
        blocks,
        random_seed=3,
    )
    assignments = {
        assignment.party_key: assignment for assignment in plan.party_assignments
    }
    assignments["front-party"].seat_block_idx = next(
        block.idx for block in blocks if block.zone == "Rear"
    )
    assignments["rear-party"].seat_block_idx = next(
        block.idx for block in blocks if block.zone == "Front"
    )
    before = Solver.analyze(plan)
    config = {
        "random_seed": 3,
        "phases": [
            {
                "type": "local_search",
                "local_search_type": "acceptor_forager",
                "move_selector": {
                    "type": "swap_move_selector",
                    "selection_order": "original",
                    "entity_class": "PartyAssignment",
                    "variable_name": "seat_block_idx",
                },
                "acceptor": {"type": "hill_climbing"},
                "forager": {"type": "best_score"},
                "termination": {"step_count_limit": 5},
            }
        ],
    }

    solved = Solver.solve(plan, config)

    assert solved.score["levels"][1] > before["levels"][1]
    solved_assignments = {
        assignment.party_key: assignment for assignment in solved.party_assignments
    }
    assert solved_assignments["front-party"].selected_block.zone == "Front"
    assert solved_assignments["rear-party"].selected_block.zone == "Rear"


def test_fixed_seed_solving_is_deterministic() -> None:
    blocks = generate_contiguous_blocks(row_seats(count=9), {2, 3})
    specs = (
        party("alpha", size=2, order_id=1),
        party("beta", size=2, order_id=2),
        party("gamma", size=3, order_id=3),
    )

    def solve_once() -> list[dict[str, object]]:
        plan = build_solver_plan(deepcopy(specs), blocks, random_seed=77)
        solved = solve_seating_plan(
            plan,
            random_seed=77,
            step_count_limit=60,
        )
        return assignment_payload(solved)

    assert solve_once() == solve_once()
