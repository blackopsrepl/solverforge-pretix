import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pretixbase", "0301_reusablemedium_remove_orderposition"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlannerConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("wheelchair_question_identifier", models.CharField(blank=True, max_length=190)),
                ("companion_question_identifier", models.CharField(blank=True, max_length=190)),
                ("preference_question_identifier", models.CharField(blank=True, max_length=190)),
                ("accessible_seat_guids", models.JSONField(blank=True, default=list)),
                ("aisle_seat_guids", models.JSONField(blank=True, default=list)),
                ("zone_quality", models.JSONField(blank=True, default=dict)),
                ("random_seed", models.PositiveBigIntegerField(default=20260601)),
                ("step_count_limit", models.PositiveIntegerField(default=500)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="solverforge_seating_configuration",
                        to="pretixbase.event",
                    ),
                ),
            ],
            options={
                "verbose_name": "SolverForge seat planner configuration",
                "verbose_name_plural": "SolverForge seat planner configurations",
            },
        ),
        migrations.CreateModel(
            name="SeatingProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("proposed", "Proposed"),
                            ("stale", "Stale"),
                            ("discarded", "Discarded"),
                            ("committed", "Committed"),
                            ("infeasible", "Infeasible"),
                        ],
                        default="proposed",
                        max_length=16,
                    ),
                ),
                ("snapshot_fingerprint", models.CharField(db_index=True, max_length=64)),
                ("assignments", models.JSONField(default=list)),
                ("input_summary", models.JSONField(default=dict)),
                ("solver_config", models.JSONField(default=dict)),
                ("score", models.JSONField(default=dict)),
                ("score_explanation", models.JSONField(default=dict)),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("committed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="solverforge_seating_proposals",
                        to="pretixbase.event",
                    ),
                ),
            ],
            options={"ordering": ("-created_at", "-pk")},
        ),
        migrations.CreateModel(
            name="PlacementLock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("party_key", models.CharField(max_length=255)),
                ("position_ids", models.JSONField(default=list)),
                ("seat_guids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="solverforge_seating_locks",
                        to="pretixbase.event",
                    ),
                ),
            ],
            options={"ordering": ("party_key",)},
        ),
        migrations.AddIndex(
            model_name="seatingproposal",
            index=models.Index(
                fields=["event", "status", "-created_at"],
                name="sf_seating_event_status_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="placementlock",
            constraint=models.UniqueConstraint(
                fields=("event", "party_key"),
                name="solverforge_seating_unique_party_lock",
            ),
        ),
    ]
