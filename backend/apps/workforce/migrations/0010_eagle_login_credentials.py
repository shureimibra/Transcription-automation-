# Generated for Eagle username/password auto-login (encrypted, optional --
# a credential with only a cookie keeps working unmodified).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workforce', '0009_task_deadline_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='eaglecredential',
            name='username_encrypted',
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='eaglecredential',
            name='password_encrypted',
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='eaglecredential',
            name='last_login_failure_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
