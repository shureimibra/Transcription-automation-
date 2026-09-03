import logging
import os
from typing import Any, Dict, List

from django.db import transaction

logger = logging.getLogger(__name__)


class JobCleanupService:
    """
    Deletes a TranscriptionJob and everything associated with it.

    Database: TranscriptSegment, Translation, TranslatedSegment, AcademicFormat,
    and AcademicSegment already CASCADE-delete via their FK/OneToOne to
    TranscriptionJob (and TranslatedSegment/AcademicSegment cascade from
    Translation/AcademicFormat respectively), so a plain instance.delete()
    already handles those correctly -- this service adds what the ORM's
    cascade can't reach: leftover temp audio files on disk, and any
    DiscoveryTask that discovered this exact URL and has no other reason to
    stick around.

    Filesystem cleanup happens BEFORE the database delete and is always
    best-effort: a file that can't be removed (e.g. still locked by an
    OS handle) is logged as orphaned rather than blocking the deletion, so
    a stuck file never leaves the database in a partially-deleted state.
    The database delete itself runs inside one atomic transaction, so it's
    all-or-nothing -- either the job and its DB-cascaded children are all
    gone, or none of them are.

    Deliberately one reusable static method, not tied to being called from
    a view -- a future scheduled expiration policy can call this exact
    same delete_job() on old jobs without any rewrite, just a different
    caller (e.g. a management command iterating a queryset).
    """

    @staticmethod
    def _temp_file_paths(job) -> List[str]:
        # Mirrors tasks.py's run_transcription_pipeline exactly: temp_wav_path
        # = os.path.abspath(f"job_{job_id}_temp.wav"), plus the ".temp"
        # intermediate download uses before FFmpeg extraction completes.
        base = os.path.abspath(f"job_{job.id}_temp.wav")
        paths = [base, base + ".temp"]
        # Uploaded original file (job.input_source == 'upload'): written
        # directly by TranscriptionJobViewSet.create() to its own OS-temp
        # path, entirely separate from the job_<id>_temp.wav naming above.
        # The pipeline's own finally block (see tasks.py) already removes
        # this the moment a job reaches completed/failed/cancelled, so by
        # the time a user clicks Delete it's normally already gone -- this
        # is the defense-in-depth path for a job that never got that far
        # (e.g. still queued, or the process died before the pipeline's
        # finally block ran).
        if job.input_source == 'upload' and job.file_path:
            paths.append(job.file_path)
        return paths

    @classmethod
    def delete_job(cls, job) -> Dict[str, Any]:
        """
        job: an unsaved-for-deletion TranscriptionJob instance (the caller
        still owns fetching/authorizing it). Returns a summary dict:
        {'files_removed': [...], 'files_orphaned': [...], 'discovery_tasks_removed': int}.
        """
        job_id = job.id
        source_url = job.source_url

        files_removed: List[str] = []
        files_orphaned: List[str] = []
        for path in cls._temp_file_paths(job):
            if not os.path.exists(path):
                continue
            try:
                os.remove(path)
                files_removed.append(path)
            except OSError as e:
                logger.warning(
                    f"Job {job_id}: could not remove temp file '{path}' during deletion "
                    f"(leaving it orphaned on disk, database deletion still proceeds): {e}"
                )
                files_orphaned.append(path)

        discovery_tasks_removed = 0
        with transaction.atomic():
            if source_url:
                # DiscoveryTask has no FK to TranscriptionJob (items it
                # discovers become independent jobs, never referencing it
                # back), so "orphaned" here means best-effort: any
                # DiscoveryTask that was asked about this job's exact
                # source_url. Deleting it is always safe -- it never
                # cascades to or affects already-created jobs, which only
                # ever used its `result` at creation time.
                from .models import DiscoveryTask
                discovery_tasks_removed, _ = DiscoveryTask.objects.filter(source_url=source_url).delete()

            job.delete()

        logger.info(
            f"Job {job_id} deleted: {len(files_removed)} temp file(s) removed, "
            f"{len(files_orphaned)} orphaned, {discovery_tasks_removed} DiscoveryTask row(s) removed."
        )
        return {
            'files_removed': files_removed,
            'files_orphaned': files_orphaned,
            'discovery_tasks_removed': discovery_tasks_removed,
        }


# Statuses that only ever exist while a background thread is actively
# working on something. Nothing outside that thread ever transitions a row
# out of one of these -- so a row sitting in one of them the moment a
# *fresh* server process starts can only mean the thread that owned it
# died with the previous process (e.g. an autoreload restart mid-job) and
# never got to report completion, failure, or cancellation. There's no way
# to distinguish "genuinely still running" from "orphaned" from inside a
# brand new process, since the only thing that could ever still be running
# it was the old one -- so anything in these statuses at startup is, by
# construction, orphaned.
ORPHANED_JOB_STATUSES = ['queued', 'downloading', 'transcribing']
ORPHANED_TRANSLATION_STATUSES = ['queued', 'translating']
ORPHANED_DISCOVERY_STATUSES = ['pending']
ORPHANED_ACADEMIC_FORMAT_STATUSES = ['queued', 'detecting', 'formatting', 'validating']


def recover_orphaned_jobs() -> Dict[str, int]:
    """
    Called once per server process start (see TranscriptionsConfig.ready()).
    Without this, a job/translation/discovery task interrupted by a server
    restart sits "in progress" forever -- nothing else ever marks it
    failed, so the frontend (including session recovery) would poll it
    indefinitely with no process behind it, indistinguishable from a real
    stall. Marks each one failed with an honest, specific reason instead,
    and best-effort removes whatever temp files that job's own pipeline
    would have cleaned up had it gotten the chance to finish. Never
    deletes the rows themselves -- same "stays visible in history"
    principle as a cancelled job.
    """
    from .models import TranscriptionJob, Translation, DiscoveryTask, AcademicFormat

    orphaned_jobs = list(TranscriptionJob.objects.filter(status__in=ORPHANED_JOB_STATUSES))
    for job in orphaned_jobs:
        for path in JobCleanupService._temp_file_paths(job):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"Startup recovery: could not remove temp file '{path}' for job {job.id}: {e}")

    jobs_recovered = TranscriptionJob.objects.filter(id__in=[j.id for j in orphaned_jobs]).update(
        status='failed',
        error_message='Interrupted by a server restart before this job could finish. Please resubmit.',
    )
    translations_recovered = Translation.objects.filter(status__in=ORPHANED_TRANSLATION_STATUSES).update(
        status='failed',
        error_message='Interrupted by a server restart before this translation could finish. Please retry.',
    )
    discovery_tasks_recovered = DiscoveryTask.objects.filter(status__in=ORPHANED_DISCOVERY_STATUSES).update(
        status='failed',
        error_message='Interrupted by a server restart before discovery could finish. Please retry.',
    )
    academic_formats_recovered = AcademicFormat.objects.filter(status__in=ORPHANED_ACADEMIC_FORMAT_STATUSES).update(
        status='failed',
        error_message='Interrupted by a server restart before academic formatting could finish. Please retry.',
    )

    if jobs_recovered or translations_recovered or discovery_tasks_recovered or academic_formats_recovered:
        logger.warning(
            f"Startup recovery: marked {jobs_recovered} orphaned job(s), "
            f"{translations_recovered} orphaned translation(s), "
            f"{discovery_tasks_recovered} orphaned discovery task(s), and "
            f"{academic_formats_recovered} orphaned academic format(s) as failed "
            f"(interrupted by a previous server run)."
        )

    return {
        'jobs_recovered': jobs_recovered,
        'translations_recovered': translations_recovered,
        'discovery_tasks_recovered': discovery_tasks_recovered,
        'academic_formats_recovered': academic_formats_recovered,
    }
