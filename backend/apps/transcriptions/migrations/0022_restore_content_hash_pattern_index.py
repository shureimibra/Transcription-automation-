from django.db import migrations, models


def create_index_concurrently(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS '
                'ai_seg_cache_hash_like_idx '
                'ON transcriptions_aisegmentcache (content_hash varchar_pattern_ops);'
            )
        else:
            # SQLite (local dev/tests): no CONCURRENTLY concept, no
            # pattern-ops distinction -- a plain index is fine here.
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS ai_seg_cache_hash_like_idx '
                'ON transcriptions_aisegmentcache (content_hash);'
            )


def drop_index(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute('DROP INDEX CONCURRENTLY IF EXISTS ai_seg_cache_hash_like_idx;')
        else:
            cursor.execute('DROP INDEX IF EXISTS ai_seg_cache_hash_like_idx;')


class Migration(migrations.Migration):
    """
    Restores the content_hash pattern-ops index dropped as collateral
    damage by migration 0021 (see that migration's own note, and this
    model's Meta.indexes comment) -- it measured 6,348 real scans against
    pg_stat_user_indexes, not zero, and should never have been removed.
    Only its sibling, the plain btree variant (genuinely 0 scans), was
    meant to go.

    CONCURRENTLY this time, unlike 0021's plain DROP INDEX. DROP does not
    scan the table; CREATE does, to build the index -- and this table is
    live and actively growing (~70K rows and climbing) in production, so
    a normal CREATE INDEX would hold a lock for the duration of that scan.
    CONCURRENTLY cannot run inside a transaction, hence atomic = False and
    the SeparateDatabaseAndState split: Django's own migration-state
    bookkeeping tracks the index normally either way, independent of how
    it is actually built against Postgres.
    """

    atomic = False

    dependencies = [
        ('transcriptions', '0021_drop_unused_content_hash_index'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='aisegmentcache',
                    index=models.Index(
                        fields=['content_hash'],
                        name='ai_seg_cache_hash_like_idx',
                        opclasses=['varchar_pattern_ops'],
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(create_index_concurrently, drop_index),
            ],
        ),
    ]
