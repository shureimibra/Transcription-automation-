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
        # process start, not on every reload of this module or every
        # unrelated management command.
        #
        # ORIGINALLY GATED ON `runserver` ALONE, WHICH NEVER RUNS IN
        # PRODUCTION. `runserver` is Django's own dev server; production
        # serves via a standalone `daphne` process (its own systemd unit,
        # `shurz-daphne`, proxied by nginx -- confirmed by that unit's
        # ExecStart, never `manage.py runserver`) and the warm pool via
        # `manage.py eagle_cycle --loop` (deploy/eagle-cycle.service). The
        # `runserver`-only gate meant this recovery had *never* actually
        # run in production, on any restart, ever -- confirmed the hard
        # way: restarting eagle-cycle for an unrelated deploy left 3
        # already-orphaned jobs sitting in `transcribing` for 50+ minutes
        # with nothing watching, found only by a manual DB query.
        #
        # `eagle_cycle` is added with certainty -- deploy/eagle-cycle.
        # service's own ExecStart is `manage.py eagle_cycle`, so detecting
        # it via sys.argv is exact. `daphne` is added on a best-effort
        # basis: the standalone `daphne` package installs a console-script
        # of that same name, which is the standard way every Django+
        # Channels deployment guide runs it in production, and matches
        # this service being named `shurz-daphne` rather than
        # `shurz-runserver` -- but no `deploy/shurz-daphne.service` is
        # committed to this repo to confirm the exact invocation against,
        # so if production's actual command differs from a plain `daphne
        # ...` script call, this detection silently misses it. That is
        # not a regression -- production had zero coverage before this
        # change -- just an incompleteness worth knowing about.
        #
        # Guarded the same three ways as before, restructured so each
        # applies to the right entry point:
        #  1. None of runserver/eagle_cycle/daphne -- never during
        #     migrate/makemigrations/test/shell/other one-off commands,
        #     where either the DB isn't ready yet or we'd be mutating real
        #     rows as an unwanted side effect of an unrelated command
        #     (confirmed: `manage.py test` would otherwise touch the
        #     production db.sqlite3, not the isolated test DB, since
        #     ready() fires before the test runner swaps databases).
        #  2. RUN_MAIN check -- SCOPED TO runserver ONLY. Its autoreloader
        #     is the only one of the three with a watcher/worker split
        #     (RUN_MAIN is set only in the spawned worker); eagle_cycle
        #     and daphne have no such split and never set RUN_MAIN at
        #     all, so applying this check to them unconditionally would
        #     always be false and silently skip recovery every time --
        #     exactly the bug this whole change exists to fix.
        #  3. try/except around the DB work itself -- startup recovery
        #     must never be the reason the process fails to start.
        argv = sys.argv
        is_runserver = 'runserver' in argv
        is_eagle_cycle = 'eagle_cycle' in argv
        # The FULL argv[0], not just its basename: covers both the
        # `daphne` console-script directly (.../bin/daphne) and `python -m
        # daphne`, where argv[0] resolves to .../daphne/__main__.py --
        # basename alone would miss the latter.
        is_daphne = bool(argv) and 'daphne' in argv[0].lower()

        if not (is_runserver or is_eagle_cycle or is_daphne):
            return
        if is_runserver and os.environ.get('RUN_MAIN') != 'true':
            return
        try:
            from .job_cleanup import recover_orphaned_jobs
            recover_orphaned_jobs()
        except Exception as e:
            logger.warning(f"Startup orphaned-job recovery failed (server is starting anyway): {e}")

        # Pre-warm speaker embedding model in background daemon thread.
        # Unchanged in scope -- runserver only, as before this change --
        # this fix is about recovery, not about widening what else warms.
        if is_runserver:
            def _prewarm_diarization():
                try:
                    from core.services.diarization import SpeakerDiarizer
                    SpeakerDiarizer._load_embedding_model()
                except Exception as e:
                    logger.debug(f"Background diarization pre-warm skipped/failed: {e}")

            import threading
            threading.Thread(target=_prewarm_diarization, daemon=True, name="diarization-prewarm").start()
