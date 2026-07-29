from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView, TemplateView
from pretix.base.models import SubEvent
from pretix.base.services.orders import OrderError
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.control.views.event import EventSettingsViewMixin
from solverforge import SolverForgeError

from .commit import commit_proposal
from .domain import (
    CommitError,
    InfeasiblePlanError,
    PlanningInputError,
    StaleProposalError,
)
from .extraction import load_planning_snapshot
from .forms import CommitConfirmationForm, PlannerConfigurationForm
from .models import PlacementLock, PlannerConfiguration, SeatingProposal
from .proposals import (
    discard_proposal,
    generate_proposal,
    lock_existing_placement,
    lock_proposed_placement,
    seat_map_payload,
    unlock_placement,
)


class IndexView(EventPermissionRequiredMixin, TemplateView):
    template_name = "pretix_solverforge_seating/planner.html"
    permission = "event.orders:read"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        event = self.request.event
        subevent = _selected_subevent(self.request, source="GET")
        scope_ready = not event.has_subevents or subevent is not None
        try:
            configuration = PlannerConfiguration.objects.get(event=event)
        except PlannerConfiguration.DoesNotExist:
            configuration = PlannerConfiguration(event=event)

        proposal = None
        if scope_ready:
            proposal = (
                SeatingProposal.objects.filter(
                    event=event,
                    subevent=subevent,
                )
                .exclude(status=SeatingProposal.Status.DISCARDED)
                .first()
            )
        snapshot = None
        input_error = ""
        if scope_ready:
            try:
                snapshot = load_planning_snapshot(
                    event,
                    configuration,
                    subevent=subevent,
                    reject_orphan_locks=False,
                )
            except PlanningInputError as exc:
                input_error = str(exc)

        proposal_is_stale = bool(
            proposal
            and proposal.status == SeatingProposal.Status.PROPOSED
            and snapshot
            and proposal.snapshot_fingerprint != snapshot.fingerprint
        )
        assignments = list(proposal.assignments) if proposal else []
        locks = (
            PlacementLock.objects.filter(
                event=event,
                subevent=subevent,
            )
            if scope_ready
            else PlacementLock.objects.none()
        )
        current_lock_keys = set(
            locks.values_list(
                "party_key",
                flat=True,
            )
        )
        for assignment in assignments:
            assignment["changed"] = (
                frozenset(assignment["current_seat_guids"])
                != frozenset(assignment["seat_guids"])
            )
            assignment["currently_locked"] = (
                assignment["party_key"] in current_lock_keys
            )

        can_write = self.request.user.has_event_permission(
            self.request.organizer,
            event,
            "event.orders:write",
            request=self.request,
        )
        can_configure = self.request.user.has_event_permission(
            self.request.organizer,
            event,
            "event.settings.general:write",
            request=self.request,
        )
        seat_map = seat_map_payload(snapshot, proposal) if snapshot else []
        current_parties = (
            [
                {
                    "party_key": party.key,
                    "order_code": party.order_code,
                    "product_name": party.product_name,
                    "position_count": party.size,
                    "seat_guids": list(party.current_seat_guids),
                    "currently_locked": party.key in current_lock_keys,
                }
                for party in snapshot.parties
                if party.current_seat_guids
            ]
            if snapshot
            else []
        )
        context.update(
            {
                "configuration": configuration,
                "subevent": subevent,
                "selected_subevents": [subevent] if subevent is not None else [],
                "scope_ready": scope_ready,
                "proposal": proposal,
                "proposal_is_stale": proposal_is_stale,
                "assignments": assignments,
                "snapshot": snapshot,
                "input_error": input_error,
                "can_write": can_write,
                "can_configure": can_configure,
                "locks": locks,
                "current_parties": current_parties,
                "seat_map": seat_map,
                "proposed_seat_count": sum(
                    len(assignment["seat_guids"]) for assignment in assignments
                ),
                "changed_assignment_count": sum(
                    bool(assignment["changed"]) for assignment in assignments
                ),
            }
        )
        return context


class SettingsView(
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    FormView,
):
    template_name = "pretix_solverforge_seating/settings.html"
    form_class = PlannerConfigurationForm
    permission = "event.settings.general:write"

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        configuration, _ = PlannerConfiguration.objects.get_or_create(
            event=self.request.event
        )
        kwargs.update({"instance": configuration, "event": self.request.event})
        return kwargs

    def form_valid(self, form: PlannerConfigurationForm) -> HttpResponse:
        changed = form.has_changed()
        configuration = form.save()
        if changed:
            SeatingProposal.objects.filter(
                event=self.request.event,
                status=SeatingProposal.Status.PROPOSED,
            ).update(status=SeatingProposal.Status.STALE)
            self.request.event.log_action(
                "pretix.event.settings",
                user=self.request.user,
                data={
                    "solverforge_seating_configuration": configuration.pk,
                    "changed_fields": form.changed_data,
                },
            )
        messages.success(self.request, _("The seat planner configuration was saved."))
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        return _url(self.request, "settings")


class GenerateView(EventPermissionRequiredMixin, View):
    permission = "event.orders:write"

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        subevent = None
        try:
            subevent = _selected_subevent(
                request,
                source="POST",
                required=request.event.has_subevents,
            )
            proposal = generate_proposal(
                request.event,
                request.user,
                subevent=subevent,
            )
        except (PlanningInputError, StaleProposalError, SolverForgeError) as exc:
            messages.error(request, _("No proposal was generated: %(error)s") % {"error": exc})
        else:
            messages.success(
                request,
                _("Proposal %(id)s is ready for review. Nothing has been committed.")
                % {"id": proposal.pk},
            )
        return redirect(_url(request, "index", subevent=subevent))


class _ProposalView(EventPermissionRequiredMixin, View):
    permission = "event.orders:write"

    def get_proposal(self, proposal_id: int) -> SeatingProposal:
        try:
            return SeatingProposal.objects.select_related("subevent").get(
                pk=proposal_id,
                event=self.request.event,
            )
        except SeatingProposal.DoesNotExist as exc:
            raise Http404 from exc


class LockView(_ProposalView):
    def post(
        self,
        request: HttpRequest,
        proposal_id: int,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        proposal = self.get_proposal(proposal_id)
        party_key = request.POST.get("party_key", "")
        try:
            lock_proposed_placement(proposal, party_key, request.user)
        except (InfeasiblePlanError, StaleProposalError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Placement locked. Replan to apply the lock."),
            )
        return redirect(_url(request, "index", subevent=proposal.subevent))


class LockExistingView(EventPermissionRequiredMixin, View):
    permission = "event.orders:write"

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        subevent = None
        try:
            subevent = _selected_subevent(
                request,
                source="POST",
                required=request.event.has_subevents,
            )
            lock_existing_placement(
                request.event,
                request.POST.get("party_key", ""),
                request.user,
                subevent=subevent,
            )
        except (
            InfeasiblePlanError,
            PlanningInputError,
            StaleProposalError,
        ) as exc:
            messages.error(request, _("Placement was not locked: %(error)s") % {"error": exc})
        else:
            messages.success(
                request,
                _("The existing placement is locked. Replan to apply the lock."),
            )
        return redirect(_url(request, "index", subevent=subevent))


class UnlockView(EventPermissionRequiredMixin, View):
    permission = "event.orders:write"

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        try:
            subevent = _selected_subevent(
                request,
                source="POST",
                required=request.event.has_subevents,
            )
        except PlanningInputError as exc:
            messages.error(request, str(exc))
            return redirect(_url(request, "index"))
        lock_id_raw = request.POST.get("lock_id")
        lock_id = int(lock_id_raw) if lock_id_raw and lock_id_raw.isdigit() else None
        changed = unlock_placement(
            request.event,
            subevent=subevent,
            party_key=request.POST.get("party_key") or None,
            lock_id=lock_id,
            user=request.user,
        )
        if changed:
            messages.success(request, _("Placement unlocked. Replan to continue."))
        else:
            messages.info(request, _("The placement was already unlocked."))
        return redirect(_url(request, "index", subevent=subevent))


class DiscardView(_ProposalView):
    def post(
        self,
        request: HttpRequest,
        proposal_id: int,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        proposal = self.get_proposal(proposal_id)
        discard_proposal(proposal, request.user)
        messages.success(request, _("The proposal was discarded."))
        return redirect(_url(request, "index", subevent=proposal.subevent))


class CommitConfirmView(
    EventPermissionRequiredMixin,
    TemplateView,
):
    template_name = "pretix_solverforge_seating/commit_confirm.html"
    permission = "event.orders:write"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        try:
            proposal = SeatingProposal.objects.select_related("subevent").get(
                pk=self.kwargs["proposal_id"],
                event=self.request.event,
            )
        except SeatingProposal.DoesNotExist as exc:
            raise Http404 from exc
        if proposal.status != SeatingProposal.Status.PROPOSED:
            raise PermissionDenied(_("Only a current proposal can be committed."))
        context.update(
            {
                "proposal": proposal,
                "subevent": proposal.subevent,
                "form": CommitConfirmationForm(),
                "changed_positions": sum(
                    frozenset(assignment["current_seat_guids"])
                    != frozenset(assignment["seat_guids"])
                    for assignment in proposal.assignments
                ),
            }
        )
        return context


class CommitView(_ProposalView):
    def post(
        self,
        request: HttpRequest,
        proposal_id: int,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        proposal = self.get_proposal(proposal_id)
        form = CommitConfirmationForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Explicit confirmation is required."))
            return redirect(
                _url(
                    request,
                    "commit.confirm",
                    subevent=proposal.subevent,
                    proposal_id=proposal.pk,
                )
            )
        try:
            result = commit_proposal(
                proposal.pk,
                event=request.event,
                user=request.user,
            )
        except (
            CommitError,
            InfeasiblePlanError,
            StaleProposalError,
            OrderError,
        ) as exc:
            messages.error(request, _("Commit was rejected: %(error)s") % {"error": exc})
        else:
            if result.already_committed:
                messages.info(request, _("This proposal was already committed."))
            else:
                messages.success(
                    request,
                    _("%(count)s real order positions were assigned through pretix.")
                    % {"count": result.changed_positions},
                )
        return redirect(_url(request, "index", subevent=proposal.subevent))


def _selected_subevent(
    request: HttpRequest,
    *,
    source: str,
    required: bool = False,
) -> SubEvent | None:
    values = request.POST if source == "POST" else request.GET
    raw = values.get("subevent", "")
    if not request.event.has_subevents:
        if raw:
            raise Http404
        return None
    if not raw:
        if required:
            raise PlanningInputError(
                "Select one date in this event series before planning seats."
            )
        return None
    if not raw.isdigit():
        raise Http404
    try:
        return request.event.subevents.get(pk=int(raw))
    except SubEvent.DoesNotExist as exc:
        raise Http404 from exc


def _url(
    request: HttpRequest,
    name: str,
    *,
    subevent: SubEvent | None = None,
    **kwargs: object,
) -> str:
    url = reverse(
        f"plugins:pretix_solverforge_seating:{name}",
        kwargs={
            "organizer": request.organizer.slug,
            "event": request.event.slug,
            **kwargs,
        },
    )
    if subevent is not None:
        return f"{url}?{urlencode({'subevent': subevent.pk})}"
    return url
