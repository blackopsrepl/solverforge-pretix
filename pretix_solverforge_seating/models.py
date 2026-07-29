from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from pretix.base.models import Event


class PlannerConfiguration(models.Model):
    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        related_name="solverforge_seating_configuration",
    )
    wheelchair_question_identifier = models.CharField(max_length=190, blank=True)
    companion_question_identifier = models.CharField(max_length=190, blank=True)
    preference_question_identifier = models.CharField(max_length=190, blank=True)
    accessible_seat_guids = models.JSONField(default=list, blank=True)
    aisle_seat_guids = models.JSONField(default=list, blank=True)
    zone_quality = models.JSONField(default=dict, blank=True)
    random_seed = models.PositiveBigIntegerField(default=20_260_601)
    step_count_limit = models.PositiveIntegerField(default=500)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("SolverForge seat planner configuration")
        verbose_name_plural = _("SolverForge seat planner configurations")

    def __str__(self) -> str:
        return str(self.event)


class PlacementLock(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="solverforge_seating_locks",
    )
    party_key = models.CharField(max_length=255)
    position_ids = models.JSONField(default=list)
    seat_guids = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("event", "party_key"),
                name="solverforge_seating_unique_party_lock",
            ),
        ]
        ordering = ("party_key",)

    def __str__(self) -> str:
        return f"{self.event}: {self.party_key}"


class SeatingProposal(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed", _("Proposed")
        STALE = "stale", _("Stale")
        DISCARDED = "discarded", _("Discarded")
        COMMITTED = "committed", _("Committed")
        INFEASIBLE = "infeasible", _("Infeasible")

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="solverforge_seating_proposals",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROPOSED,
    )
    snapshot_fingerprint = models.CharField(max_length=64, db_index=True)
    assignments = models.JSONField(default=list)
    input_summary = models.JSONField(default=dict)
    solver_config = models.JSONField(default=dict)
    score = models.JSONField(default=dict)
    score_explanation = models.JSONField(default=dict)
    message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(
                fields=("event", "status", "-created_at"),
                name="sf_seating_event_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event}: proposal {self.pk} ({self.get_status_display()})"
