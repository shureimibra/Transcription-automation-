import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TranscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.transcriptions'

    def ready(self):
        # Self-healing for jobs orphaned by a server restart mid-pipeline
        # (see job_cleanup.recover_orphaned_jobs) -- runs once per real
        # server start, not on every reload of this module or every
        # unrelated management command. Guarded three ways:
        #  1. Only for `runserver` -- never during migrate/makemigrations/
        #     test/shell, where either the DB isn't ready yet or we'd be
        #     mutating real rows as an unwanted side effect of an unrelated
        #     command (confirmed: `manage.py test` would otherwise touch
        #     the production db.sqlite3, not the isolated test DB, since
        #     ready() fires before the test runner swaps databases).
        #  2. RUN_MAIN check -- `runserver`'s autoreloader spawns a child
        #     process and calls ready() in BOTH the watcher and the
        #     spawned worker; RUN_MAIN is only set in the latter, so this
        #     runs exactly once per actual server process instead of twice.
        #  3. try/except around the DB work itself -- startup recovery
        #     must never be the reason the server fails to start.
        if 'runserver' not in sys.argv:
            return
        if os.environ.get('RUN_MAIN') != 'true':
            return
        try:
            from .job_cleanup import recover_orphaned_jobs
            recover_orphaned_jobs()
        except Exception as e:
            logger.warning(f"Startup orphaned-job recovery failed (server is starting anyway): {e}")

        # Pre-warm speaker embedding model in background daemon thread
        def _prewarm_diarization():
            try:
                from core.services.diarization import SpeakerDiarizer
                SpeakerDiarizer._load_embedding_model()
            except Exception as e:
                logger.debug(f"Background diarization pre-warm skipped/failed: {e}")

        import threading
        threading.Thread(target=_prewarm_diarization, daemon=True, name="diarization-prewarm").start()
