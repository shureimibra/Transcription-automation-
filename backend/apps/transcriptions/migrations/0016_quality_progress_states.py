"""
Adds the two in-progress quality states.

A choices-only change: no column is added, no stored value changes meaning.
Existing rows keep whatever they hold, and 'not_run' continues to mean
exactly what it meant -- the difference is that from now on a gated run
passes through 'pending' and 'auditing' on its way to a verdict, so
'not_run' stops being overloaded with "gated, but we have not heard back".
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transcriptions', '0015_ai_quality'),
    ]

    operations = [
        migrations.AlterField(
            model_name='airevision',
            name='quality_status',
            field=models.CharField(
                choices=[
                    ('not_run', 'No quality pipeline'),
                    ('pending', 'Quality Pipeline Queued'),
                    ('auditing', 'Auditing'),
                    ('validated', 'Validated'),
                    ('quality_review_required', 'Quality Review Required'),
                    ('technical_failure', 'Technical Failure'),
                ],
                default='not_run', max_length=30,
            ),
        ),
    ]
