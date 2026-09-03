"""
Lets one AI revision be produced from another instead of always from the raw
transcript -- "translate the version I just corrected".

A single nullable self-FK. Null keeps the existing meaning (read the job's own
transcript), so every revision already in the database stays correct without a
data migration.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transcriptions', '0012_ai_assist'),
    ]

    operations = [
        migrations.AddField(
            model_name='airevision',
            name='source_revision',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='derived_revisions',
                to='transcriptions.airevision',
            ),
        ),
    ]
