import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pretix_solverforge_seating", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="placementlock",
            name="subevent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="solverforge_seating_locks",
                to="pretixbase.subevent",
            ),
        ),
        migrations.AddField(
            model_name="seatingproposal",
            name="subevent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="solverforge_seating_proposals",
                to="pretixbase.subevent",
            ),
        ),
    ]
