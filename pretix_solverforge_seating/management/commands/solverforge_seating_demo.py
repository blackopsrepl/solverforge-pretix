from typing import Any

from django.core.management.base import BaseCommand

from ...demo import (
    DEMO_USER_EMAIL,
    DEMO_USER_PASSWORD,
    create_demo,
)


class Command(BaseCommand):
    help = "Create the deterministic SolverForge assigned-seating demo event."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--password",
            default=DEMO_USER_PASSWORD,
            help="Password for the deterministic demo organizer account.",
        )

    def handle(self, *args: object, **options: object) -> None:
        result = create_demo(password=str(options["password"]))
        state = "Created" if result.created else "Already present"
        self.stdout.write(
            self.style.SUCCESS(
                f"{state}: /control/event/{result.organizer.slug}/{result.event.slug}/"
                "solverforge-seat-planner/"
            )
        )
        self.stdout.write(f"Login: {DEMO_USER_EMAIL}")
        if result.created:
            self.stdout.write(f"Password: {options['password']}")
