from typing import Any

from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from pretix.base.logentrytypes import EventLogEntryType, log_entry_types
from pretix.base.signals import event_copy_data
from pretix.control.signals import nav_event, nav_event_settings

from .models import PlannerConfiguration


@receiver(nav_event, dispatch_uid="pretix_solverforge_seating_nav_event")
def planner_navigation(
    sender: Any,
    request: Any,
    **kwargs: Any,
) -> list[dict[str, object]]:
    if not request.user.has_event_permission(
        request.organizer,
        request.event,
        "event.orders:read",
        request=request,
    ):
        return []
    current = resolve(request.path_info)
    return [
        {
            "label": _("SolverForge Seat Planner"),
            "icon": "th",
            "url": reverse(
                "plugins:pretix_solverforge_seating:index",
                kwargs={
                    "organizer": request.organizer.slug,
                    "event": request.event.slug,
                },
            ),
            "parent": reverse(
                "control:event.orders",
                kwargs={
                    "organizer": request.organizer.slug,
                    "event": request.event.slug,
                },
            ),
            "active": current.namespace == "plugins:pretix_solverforge_seating",
        }
    ]


@receiver(
    nav_event_settings,
    dispatch_uid="pretix_solverforge_seating_nav_event_settings",
)
def planner_settings_navigation(
    sender: Any,
    request: Any,
    **kwargs: Any,
) -> list[dict[str, object]]:
    if not request.user.has_event_permission(
        request.organizer,
        request.event,
        "event.settings.general:write",
        request=request,
    ):
        return []
    current = resolve(request.path_info)
    return [
        {
            "label": _("SolverForge Seat Planner"),
            "url": reverse(
                "plugins:pretix_solverforge_seating:settings",
                kwargs={
                    "organizer": request.organizer.slug,
                    "event": request.event.slug,
                },
            ),
            "active": (
                current.namespace == "plugins:pretix_solverforge_seating"
                and current.url_name == "settings"
            ),
        }
    ]


@receiver(
    event_copy_data,
    dispatch_uid="pretix_solverforge_seating_copy_configuration",
)
def copy_configuration(sender: object, other: object, **kwargs: object) -> None:
    if PlannerConfiguration.objects.filter(event=sender).exists():
        return
    try:
        source = PlannerConfiguration.objects.get(event=other)
    except PlannerConfiguration.DoesNotExist:
        return
    PlannerConfiguration.objects.create(
        event=sender,
        wheelchair_question_identifier=source.wheelchair_question_identifier,
        companion_question_identifier=source.companion_question_identifier,
        preference_question_identifier=source.preference_question_identifier,
        accessible_seat_guids=list(source.accessible_seat_guids),
        aisle_seat_guids=list(source.aisle_seat_guids),
        zone_quality=dict(source.zone_quality),
        random_seed=source.random_seed,
        step_count_limit=source.step_count_limit,
    )


@log_entry_types.new(
    "pretix.solverforge_seating.proposal.generated",
    _("A SolverForge seating proposal was generated."),
)
@log_entry_types.new(
    "pretix.solverforge_seating.proposal.discarded",
    _("A SolverForge seating proposal was discarded."),
)
@log_entry_types.new(
    "pretix.solverforge_seating.proposal.committed",
    _("A SolverForge seating proposal was committed."),
)
@log_entry_types.new(
    "pretix.solverforge_seating.lock.changed",
    _("A SolverForge seating placement lock was changed."),
)
class SolverForgeSeatingLogEntryType(EventLogEntryType):
    pass
