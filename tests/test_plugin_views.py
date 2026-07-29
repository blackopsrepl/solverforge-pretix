from __future__ import annotations

from importlib.metadata import entry_points

import pytest
from django.urls import reverse
from pretix.base.models import Team, User

from pretix_solverforge_seating.apps import SolverForgeSeatingApp


def _planner_url(demo: object, name: str = "index") -> str:
    return reverse(
        f"plugins:pretix_solverforge_seating:{name}",
        kwargs={
            "organizer": demo.organizer.slug,
            "event": demo.event.slug,
        },
    )


@pytest.mark.django_db
def test_plugin_entry_point_discovery_and_event_activation(demo: object) -> None:
    discovered = {
        entry_point.name: entry_point.load()
        for entry_point in entry_points(group="pretix.plugin")
    }

    assert discovered["pretix_solverforge_seating"].__name__ == (
        "pretix_solverforge_seating"
    )
    assert "pretix_solverforge_seating" in demo.event.get_plugins()
    assert SolverForgeSeatingApp.PretixPluginMeta.compatibility == "pretix==2026.6.1"


@pytest.mark.django_db
def test_navigation_and_planner_page_are_native_pretix_views(client: object, demo: object) -> None:
    client.force_login(demo.user)

    response = client.get(_planner_url(demo))

    assert response.status_code == 200
    assert b"SolverForge Seat Planner" in response.content
    assert b"Visual seat map" in response.content
    assert b"48" in response.content
    assert b"Generate proposal" in response.content


@pytest.mark.django_db
def test_permission_checks_allow_read_but_reject_generate(
    client: object,
    demo: object,
) -> None:
    viewer = User.objects.create_user(
        "viewer@solverforge.invalid",
        "viewer-password",
        is_active=True,
        is_verified=True,
    )
    team = Team.objects.create(
        organizer=demo.organizer,
        name="Seat planner viewers",
        all_events=True,
        all_event_permissions=False,
        limit_event_permissions={"event.orders:read": True},
    )
    team.members.add(viewer)
    client.force_login(viewer)

    read_response = client.get(_planner_url(demo))
    write_response = client.post(_planner_url(demo, "generate"))

    assert read_response.status_code == 200
    assert b"Generate proposal" not in read_response.content
    assert write_response.status_code == 403


@pytest.mark.django_db
def test_permission_checks_reject_users_without_event_access(
    client: object,
    demo: object,
) -> None:
    outsider = User.objects.create_user(
        "outsider@solverforge.invalid",
        "outsider-password",
        is_active=True,
        is_verified=True,
    )
    client.force_login(outsider)

    response = client.get(_planner_url(demo))

    assert response.status_code == 404
