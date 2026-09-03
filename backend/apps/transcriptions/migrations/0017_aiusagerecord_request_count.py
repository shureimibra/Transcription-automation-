"""
Adds the provider-call count to the usage ledger.

Defaults to 1, which is exactly what every existing row means: the primary
pass writes one row per batch, and a batch is one request. Only the quality
gate needs to say otherwise, because it writes one row for a run that may
have made several calls.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transcriptions', '0016_quality_progress_states'),
    ]

    operations = [
        migrations.AddField(
            model_name='aiusagerecord',
            name='request_count',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
