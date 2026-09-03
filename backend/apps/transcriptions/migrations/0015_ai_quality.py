"""
Quality pipeline persistence.

Additive only, and every added column has a default -- so this applies to an
existing database with live revisions in it without a data migration and
without touching a single existing row's meaning. `quality_status` defaults
to 'not_run', which is the honest description of every revision that
predates the gate: it was never assessed, and it must not be presented as
though it passed.
"""
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('transcriptions', '0014_airevision_cache_hits_airevision_cache_misses_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='airevision',
            name='quality_profile',
            field=models.CharField(blank=True, default='', max_length=30),
        ),
        migrations.AddField(
            model_name='airevision',
            name='quality_status',
            field=models.CharField(
                choices=[
                    ('not_run', 'No quality pipeline'),
                    ('validated', 'Validated'),
                    ('quality_review_required', 'Quality Review Required'),
                    ('technical_failure', 'Technical Failure'),
                ],
                default='not_run', max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='airevision',
            name='quality_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='airevision',
            name='qa_iterations',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='quality_confidence',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='critical_issue_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='high_issue_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='medium_issue_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='low_issue_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='airevision',
            name='detected_domain',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='airevision',
            name='detected_register',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='airevision',
            name='quality_prompt_version',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddIndex(
            model_name='airevision',
            index=models.Index(
                fields=['job', 'quality_status'], name='ai_rev_job_qstatus_idx',
            ),
        ),
        migrations.CreateModel(
            name='AIQualityReport',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4, editable=False, primary_key=True, serialize=False,
                )),
                ('profile', models.CharField(blank=True, default='', max_length=30)),
                ('status', models.CharField(blank=True, default='', max_length=30)),
                ('reason', models.TextField(blank=True, default='')),
                ('iterations', models.PositiveIntegerField(default=0)),
                ('confidence', models.FloatField(default=0.0)),
                ('model_name', models.CharField(blank=True, default='', max_length=100)),
                ('prompt_version', models.CharField(blank=True, default='', max_length=20)),
                ('total_tokens', models.PositiveIntegerField(default=0)),
                ('total_latency_seconds', models.FloatField(default=0.0)),
                ('report', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('revision', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='quality_reports',
                    to='transcriptions.airevision',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='aiqualityreport',
            index=models.Index(
                fields=['revision', 'created_at'], name='ai_qrep_rev_created_idx',
            ),
        ),
    ]
