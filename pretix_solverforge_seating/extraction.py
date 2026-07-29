from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from django.db.models import F, Prefetch, Q
from django.utils.timezone import now
from pretix.base.models import (
    CartPosition,
    Event,
    Order,
    OrderPosition,
    QuestionAnswer,
    Voucher,
)

from .domain import (
    InfeasiblePlanError,
    PartySpec,
    PlanningInputError,
    PlanningSnapshot,
    SeatFact,
    with_seat_configuration,
)
from .models import PlacementLock, PlannerConfiguration

_FALSE_ANSWERS = {"", "0", "false", "no", "none", "off"}
_PREFERENCE_TOKENS = {"front", "rear", "aisle"}
_ZONE_PREFERENCE_PATTERN = re.compile(r"^zone(?:[:._-])(.+)$", re.IGNORECASE)


def load_planning_snapshot(
    event: Event,
    configuration: PlannerConfiguration,
    *,
    reject_orphan_locks: bool = True,
) -> PlanningSnapshot:
    if event.has_subevents:
        raise PlanningInputError(
            "Event series and per-date seating plans are not supported yet. "
            "No adjacency assumptions were made."
        )
    if event.seating_plan_id is None:
        raise PlanningInputError("This event has no seating plan.")
    if float(event.settings.seating_minimal_distance) > 0:
        raise PlanningInputError(
            "This event uses a nonzero minimum seat distance. SolverForge Seat "
            "Planner does not yet model pairwise distance between newly assigned "
            "blocks, so it will not guess a plan."
        )

    seat_models = list(
        event.seats.filter(subevent=None)
        .select_related("product")
        .order_by("sorting_rank", "seat_guid", "pk")
    )
    if not seat_models:
        raise PlanningInputError("The event seating plan contains no concrete seats.")

    seat_product_ids = {
        seat.product_id for seat in seat_models if seat.product_id is not None
    }
    positions = list(
        OrderPosition.objects.filter(
            order__event=event,
            order__status__in=(Order.STATUS_PENDING, Order.STATUS_PAID),
            canceled=False,
            item_id__in=seat_product_ids,
            subevent=None,
        )
        .select_related("order", "item", "seat")
        .prefetch_related(
            Prefetch(
                "answers",
                queryset=QuestionAnswer.objects.select_related("question").prefetch_related(
                    "options"
                ),
            )
        )
        .order_by("order_id", "item_id", "positionid", "pk")
    )
    if not positions:
        raise PlanningInputError(
            "No paid or pending ticket positions match products in this seating plan."
        )

    planning_position_ids = {position.pk for position in positions}
    active_seat_positions = list(
        OrderPosition.objects.filter(
            order__event=event,
            order__status__in=(Order.STATUS_PENDING, Order.STATUS_PAID),
            canceled=False,
            seat__isnull=False,
            subevent=None,
        )
        .select_related("order", "seat")
        .order_by("pk")
    )
    immutable_position_seat_ids = {
        position.seat_id
        for position in active_seat_positions
        if position.pk not in planning_position_ids
    }

    timestamp = now()
    carts = list(
        CartPosition.objects.filter(
            event=event,
            subevent=None,
            seat__isnull=False,
            expires__gte=timestamp,
        )
        .order_by("pk")
        .values("pk", "seat_id", "expires", "item_id")
    )
    vouchers = list(
        Voucher.objects.filter(
            event=event,
            subevent=None,
            seat__isnull=False,
            redeemed__lt=F("max_usages"),
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=timestamp))
        .order_by("pk")
        .values("pk", "seat_id", "valid_until", "redeemed", "max_usages", "item_id")
    )
    reserved_seat_ids = {
        int(entry["seat_id"]) for entry in [*carts, *vouchers]
    }
    immutable_occupied_ids = immutable_position_seat_ids | reserved_seat_ids

    accessible_guids = _normalized_guid_set(configuration.accessible_seat_guids)
    aisle_guids = _normalized_guid_set(configuration.aisle_seat_guids)
    seat_facts = with_seat_configuration(
        tuple(
            SeatFact(
                id=seat.pk,
                guid=seat.seat_guid,
                zone=seat.zone_name,
                row=seat.row_name,
                seat_number=seat.seat_number,
                product_id=seat.product_id,
                blocked=seat.blocked,
                occupied=seat.pk in immutable_occupied_ids,
                x=seat.x,
                y=seat.y,
                sorting_rank=seat.sorting_rank,
            )
            for seat in seat_models
        ),
        accessible_guids=accessible_guids,
        aisle_guids=aisle_guids,
    )

    locks = list(
        PlacementLock.objects.filter(event=event).order_by("party_key", "pk")
    )
    lock_by_party = {lock.party_key: lock for lock in locks}
    grouped: dict[tuple[int, int], list[OrderPosition]] = defaultdict(list)
    for position in positions:
        grouped[(position.order_id, position.item_id)].append(position)

    parties: list[PartySpec] = []
    answer_fingerprint: list[dict[str, Any]] = []
    for _, group in sorted(grouped.items()):
        first = group[0]
        position_ids = tuple(position.pk for position in group)
        party_key = _party_key(first.order_id, first.item_id, position_ids)
        lock = lock_by_party.get(party_key)
        locked_seat_guids: tuple[str, ...] = ()
        if lock is not None:
            lock_position_ids = tuple(int(value) for value in lock.position_ids)
            locked_seat_guids = tuple(str(value) for value in lock.seat_guids)
            if (
                lock_position_ids != position_ids
                or len(locked_seat_guids) != len(position_ids)
            ):
                raise InfeasiblePlanError(
                    f"Organizer lock {lock.pk} no longer contains the exact party "
                    "position mapping. Unlock it before replanning."
                )
        answer_rows = [
            answer
            for position in group
            for answer in position.answers.all()
        ]
        answer_fingerprint.extend(
            {
                "position_id": answer.orderposition_id,
                "question": answer.question.identifier,
                "answer": answer.answer,
                "options": sorted(option.identifier for option in answer.options.all()),
            }
            for answer in answer_rows
        )
        preferences = _party_preferences(
            answer_rows,
            configuration.preference_question_identifier,
        )
        wheelchair_position_ids = tuple(
            position.pk
            for position in group
            if _question_is_true(
                list(position.answers.all()),
                configuration.wheelchair_question_identifier,
            )
        )
        parties.append(
            PartySpec(
                key=party_key,
                order_id=first.order_id,
                order_code=first.order.code,
                position_ids=position_ids,
                product_id=first.item_id,
                product_name=str(first.item.name),
                preferences=preferences,
                wheelchair_position_ids=wheelchair_position_ids,
                requires_companion=_question_is_true(
                    answer_rows,
                    configuration.companion_question_identifier,
                ),
                current_seat_guids=tuple(
                    position.seat.seat_guid
                    for position in group
                    if position.seat_id is not None
                ),
                locked_seat_guids=locked_seat_guids,
            )
        )

    party_keys = {party.key for party in parties}
    orphan_locks = [lock for lock in locks if lock.party_key not in party_keys]
    if orphan_locks and reject_orphan_locks:
        identifiers = ", ".join(str(lock.pk) for lock in orphan_locks)
        raise InfeasiblePlanError(
            "Organizer lock(s) "
            f"{identifiers} refer to ticket positions that changed or disappeared. "
            "Unlock them before replanning."
        )

    canonical = {
        "event": {
            "id": event.pk,
            "seating_plan_id": event.seating_plan_id,
            "seating_minimal_distance": event.settings.seating_minimal_distance,
            "seating_distance_within_row": event.settings.seating_distance_within_row,
        },
        "configuration": _configuration_payload(configuration),
        "seats": [
            {
                "id": seat.pk,
                "guid": seat.seat_guid,
                "zone": seat.zone_name,
                "row": seat.row_name,
                "number": seat.seat_number,
                "product_id": seat.product_id,
                "blocked": seat.blocked,
                "x": seat.x,
                "y": seat.y,
                "sorting_rank": seat.sorting_rank,
            }
            for seat in seat_models
        ],
        "orders": [
            {
                "id": position.order_id,
                "code": position.order.code,
                "status": position.order.status,
                "last_modified": position.order.last_modified.isoformat(),
                "position_id": position.pk,
                "positionid": position.positionid,
                "item_id": position.item_id,
                "seat_id": position.seat_id,
                "canceled": position.canceled,
            }
            for position in positions
        ],
        "all_active_assignments": [
            {
                "position_id": position.pk,
                "order_id": position.order_id,
                "seat_id": position.seat_id,
                "last_modified": position.order.last_modified.isoformat(),
            }
            for position in active_seat_positions
        ],
        "answers": sorted(
            answer_fingerprint,
            key=lambda value: (
                int(value["position_id"]),
                str(value["question"]),
            ),
        ),
        "carts": [_json_safe_row(row) for row in carts],
        "vouchers": [_json_safe_row(row) for row in vouchers],
        "locks": [
            {
                "id": lock.pk,
                "party_key": lock.party_key,
                "position_ids": list(lock.position_ids),
                "seat_guids": list(lock.seat_guids),
                "updated_at": lock.updated_at.isoformat(),
            }
            for lock in locks
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    all_occupied_seat_ids = {
        position.seat_id for position in active_seat_positions
    } | reserved_seat_ids
    summary = {
        "parties": len(parties),
        "positions": sum(party.size for party in parties),
        "seats": len(seat_facts),
        "available": sum(
            not seat.blocked and seat.id not in all_occupied_seat_ids
            for seat in seat_facts
        ),
        "blocked": sum(seat.blocked for seat in seat_facts),
        "occupied": len(all_occupied_seat_ids),
        "locked_parties": sum(bool(party.locked_seat_guids) for party in parties),
        "orphan_locks": len(orphan_locks),
    }
    return PlanningSnapshot(
        event_id=event.pk,
        seats=seat_facts,
        parties=tuple(parties),
        fingerprint=fingerprint,
        summary=summary,
    )


def _party_key(order_id: int, item_id: int, position_ids: tuple[int, ...]) -> str:
    joined = "-".join(str(position_id) for position_id in position_ids)
    return f"order-{order_id}:item-{item_id}:positions-{joined}"


def _question_is_true(
    answers: list[QuestionAnswer],
    identifier: str,
) -> bool:
    if not identifier:
        return False
    for answer in answers:
        if answer.question.identifier != identifier:
            continue
        tokens = _answer_tokens(answer)
        if any(token.casefold() not in _FALSE_ANSWERS for token in tokens):
            return True
    return False


def _party_preferences(
    answers: list[QuestionAnswer],
    identifier: str,
) -> tuple[str, ...]:
    if not identifier:
        return ()
    values: set[str] = set()
    for answer in answers:
        if answer.question.identifier != identifier:
            continue
        for token in _answer_tokens(answer):
            normalized = token.strip()
            folded = normalized.casefold()
            if folded in _PREFERENCE_TOKENS:
                values.add(folded)
                continue
            zone_match = _ZONE_PREFERENCE_PATTERN.match(normalized)
            if zone_match and zone_match.group(1).strip():
                values.add(f"zone:{zone_match.group(1).strip()}")
    return tuple(sorted(values, key=str.casefold))


def _answer_tokens(answer: QuestionAnswer) -> set[str]:
    values = {
        part.strip()
        for part in re.split(r"[,;\n]", answer.answer or "")
        if part.strip()
    }
    values.update(
        option.identifier
        for option in answer.options.all()
        if option.identifier
    )
    return values


def _normalized_guid_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _configuration_payload(configuration: PlannerConfiguration) -> dict[str, Any]:
    return {
        "wheelchair_question_identifier": configuration.wheelchair_question_identifier,
        "companion_question_identifier": configuration.companion_question_identifier,
        "preference_question_identifier": configuration.preference_question_identifier,
        "accessible_seat_guids": sorted(
            _normalized_guid_set(configuration.accessible_seat_guids)
        ),
        "aisle_seat_guids": sorted(
            _normalized_guid_set(configuration.aisle_seat_guids)
        ),
        "zone_quality": {
            str(key): int(value)
            for key, value in sorted(configuration.zone_quality.items())
        },
        "random_seed": configuration.random_seed,
        "step_count_limit": configuration.step_count_limit,
    }


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }
