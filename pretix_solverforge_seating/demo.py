from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from pretix.base.models import (
    Event,
    Item,
    Order,
    OrderPosition,
    Organizer,
    Question,
    QuestionAnswer,
    QuestionOption,
    SeatingPlan,
    Team,
    User,
)
from pretix.base.services.orders import OrderChangeManager
from pretix.base.services.seating import generate_seats

from .extraction import load_planning_snapshot
from .models import PlacementLock, PlannerConfiguration

DEMO_ORGANIZER_SLUG = "solverforge-demo"
DEMO_EVENT_SLUG = "assigned-seating"
DEMO_USER_EMAIL = "admin@solverforge.invalid"
DEMO_USER_PASSWORD = "solverforge-demo"


@dataclass(frozen=True, slots=True)
class DemoResult:
    organizer: Organizer
    event: Event
    user: User
    created: bool


@transaction.atomic
def create_demo(*, password: str = DEMO_USER_PASSWORD) -> DemoResult:
    """Create the deterministic development event once, without touching other data."""

    with scopes_disabled():
        existing = Event.objects.filter(
            organizer__slug=DEMO_ORGANIZER_SLUG,
            slug=DEMO_EVENT_SLUG,
        ).select_related("organizer").first()
        if existing is not None:
            user = User.objects.get(email=DEMO_USER_EMAIL)
            return DemoResult(
                organizer=existing.organizer,
                event=existing,
                user=user,
                created=False,
            )

        organizer, organizer_created = Organizer.objects.get_or_create(
            slug=DEMO_ORGANIZER_SLUG,
            defaults={"name": "SolverForge Demo"},
        )
        if not organizer_created:
            raise RuntimeError(
                "The reserved demo organizer slug already exists without the "
                "expected event. "
                "No existing data was changed."
            )
        if User.objects.filter(email=DEMO_USER_EMAIL).exists():
            raise RuntimeError(
                "The reserved demo user email already exists without the expected "
                "event. No existing data was changed."
            )
        user = User.objects.create(
            email=DEMO_USER_EMAIL,
            fullname="SolverForge Demo Organizer",
            is_active=True,
            is_verified=True,
        )
        user.set_password(password)
        user.save()
        team = Team.objects.create(
            organizer=organizer,
            name="Administrators",
            all_events=True,
            all_event_permissions=True,
            all_organizer_permissions=True,
        )
        team.members.add(user)

    with scope(organizer=organizer):
        plan = SeatingPlan.objects.create(
            organizer=organizer,
            name="SolverForge Hall",
            layout=json.dumps(_seating_plan_layout(), separators=(",", ":")),
        )
        event = Event.objects.create(
            organizer=organizer,
            name="SolverForge Assigned Seating Demo",
            slug=DEMO_EVENT_SLUG,
            date_from=now() + timedelta(days=30),
            seating_plan=plan,
            plugins="",
        )
        event.settings.set("seating_choice", False)
        event.settings.set("seating_minimal_distance", 0)
        event.settings.set("seating_distance_within_row", False)
        event.enable_plugin("pretix_solverforge_seating")
        event.save(update_fields=("plugins", "seating_plan"))

        standard = Item.objects.create(
            event=event,
            name="Standard admission",
            default_price=Decimal("35.00"),
            admission=True,
        )
        premium = Item.objects.create(
            event=event,
            name="Premium admission",
            default_price=Decimal("55.00"),
            admission=True,
        )
        quota = event.quotas.create(name="All seated tickets", size=48)
        quota.items.add(standard, premium)
        generate_seats(
            event,
            None,
            plan,
            {"Standard": standard, "Premium": premium},
            blocked_guids={"B-04", "D-08"},
        )

        questions, preference_options = _create_questions(event, standard, premium)
        orders = {
            "SF001": _create_party_order(event, premium, "SF001", 1),
            "SF002": _create_party_order(event, standard, "SF002", 2),
            "SF003": _create_party_order(event, standard, "SF003", 3),
            "SF004": _create_party_order(event, standard, "SF004", 4),
            "SF005": _create_party_order(event, standard, "SF005", 5),
        }
        _answer(
            orders["SF001"][0],
            questions["preference"],
            options=(preference_options["front"],),
        )
        _answer(
            orders["SF002"][0],
            questions["wheelchair"],
            answer="True",
        )
        _answer(
            orders["SF002"][0],
            questions["companion"],
            answer="True",
        )
        _answer(
            orders["SF002"][0],
            questions["preference"],
            options=(preference_options["aisle"],),
        )
        _answer(
            orders["SF003"][0],
            questions["preference"],
            options=(
                preference_options["front"],
                preference_options["zone-stalls"],
            ),
        )
        _answer(
            orders["SF004"][0],
            questions["preference"],
            options=(
                preference_options["aisle"],
                preference_options["zone-stalls"],
            ),
        )
        _answer(
            orders["SF005"][0],
            questions["preference"],
            options=(preference_options["rear"],),
        )

        seats_by_guid = {
            seat.seat_guid: seat
            for seat in event.seats.select_related("product").order_by("pk")
        }
        _assign_positions(
            orders["SF001"],
            ("C-12",),
            seats_by_guid,
            user,
        )
        _assign_positions(
            orders["SF002"],
            ("A-01", "A-02"),
            seats_by_guid,
            user,
        )
        _assign_positions(
            orders["SF003"],
            ("D-01", "D-03", "D-05"),
            seats_by_guid,
            user,
        )
        _assign_positions(
            orders["SF004"],
            ("D-09", "D-10", "D-11", "D-12"),
            seats_by_guid,
            user,
        )

        configuration = PlannerConfiguration.objects.create(
            event=event,
            wheelchair_question_identifier="wheelchair",
            companion_question_identifier="companion",
            preference_question_identifier="seat-preference",
            accessible_seat_guids=["A-01"],
            aisle_seat_guids=[
                f"{row}-{number:02d}"
                for row in ("A", "B", "C", "D")
                for number in (6, 7)
            ],
            zone_quality={"Stalls": 90, "Balcony": 45},
            random_seed=20_260_601,
            step_count_limit=750,
        )
        snapshot = load_planning_snapshot(event, configuration)
        wheelchair_party = next(
            party for party in snapshot.parties if party.order_code == "SF002"
        )
        PlacementLock.objects.create(
            event=event,
            party_key=wheelchair_party.key,
            position_ids=list(wheelchair_party.position_ids),
            seat_guids=["A-01", "A-02"],
            created_by=user,
        )
        return DemoResult(
            organizer=organizer,
            event=event,
            user=user,
            created=True,
        )


def _seating_plan_layout() -> dict[str, Any]:
    zones = [
        {
            "name": "Stalls",
            "position": {"x": 0, "y": 0},
            "rows": [
                _row("A", y=1, category="Standard"),
                _row("B", y=2, category="Standard"),
            ],
        },
        {
            "name": "Balcony",
            "position": {"x": 0, "y": 0},
            "rows": [
                _row("C", y=4, category="Premium"),
                _row("D", y=5, category="Standard"),
            ],
        },
    ]
    return {
        "name": "SolverForge Hall",
        "categories": [
            {"name": "Standard", "color": "#5b8def"},
            {"name": "Premium", "color": "#d99b2b"},
        ],
        "zones": zones,
        "size": {"width": 560, "height": 260},
    }


def _row(row: str, *, y: int, category: str) -> dict[str, Any]:
    return {
        "row_number": row,
        "row_label": "Row %s",
        "seat_label": "Seat %s",
        "position": {"x": 24, "y": y * 34},
        "seats": [
            {
                "seat_guid": f"{row}-{number:02d}",
                "seat_number": str(number),
                "position": {
                    "x": (number - 1) * 30 + (60 if number >= 7 else 0),
                    "y": 0,
                },
                "category": category,
            }
            for number in range(1, 13)
        ],
    }


def _create_questions(
    event: Event,
    standard: Item,
    premium: Item,
) -> tuple[dict[str, Question], dict[str, QuestionOption]]:
    wheelchair = Question.objects.create(
        event=event,
        question="Do you need a wheelchair-accessible placement?",
        identifier="wheelchair",
        type=Question.TYPE_BOOLEAN,
        required=False,
        position=1,
    )
    companion = Question.objects.create(
        event=event,
        question="Should a companion remain adjacent?",
        identifier="companion",
        type=Question.TYPE_BOOLEAN,
        required=False,
        position=2,
    )
    preference = Question.objects.create(
        event=event,
        question="Which seating preferences apply?",
        identifier="seat-preference",
        type=Question.TYPE_CHOICE_MULTIPLE,
        required=False,
        position=3,
    )
    for question in (wheelchair, companion, preference):
        question.items.add(standard, premium)
    option_labels = {
        "front": "Near the front",
        "rear": "Near the rear",
        "aisle": "Next to an aisle",
        "zone-stalls": "Stalls",
        "zone-balcony": "Balcony",
    }
    options = {
        identifier: QuestionOption.objects.create(
            question=preference,
            identifier=identifier,
            answer=label,
            position=position,
        )
        for position, (identifier, label) in enumerate(option_labels.items())
    }
    return (
        {
            "wheelchair": wheelchair,
            "companion": companion,
            "preference": preference,
        },
        options,
    )


def _create_party_order(
    event: Event,
    item: Item,
    code: str,
    size: int,
) -> list[OrderPosition]:
    timestamp = now()
    order = Order.objects.create(
        event=event,
        organizer=event.organizer,
        code=code,
        status=Order.STATUS_PAID,
        email=f"{code.casefold()}@solverforge.invalid",
        locale="en",
        datetime=timestamp,
        expires=timestamp + timedelta(days=14),
        total=item.default_price * size,
        sales_channel=event.organizer.sales_channels.get(identifier="web"),
    )
    positions = [
        OrderPosition.objects.create(
            order=order,
            organizer=event.organizer,
            item=item,
            variation=None,
            price=item.default_price,
            positionid=position,
        )
        for position in range(1, size + 1)
    ]
    order.create_transactions(is_new=True, positions=positions, fees=[])
    return positions


def _answer(
    position: OrderPosition,
    question: Question,
    *,
    answer: str = "",
    options: tuple[QuestionOption, ...] = (),
) -> None:
    question_answer = QuestionAnswer.objects.create(
        orderposition=position,
        question=question,
        answer=answer or ",".join(option.identifier for option in options),
    )
    if options:
        question_answer.options.add(*options)


def _assign_positions(
    positions: list[OrderPosition],
    seat_guids: tuple[str, ...],
    seats_by_guid: dict[str, object],
    user: User,
) -> None:
    manager = OrderChangeManager(
        positions[0].order,
        user=user,
        notify=False,
        reissue_invoice=False,
    )
    for position, seat_guid in zip(positions, seat_guids, strict=True):
        manager.change_seat(position, seats_by_guid[seat_guid])
    manager.commit()
