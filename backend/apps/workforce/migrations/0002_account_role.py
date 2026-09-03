"""
Adds the explicit account role, and backfills it from what was previously
inferred.

THE BACKFILL IS THE POINT. Before this migration a person's role was implied
by two unrelated facts -- `user.is_staff` for administrators, and the
existence of an active WorkerProfile for workers. Creating the column with
its default alone would silently demote every existing worker and
administrator to TOOL, locking the team out of their own queues on deploy.

The mapping preserves what the old inference meant:

  is_staff or is_superuser        -> ADMIN
  has a WorkerProfile             -> TASKER   (regardless of suspension --
                                    suspension is a STATUS, not a role, and
                                    a suspended worker must come back as a
                                    worker, not as a tool user)
  anything else                   -> TOOL

The reverse operation deletes the rows, so this migration is reversible; the
information it adds was derivable before it ran, and would be derivable
again by the same rules.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def backfill_roles(apps, schema_editor):
    Account = apps.get_model('workforce', 'Account')
    WorkerProfile = apps.get_model('workforce', 'WorkerProfile')
    User = apps.get_model('auth', 'User')

    worker_user_ids = set(WorkerProfile.objects.values_list('user_id', flat=True))
    existing = set(Account.objects.values_list('user_id', flat=True))

    new_accounts = []
    for user in User.objects.all().iterator():
        if user.id in existing:
            continue
        if user.is_staff or user.is_superuser:
            role = 'admin'
        elif user.id in worker_user_ids:
            role = 'tasker'
        else:
            role = 'tool'
        new_accounts.append(Account(user_id=user.id, role=role))

    if new_accounts:
        Account.objects.bulk_create(new_accounts)


def drop_roles(apps, schema_editor):
    apps.get_model('workforce', 'Account').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('workforce', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Account',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(choices=[('admin', 'Administrator'), ('tasker', 'Tasker'), ('tool', 'Tool user')], db_index=True, default='tool', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='workforce_account', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['user__username'],
            },
        ),
        migrations.RunPython(backfill_roles, drop_roles),
    ]
