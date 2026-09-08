"""
Pre-processing tasks BEFORE they are claimed.

WHY THIS EXISTS -- THE ARITHMETIC. Eagle gives one hour from claim to
submission. Process after claiming and a 25-minute file spends ~19 minutes in
ASR plus diarization plus AI assist, leaving barely half the clock for a human
to review 25 minutes of audio. Run six workers that way, on unbounded
concurrency, and the pipelines stretch each other three- to six-fold: all six
miss the deadline. Processing before the claim moves every machine-minute
outside the clock, so the hour is spent on human review instead of waiting.

WHAT IT COSTS TO BE WRONG. If another platform worker takes a task we warmed,
we lose CPU. We do not lose money: minutes are spent at CLAIM, never here.
That is the right side to waste, and it is why warming is generous and
claiming is not.

CONCURRENCY IS BOUNDED HERE, DELIBERATELY. The rest of the engine still
launches a thread per request (the Phase 1 finding); this path does not.
`EAGLE_WARM_CONCURRENCY` is the number of model-bearing pipelines allowed to
run at once, because at roughly 750 MB each that number is a memory budget
as much as a speed setting.

CREDENTIALS STOP AT THIS FILE. Media is fetched server-side and written to a
local cache; the resulting TranscriptionJob points at a local path and its
`site_auth_headers` stays empty. No cookie is copied per job the way the
legacy path did on 105 rows.
"""
import datetime as dt
import logging
import os
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from . import states
from .models import Task

logger = logging.getLogger('eagle')

#: How many drafts to keep ready. Six workers times two is enough that nobody
#: waits and the machine is not permanently pegged.
DEFAULT_POOL_SIZE = int(os.environ.get('EAGLE_WARM_POOL_SIZE', '12'))

#: Concurrent pipelines. A memory budget: ~750 MB of models each.
DEFAULT_CONCURRENCY = int(os.environ.get('EAGLE_WARM_CONCURRENCY', '2'))

#: How many tasks per cycle may spend Gemini quota.
#:
#: THE TWO STAGES COST NOTHING ALIKE. Stage A -- fetch plus ASR plus
#: diarization -- runs on local models: slow, but free and unmetered. Stage B
#: is the standard pass or the translation, which is 2-3 provider calls for a
#: translation and 5-14 for a transcription, against a metered free tier.
#:
#: WHY THE SHORTEST. Task 4705 is the argument: a 20-minute file, warmed end
#: to end, taken by another company's tasker 102 seconds after its French
#: landed. The platform's pool is shared and drains fast -- 75%% of one day's
#: translation went to other people within four hours -- so quota spent on a
#: long file is the likeliest to be spent on work we never get to do. Short
#: tasks are claimed sooner, finish inside the hour more easily, and turn a
#: worker-hour into more completed tasks.
#:
#: Stage A still runs on EVERYTHING, because it is free and it is the slow
#: part: a tasker who claims a long file still finds a transcript waiting,
#: which is what warming exists to provide.
DEFAULT_AI_LIMIT = int(os.environ.get('EAGLE_WARM_AI_LIMIT', '12'))

#: How long cached audio outlives the task that needed it.
AUDIO_RETENTION_DAYS = int(os.environ.get('EAGLE_AUDIO_RETENTION_DAYS', '4'))

#: Audio for a task we never claimed is dead weight almost immediately.
LOST_RACE_RETENTION_HOURS = int(os.environ.get('EAGLE_LOST_AUDIO_HOURS', '1'))

DOWNLOAD_TIMEOUT = 180


def cache_dir() -> str:
    path = getattr(settings, 'EAGLE_MEDIA_CACHE_DIR', None) or os.path.join(
        str(settings.BASE_DIR), 'media_cache')
    os.makedirs(path, exist_ok=True)
    return path


def cache_path_for(task: Task) -> str:
    """Deterministic, so a restart mid-cycle re-uses what was downloaded."""
    suffix = '.mp3'
    return os.path.join(
        cache_dir(), f'{task.external_source or "task"}-{task.external_task_id or task.id}{suffix}')


@dataclass
class WarmReport:
    considered: int = 0
    already_warm: int = 0
    fetched: int = 0
    processed: int = 0
    stage_a_only: int = 0
    deferred: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self):
        return {
            'considered': self.considered, 'already_warm': self.already_warm,
            'fetched': self.fetched, 'processed': self.processed,
            'stage_a_only': self.stage_a_only, 'deferred': self.deferred,
            'failed': self.failed, 'errors': self.errors,
        }


# --------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------- #

def fetch_media(task: Task, service=None) -> str:
    """
    Download a task's audio to the local cache. Returns the path.

    Uses the credential-bearing door ONLY when the platform requires it --
    transcription audio is served with no credentials at all, and asking for
    authenticated media there would attach a cookie for no reason.
    """
    from .eagle import get_eagle_service

    service = service or get_eagle_service()
    destination = cache_path_for(task)

    if os.path.exists(destination) and os.path.getsize(destination) > 0:
        return destination

    # Ask the platform ONCE whether its media is public. Not inferred from
    # the product name: that transcription serves audio openly and
    # translation does not is an observed fact today, not a guarantee, and
    # the task record already carries the answer.
    external = service.get_task(task.external_task_id, task.external_source)
    needs_session = bool((external.raw or {}).get('media_needs_session', True))

    if needs_session:
        media = service.get_authenticated_media(task.external_task_id, task.external_source)
        headers = media.headers or {}
    else:
        media = service.get_task_media(task.external_task_id, task.external_source)
        headers = {}

    request = urllib.request.Request(media.url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
        **headers,
    })

    partial = destination + '.part'
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, \
                open(partial, 'wb') as handle:
            shutil.copyfileobj(response, handle, length=1024 * 256)
        # Rename only once complete, so an interrupted download is never
        # mistaken for a cached file on the next cycle.
        os.replace(partial, destination)
    finally:
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass

    return destination


def _working_copy(cache_path: str, job_id) -> str:
    """
    A throwaway path for the pipeline to consume.

    THE PIPELINE DELETES `job.file_path` WHEN IT FINISHES -- deliberately,
    because for a browser upload that file is a one-shot. Pointing it at the
    cache directly would therefore destroy the audio the worker still needs
    to review against. A hard link costs nothing and gives the pipeline its
    own name to unlink, leaving the cached bytes intact; a copy is the
    fallback where linking is unavailable.
    """
    working = os.path.join(cache_dir(), f'work-{job_id}{os.path.splitext(cache_path)[1]}')
    if os.path.exists(working):
        return working
    try:
        os.link(cache_path, working)
    except (OSError, AttributeError):
        shutil.copyfile(cache_path, working)
    return working


# --------------------------------------------------------------------- #
# Warming
# --------------------------------------------------------------------- #

def is_warm(task: Task) -> bool:
    """
    Warm means a tasker could take this RIGHT NOW and start reviewing.

    FOR A TRANSLATION THAT MEANS THE TRANSLATION EXISTS, not merely the
    transcript. The two are not the same thing and treating them as the same
    thing deadlocks the pool:

      * a translation whose translate pass failed still has a completed
        transcript attached
      * so it counted as warm
      * so it filled a slot in the pool
      * so the pool reported itself full and stopped warming anything
      * so the failed translation was never retried

    Three half-finished tasks were enough to stop the warm pool entirely,
    while every log line said `[warm] ready=3 processed=0` -- which reads
    like everything is fine.

    This is the same question `TaskSerializer.get_is_ready` answers for the
    queue badge, and the two must agree: a task the queue advertises as
    "Ready now" is one the pool should count, and nothing else.
    """
    if not (task.job_id and task.job and task.job.status == 'completed'):
        return False
    if task.kind == Task.TRANSLATION:
        return bool(task.deliverable_revision_id
                    or (task.polished_text or '').strip())
    return True


def select_candidates(limit: int, product: Optional[str] = None) -> List[Task]:
    """
    Which unclaimed tasks to warm next.

    Shortest first. A 2-minute task costs a fraction of the machine time of
    a 25-minute one, clears the deadline trivially, and turns a worker-hour
    into more completed tasks -- so when the pool has room, short work is
    strictly better to have ready.
    """
    # NOT-YET-WARM, WHICH IS NOT THE SAME AS NEVER-STARTED.
    #
    # This used to select only tasks with no job at all, so a translation
    # whose translate pass failed -- transcript attached, translation
    # missing -- could never be picked up again. Combined with is_warm
    # counting it as done, three of them stopped the pool completely.
    #
    # A job still PROCESSING is deliberately not a candidate: the pipeline
    # has it, and starting a second one would race the first.
    # A FAILED JOB MUST BE RETRYABLE, and this is the same deadlock as the
    # one above wearing a different coat. `is_warm` says no (the job is not
    # completed) and the candidate query used to say no as well (the job is
    # not null), so a task whose pipeline failed was warm to nobody and a
    # candidate for nothing -- stranded for good.
    #
    # It is not hypothetical. FFmpeg resolves through the installing user's
    # AppData, the scheduled task runs as SYSTEM, and for one afternoon
    # every pipeline died on 'FFmpeg is not installed'. That stranded 42 of
    # the 46 open tasks; fixing FFmpeg alone would have left every one of
    # them poisoned, because nothing would ever pick them up again.
    queryset = Task.objects.filter(
        status=states.NEW, claimed_by__isnull=True,
    ).exclude(external_task_id='').filter(
        Q(job__isnull=True)
        | Q(job__status='failed')
        | Q(kind=Task.TRANSLATION, job__status='completed',
            deliverable_revision__isnull=True, polished_text='')
    )
    if product:
        queryset = queryset.filter(external_source=product)

    # RETRIES GO BEHIND FRESH WORK. There is no attempt counter, so a task
    # that fails for a reason we cannot fix -- a corrupt file rather than a
    # missing binary -- would otherwise come back every cycle and, being
    # short, sort straight to the front and spend the pool on itself. Fresh
    # tasks first means a permanently broken one only ever consumes
    # capacity nothing else wanted.
    queryset = queryset.annotate(
        _is_retry=Case(When(job__isnull=True, then=Value(0)),
                       default=Value(1), output_field=IntegerField()),
    )
    return list(queryset.order_by('_is_retry', 'duration_seconds', 'created_at')[:limit])


def warm_task(task: Task, service=None, allow_ai: bool = True) -> bool:
    """
    Fetch the audio and run the pipeline for one unclaimed task.

    Claims NOTHING. On failure the task stays NEW and claimable -- a worker
    can still take it and process it inside the clock, which is slower but
    not broken.

    `allow_ai=False` runs STAGE A ONLY: fetch, ASR and diarization, all of
    it local and free. The provider passes are left for a later cycle. See
    DEFAULT_AI_LIMIT for why the expensive half is rationed by duration.
    """
    from apps.transcriptions.models import Project, TranscriptionJob
    from apps.transcriptions.tasks import run_transcription_pipeline

    # RESUME RATHER THAN RESTART. A translation whose translate pass failed
    # comes back here with a perfectly good transcript already attached.
    # Running the ASR again would spend minutes of CPU reproducing work that
    # is sitting right there, and would throw away any correction a standard
    # pass already made to it.
    if (task.job_id and task.job and task.job.status == 'completed'
            and task.kind == Task.TRANSLATION):
        # Defensive: run_cycle does not schedule this combination, because a
        # translation that already has its transcript has nothing left that
        # Stage A could do for it.
        if not allow_ai:
            return False
        logger.info('[WARM] %s already has a transcript; translating only',
                    task.external_task_id)
        return _warm_translation(task, task.job)

    cache_path = fetch_media(task, service=service)

    project, _ = Project.objects.get_or_create(
        name='Eagle', defaults={'description': 'Tasks mirrored from Eagle.'})

    job = TranscriptionJob.objects.create(
        project=project,
        input_source='upload',
        file_path=_working_copy(cache_path, task.id),
        language=(task.source_language or None),
        # Speaker identification IS the graded work on Eagle transcription
        # tasks, so diarization is not optional there.
        enable_diarization=(task.kind == Task.TRANSCRIPTION),
        # Deliberately empty. The session stayed in the credential store;
        # the audio is already local.
        site_auth_headers=None,
    )
    Task.objects.filter(pk=task.pk).update(job=job)

    run_transcription_pipeline(job.id)

    job.refresh_from_db()
    if job.status != 'completed':
        return False

    # THE HEADER NEEDS A SUBJECT, and only a transcription submission shows
    # one -- Eagle's translation header carries no title.
    if task.kind == Task.TRANSCRIPTION and not (task.topic or '').strip():
        topic = generate_topic(job)
        if topic:
            Task.objects.filter(pk=task.pk).update(topic=topic)
            logger.info('[WARM] %s titled %r', task.external_task_id, topic)

    if task.kind == Task.TRANSLATION:
        # NO SEPARATE COACH PASS ON A TRANSLATION. It was here on the
        # reasoning that translating uncorrected ASR propagates its mistakes
        # -- which is true, but it is the most expensive step we run and its
        # English output never ships: only the French does.
        #
        # Measured on real tasks: the coach pass costs about as much again
        # as the translation itself, and roughly doubles the requests a
        # warm task spends. On a free-tier quota that is the difference
        # between fitting the day's work and not.
        #
        # The cleanup is not lost. It moves into the translation prompt
        # (the source is raw ASR; ignore disfluency) where it costs nothing
        # extra, and into the tasker's own polish step, which is where the
        # French is brought to submission standard anyway.
        #
        # `EAGLE_WARM_STANDARD_ON_TRANSLATION=1` restores the old behaviour
        # for anyone who would rather spend the quota.
        if not allow_ai:
            # The transcript is banked and survives to the next cycle, where
            # the translation can be picked up without redoing the ASR.
            logger.info('[WARM] %s transcribed; translation deferred',
                        task.external_task_id)
            return True
        if getattr(settings, 'EAGLE_WARM_STANDARD_ON_TRANSLATION', False):
            apply_transcription_standard(task, job)
        return _warm_translation(task, job)

    # Transcription is different: the transcript IS the deliverable, so the
    # standard has to be applied to it and there is nothing downstream that
    # would otherwise do it.
    if not allow_ai:
        # A raw ASR transcript is still a claimable head start -- which is
        # exactly what a worker got before the standard pass existed, and
        # what they get today whenever it fails.
        logger.info('[WARM] %s transcribed; standard pass deferred',
                    task.external_task_id)
        return True

    apply_transcription_standard(task, job)
    return True


def await_ai_capacity(timeout: float = 180.0, poll: float = 5.0) -> bool:
    """
    Block until the AI rate-limit window has room, or give up.

    WHY WARMING WAITS INSTEAD OF FAILING. `ai_budget.check_rate_limit` is a
    DISPATCH gate, written to stop a person hammering the AI Assist button
    from queueing twenty overlapping revisions. For a person that is the
    right answer: tell them no, now. The warm pool is not a person. It runs
    in the background against tasks nobody is waiting on, so refusing it is
    the worst of both worlds -- the revision is marked failed, the task
    stays cold, and the queue shows no ready work even though there was
    plenty of daily budget left.

    That is exactly what happened: 18 requests against a 900/day budget, and
    warming still produced nothing, because two parallel translations
    crossed a 10-per-MINUTE ceiling within seconds of each other.

    Bounded, because a wait with no end is a hang. On timeout the caller
    proceeds and takes the failure it would have taken anyway.
    """
    from apps.transcriptions.ai_budget import check_rate_limit
    from core.services.ai_assist.errors import AIRateLimitedError

    deadline = time.monotonic() + timeout
    waited = False
    while True:
        try:
            check_rate_limit()
            if waited:
                logger.info('[WARM] AI capacity available again; continuing')
            return True
        except AIRateLimitedError:
            if time.monotonic() >= deadline:
                logger.warning('[WARM] still rate limited after %.0fs; '
                               'letting the run take its own failure', timeout)
                return False
            waited = True
            time.sleep(poll)
        except Exception:
            # A budget module that cannot answer must not stop warming.
            return True


def generate_topic(job, max_words: int = 400) -> str:
    """
    What this recording is about, in a few words, for the header.

    WHY IT IS NOT THE FILENAME. Eagle names its media "C 64.mp3", and the
    submissions the platform accepted carry a real subject line instead --
    "Transcription Job: How to Build Strong Habits". A filename in that
    position tells a reader nothing and marks the document as unread
    machine output.

    ONE REQUEST, AND ONLY THE OPENING. A title comes from how a talk starts,
    so the first few hundred words are enough; sending twenty-five minutes
    of transcript would cost several times as much to answer the same
    question.

    Returns '' on any failure. A missing topic is a worse header, not a
    failed task, and warming must not fall over on a cosmetic step.
    """
    words = []
    for segment in job.segments.all().order_by('start_time'):
        words.extend((segment.text or '').split())
        if len(words) >= max_words:
            break
    opening = ' '.join(words[:max_words]).strip()
    if not opening:
        return ''

    try:
        # THROUGH THE FACTORY, because building the provider by hand here
        # quietly opted this call out of everything the factory does.
        #
        # It read GEMINI_API_KEY -- the SINGULAR form -- so an installation
        # that set only GEMINI_API_KEYS got an empty string and no titles at
        # all, silently. And passing a lone api_key with no `keys` left the
        # provider with a one-account pool, so neither the cross-account
        # rotation nor the model fallback chain could engage: one account,
        # one model, and a 429 ended it.
        from core.services.ai_assist.providers import (
            AINotConfiguredError, build_provider,
        )
        try:
            provider = build_provider()
        except AINotConfiguredError:
            return ''
        schema = {
            'type': 'object',
            'properties': {'title': {'type': 'string'}},
            'required': ['title'],
        }
        parsed, _ = provider.generate_json(
            'You write titles for transcripts. Reply with JSON only.',
            'Give a short descriptive title for the recording this transcript '
            'opens. Three to eight words, title case, no quotation marks, no '
            'trailing punctuation. Describe the SUBJECT, not the format: '
            '"How to Build Strong Habits", not "Lecture Transcript".\n\n'
            + opening,
            schema,
        )
        title = str((parsed or {}).get('title') or '').strip().strip('"\'')
        # A model asked for eight words occasionally writes a sentence.
        return title[:200] if 0 < len(title.split()) <= 14 else ''
    except Exception as exc:
        logger.info('[WARM] could not generate a topic: %s', exc)
        return ''


def standard_instruction(standard) -> str:
    """
    Eagle's rubric, written as an instruction the model can follow.

    DERIVED FROM THE STANDARD'S FLAGS, not hand-written per standard. The
    two rubrics invert each other on almost every rule, so a hand-written
    pair drifts: someone edits the edited-verbatim wording and the strict
    version silently keeps saying the opposite of what its flags claim.
    """
    lines = [
        'Apply the {0} transcription standard to this transcript.'.format(
            standard.label),
        'Return the same speech, corrected only as the rules below require.',
        'Never add, summarize, explain, or remove content.',
    ]
    rules = [
        (standard.remove_fillers,
         'Remove filler words and false starts (um, uh, you know, I mean).',
         'Keep every filler word, false start and repetition exactly as '
         'spoken. Write backchannels in their standard spellings: '
         'uh-huh, mm-hmm, uh, um -- consistently, every time.'),
        (standard.expand_contractions,
         # THE EXAMPLES ARE THE INSTRUCTION.
         #
         # This rule used to read "Expand contractions: do not becomes the
         # full form, never the short one." Every word of that is true and it
         # contains no contraction, so it demonstrates nothing -- "do not
         # becomes the full form" is circular, since "do not" IS the full
         # form. Measured on three lecture transcripts, the pass changed one
         # segment in fifty and left 22 contractions standing, which the
         # submission gate then reported as a HIGH finding.
         #
         # A rule about contractions has to be written with contractions in
         # it. The codebase avoids them in prose; a prompt is not prose.
         "Expand EVERY contraction to its full form. don't -> do not, "
         "it's -> it is, we're -> we are, I'm -> I am, they've -> they have, "
         "can't -> cannot, won't -> will not, let's -> let us, "
         "that's -> that is, there's -> there is. No contraction may remain "
         "anywhere in the output.",
         'Keep contractions exactly as spoken; never expand them.'),
        (standard.standardize_slang,
         'Standardize slang and non-standard spellings (gonna becomes going to).',
         'Keep slang and non-standard pronunciations exactly as spoken '
         '(gonna stays gonna).'),
        (standard.correct_grammar,
         'Correct grammar, punctuation and obvious slips of the tongue.',
         'Correct nothing: grammatical errors are part of the record.'),
        # THE TWO BRANCHES WERE THE WRONG WAY ROUND.
        #
        # Every other rule in this table reads (flag_on, do_this, else_this).
        # This one had them swapped, so edited verbatim -- which has
        # record_vocalizations=False -- was being told to RECORD coughs and
        # throat-clearing, and strict verbatim was being told not to. Both
        # exactly backwards, and the coach's own rubric is explicit: edited
        # omits them except where dropping one changes the meaning; strict
        # records them in the text flow.
        (standard.record_vocalizations,
         'Record non-speech sounds in square brackets in the text flow, e.g. '
         '[coughs], [clears throat], [chuckles], [sigh].',
         'Omit non-essential vocalizations such as [sigh], [coughs] and '
         '[clears throat]. Keep a tag such as [laughter] ONLY where dropping '
         'it would obscure sarcasm or change the meaning.'),
    ]
    for enabled, when_on, when_off in rules:
        lines.append('- ' + (when_on if enabled else when_off))

    # Format rules that hold under BOTH standards. They are the most common
    # reason Eagle sends work back, and they are cheap to state.
    lines += [
        '- Never write a name, title or role as a speaker; speakers are '
        'identified only as Male_1, Female_2 and so on.',
        '- Bracket tags, each for its own case: [inaudible] nothing audible, '
        '[unclear] heard but unconfirmed, [crosstalk] speakers overlapping, '
        'plus [pause], [laughter], [applause].',
        # PUNCTUATION CARRIES MEANING HERE, so the three cases are not
        # interchangeable and a reviewer reads them as three different
        # events: the speaker stopped, someone cut them off, or a word broke
        # off part-way.
        '- Trailing off takes an ellipsis (I was going to say...); an '
        'interruption takes an em-dash at the break; a word broken off '
        'part-way takes a hyphen (respons-).',
        # THE GUARD COMES FIRST, AND IT IS WHY THE RULE BELOW CAN EXIST.
        #
        # The number-style rule was removed entirely this morning after the
        # model, primed to "convert numbers", converted things that were not
        # numbers: the variable X came back as "1.95E+3" and the word
        # "knowledge" as "6E+1". But the coach's rubric requires the style in
        # BOTH standards, so deleting it made our output wrong on purpose.
        #
        # It is back, preceded by the prohibition that makes it safe. Stated
        # in this order deliberately: what must never happen, then what to do.
        '- NEVER replace a word with a number, or a number with a different '
        'number. Every figure, date and name must appear unchanged. If a word '
        'is not a number, leave it alone.',
        # SPELLED OUT, BECAUSE THE SHORT VERSION WAS READ BACKWARDS.
        #
        # "write zero through nine as words and 10 upward as digits" produced
        # "three or four" -> "3 or 4", which is the opposite of the rule, and
        # "the 1950s and 60s" -> "the 1950s and 1960s", which changes a
        # value. Each direction is now stated with its own example, and the
        # prohibition on altering a value is repeated where it applies.
        '- Number style, applied ONLY to things that are already numbers:',
        '  * zero to nine stay WORDS. "three or four" stays "three or four" '
        '-- never "3 or 4".',
        '  * 10 and above are DIGITS. "forty-five" becomes "45".',
        '  * money, dates, times, percentages, measurements and ages are '
        'always digits.',
        '  * NEVER change what a number means: "the 1950s and 60s" stays '
        'that, not "1960s". Decades, years and citations copy across '
        'unchanged.',
        '- Use straight quotation marks, never curly ones.',
    ]
    return '\n'.join(lines)


def apply_transcription_standard(task: Task, job) -> bool:
    """
    Bring the raw ASR transcript up to Eagle's rubric, before anyone claims
    the task.

    WHY THE TEXT GOES BACK INTO THE SEGMENTS. A tasker has to be able to
    TYPE corrections, and the only surface that allows that is
    TranscriptSegment -- AIRevisionSegment is accept/reject by design, so a
    version "labelled as the model's output" can never be edited into
    something the model did not say. Leaving the corrected text in the
    revision would therefore mean the worker edits one document and the
    submitter renders another, and the divergence would be silent.

    THIS IS NOT THE PIPELINE MUTATING ITS OWN INPUT. RULE 4 keeps
    run_ai_revision_pipeline read-only over TranscriptSegment, and that still
    holds: the pipeline writes only AIRevisionSegments. This runs afterwards,
    on a task nobody has claimed, and only ever promotes a finished revision
    to be the worker's starting point. The raw ASR survives in
    AIRevisionSegment.original_text, so the promotion is auditable.

    NEVER OVER A WORKER'S EDITS. A claimed task is left alone: warming is for
    tasks in the pool, and racing a person's typing would lose their work.

    Returns True when the standard was applied. Failure is not fatal -- the
    worker gets the raw transcript, which is what they got before this
    existed.
    """
    from apps.transcriptions.models import AIRevision, TranscriptSegment
    from apps.transcriptions.tasks import run_ai_revision_pipeline
    from core.services.ai_assist.quality import get_standard, standard_for_project

    # THE PROJECT DECIDES, NOT THE SETTING.
    #
    # Eagle assigns the standard per task, and the two are opposites: an
    # edited-verbatim pass on a strict-verbatim task strips the fillers, the
    # false starts and the contractions the platform is paying to have kept.
    # That is not a document with some mistakes in it, it is the wrong
    # document -- and no amount of care in the words rescues it.
    #
    # Three accepted transcripts from one transcriber prove the split: two
    # edited, one strict. The mapping from project to standard came from the
    # coach and lives in PROJECT_STANDARDS.
    #
    # The setting survives as the fallback for a task whose project we did
    # not get, which today is all of them -- see `project_from_row`. Most of
    # the work is lectures, so edited verbatim is right far more often than
    # not, and the log below records which of the two happened so a wrong
    # call is findable afterwards rather than mysterious.
    fallback = getattr(settings, 'EAGLE_TRANSCRIPTION_STANDARD', 'edited_verbatim')
    resolved = standard_for_project(getattr(task, 'project', ''), fallback)
    standard = get_standard(resolved)
    if standard is None:
        return False

    if (task.project or '').strip():
        logger.info('[WARM] %s project %r -> %s standard',
                    task.external_task_id, task.project, resolved)
    else:
        logger.info('[WARM] %s has no project label; ASSUMING the %s standard',
                    task.external_task_id, resolved)

    # THE STANDARD PASS GETS ITS OWN PROFILE, not the submission one.
    # `eagle_web` runs a source-fidelity audit that reports this pass's
    # required edits as defects and reverts them; `eagle_standard` keeps the
    # deterministic rubric checks and drops that stage. See the note on
    # EAGLE_STANDARD in quality/profiles.py.
    profile = getattr(settings, 'EAGLE_STANDARD_QUALITY_PROFILE', 'eagle_standard')

    if Task.objects.filter(pk=task.pk, claimed_by__isnull=True).count() != 1:
        logger.info('[WARM] task %s is claimed; leaving its transcript alone',
                    task.external_task_id)
        return False

    await_ai_capacity()
    revision = AIRevision.objects.create(
        job=job, operation='custom',
        label='Eagle standard ({0})'.format(standard.label),
        custom_instruction=standard_instruction(standard),
        source_language=task.source_language or '',
        quality_profile=(profile if profile != 'none' else ''),
    )
    run_ai_revision_pipeline(revision.id)
    revision.refresh_from_db()

    if revision.status != 'completed':
        logger.warning('[WARM] standard pass for %s finished as %s',
                       task.external_task_id, revision.status)
        return False
    if revision.quality_status in AIRevision.QUALITY_REFUSED_STATUSES:
        # The gate looked at the model's own output and would not sign it
        # off. Promoting it anyway would hand the worker text that is
        # confidently wrong, which is harder to review than raw ASR.
        logger.warning('[WARM] standard pass for %s refused by the gate: %s',
                       task.external_task_id, revision.quality_status)
        return False

    # Re-check the claim: the pipeline just spent real time, and a worker may
    # have taken the task during it.
    if Task.objects.filter(pk=task.pk, claimed_by__isnull=True).count() != 1:
        logger.info('[WARM] task %s claimed during the standard pass; not '
                    'overwriting', task.external_task_id)
        return False

    updated = 0
    with transaction.atomic():
        for segment in revision.segments.select_related('original_segment'):
            text = (segment.effective_text or '').strip()
            if not text or segment.original_segment_id is None:
                continue
            if text == (segment.original_segment.text or '').strip():
                continue
            TranscriptSegment.objects.filter(
                pk=segment.original_segment_id).update(text=text)
            updated += 1

    logger.info('[WARM] applied %s to %s (%d of %d lines changed)',
                standard.name, task.external_task_id, updated,
                revision.segments.count())
    return True


def _warm_translation(task: Task, job) -> bool:
    """
    Produce the TRANSLATION for a translation task.

    WHY THIS IS A SECOND STEP. The transcription pipeline yields the SOURCE
    transcript. Eagle's translation product wants translated text, and
    submitting the source under a "Target Language: French" header is a
    guaranteed rejection -- found by rendering a real warmed task, where the
    English transcript sailed through the gate because every structural
    marker it looked for was present.

    NO TARGET LANGUAGE MEANS NO TRANSLATION. Eagle identifies languages by
    opaque numeric id and publishes no endpoint that names them, so an
    unmapped id leaves the target unknown. Guessing would translate into the
    wrong language -- a certain rejection and a wasted purchase -- so the
    transcript is left warm and the submission gate refuses it.

    THIS SPENDS AI QUOTA, unlike the rest of warming. One ungated translation
    is one model request; a gated one (see below) is three to five.

    QUALITY-GATED, THE SAME WAY THE DASHBOARD ALREADY IS -- AND WAS NOT,
    UNTIL NOW. `AI_QUALITY_DEFAULT_PROFILE` has been sitting in production
    as `auto:volume` -- the documented, recommended setting for exactly this
    -- while this function created the revision with no `quality_profile` at
    all, so `run_ai_revision_pipeline`'s gate (`if revision.quality_profile:`)
    never engaged for a single warmed translation. Confirmed empirically,
    not just in code: every `translate`-operation usage row in the last 7
    days of production data had `avg_requests_per_translate_run = 1.00`
    -- zero exceptions. `default_profile_for` is the exact same resolver
    `TranscriptionJobViewSet.ai_revisions` already uses, so this brings the
    warm path in line with the dashboard rather than inventing a second
    resolution rule.

    THE COST WAS CHECKED AGAINST REAL USAGE BEFORE TURNING THIS ON. Busiest
    day so far: 170/900 daily requests (19%), 2.06M/6M daily tokens (34%).
    Even tripling that whole day -- a deliberately generous bound, since
    only translate's cost actually multiplies -- stays under the request
    cap with room to spare.
    """
    from apps.transcriptions.models import AIRevision
    from apps.transcriptions.tasks import run_ai_revision_pipeline
    from core.services.ai_assist import config, default_profile_for

    target = (task.target_language or '').strip()
    if not target:
        logger.warning('[WARM] task %s has no known target language; '
                       'leaving it transcript-only', task.external_task_id)
        return True

    existing = AIRevision.objects.filter(
        job=job, operation='translate', target_language=target, status='completed',
    ).first()
    if existing is None:
        # 'translate' is the platform's one DELIVERABLE_OPERATIONS entry, so
        # 'auto:volume' (or plain 'auto') always resolves to a real profile
        # here, never 'none' -- that fallback only matters for operations
        # that don't ship, which this one is not.
        quality_profile = default_profile_for(
            'translate', config('AI_QUALITY_DEFAULT_PROFILE', 'none'))
        if quality_profile == 'none':
            quality_profile = ''

        await_ai_capacity()
        revision = AIRevision.objects.create(
            job=job, operation='translate',
            label=f'Translation ({target})',
            source_language=task.source_language or '',
            target_language=target,
            quality_profile=quality_profile,
            # THE CLEANUP THAT USED TO BE ITS OWN PASS, folded in here where
            # it costs nothing: the model is already reading every line.
            custom_instruction=(
                'The source is a raw speech-recognition transcript. Ignore '
                'filler words, false starts and repetitions rather than '
                'translating them, and render the result as clean written '
                'prose. Do not summarize, omit or add content.'
            ),
        )
        if quality_profile:
            logger.info('[WARM] %s translating under quality profile %r',
                        task.external_task_id, quality_profile)
        run_ai_revision_pipeline(revision.id)
        revision.refresh_from_db()
    else:
        revision = existing

    if revision.status != 'completed':
        logger.warning('[WARM] translation for %s finished as %s',
                       task.external_task_id, revision.status)
        return False

    # Attach it as the deliverable: this is what the submitter renders, and
    # its absence is what the gate refuses on.
    Task.objects.filter(pk=task.pk).update(deliverable_revision=revision)
    return True


def run_cycle(pool_size: int = None, concurrency: int = None,
              product: Optional[str] = None, service=None,
              ai_limit: int = None) -> WarmReport:
    """
    Top the warm pool back up to `pool_size`, at most `concurrency` at once.

    Synchronous by design: the caller (a management command or scheduler)
    decides the cadence, and a cycle that has finished is a cycle whose
    memory has been released.
    """
    pool_size = pool_size or DEFAULT_POOL_SIZE
    concurrency = concurrency or DEFAULT_CONCURRENCY
    report = WarmReport()

    # MUST AGREE WITH is_warm(), NOT REIMPLEMENT IT LOOSELY. is_warm()'s own
    # docstring already names this exact failure mode: for a translation, a
    # completed TRANSCRIPT is not a completed TRANSLATION, and counting the
    # former as "warm" here believes the pool is full of finished work when
    # some of it is actually stranded half-done -- shortfall drops to 0,
    # no new candidates get pulled, and the half-finished translations are
    # never revisited. Confirmed live: production sat at a constant
    # `[warm] ready=24` for 5 hours (the loose count this query used to
    # produce) while the dashboard's real is_warm()-based "ready now"
    # badge showed 18, and only 4 real translations completed in that
    # entire window despite 76 unclaimed tasks sitting available -- the
    # other 6 of the 24 were transcript-done-translation-not-done tasks
    # this query was silently counting as finished.
    warm_now = Task.objects.filter(
        status=states.NEW, claimed_by__isnull=True, job__isnull=False,
        job__status='completed',
    ).exclude(
        kind=Task.TRANSLATION, deliverable_revision__isnull=True, polished_text='',
    ).count()
    report.already_warm = warm_now

    shortfall = max(0, pool_size - warm_now)
    if not shortfall:
        return report

    candidates = select_candidates(shortfall, product=product)

    # STAGE B IS RATIONED; STAGE A IS NOT.
    #
    # `select_candidates` already returns shortest-first, so taking the AI
    # budget off the front spends the metered half of the pipeline on the
    # tasks most likely to be claimed and finished. Everything else still
    # gets the free half, which is also the slow half.
    ai_limit = DEFAULT_AI_LIMIT if ai_limit is None else ai_limit
    plan = []
    for task in candidates:
        # A translation with its transcript already banked has nothing left
        # for Stage A to do -- only the provider can advance it. Out of
        # budget, it waits for a cycle with room rather than occupying a
        # slot to accomplish nothing.
        stage_a_done = bool(task.job_id and task.job
                            and task.job.status == 'completed')
        if stage_a_done and task.kind == Task.TRANSLATION:
            if ai_limit <= 0:
                report.deferred += 1
                continue
            ai_limit -= 1
            plan.append((task, True))
            continue

        allow_ai = ai_limit > 0
        if allow_ai:
            ai_limit -= 1
        plan.append((task, allow_ai))

    report.considered = len(plan)
    if not plan:
        return report

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_warm_guarded, t, service, allow_ai): (t, allow_ai)
                   for t, allow_ai in plan}
        for future in as_completed(futures):
            task, allow_ai = futures[future]
            ok, error = future.result()
            if ok:
                report.processed += 1
                report.fetched += 1
                if not allow_ai:
                    report.stage_a_only += 1
            else:
                report.failed += 1
                report.errors.append(f'{task.external_task_id}: {error}')
    return report


def _warm_guarded(task: Task, service, allow_ai: bool = True):
    """
    One task's failure must not take the cycle down with it -- and one
    task's THREAD must not leak a database connection.

    Django opens a connection per thread and never closes it on its own.
    That is the leak Phase 1 found across the engine, and a pool that warms
    fifty tasks a day would quietly add fifty more. Closing here keeps this
    path from contributing to a problem it would otherwise make worse.
    """
    from django.db import connection

    try:
        return warm_task(task, service=service, allow_ai=allow_ai), ''
    except Exception as exc:
        logger.exception('[WARM] failed to pre-process task %s', task.external_task_id)
        return False, f'{type(exc).__name__}: {exc}'
    finally:
        connection.close()


# --------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------- #

def purge_expired_audio(now=None) -> dict:
    """
    Delete cached audio the platform no longer needs.

    Three rules, because "finished" and "we never started" decay at very
    different rates:

      * a task we lost the race for -- audio is dead weight within the hour;
      * a task in a terminal state -- kept for the retention window, because
        a rejection means reworking it and that needs the audio back;
      * everything else -- kept.

    Only audio is removed. The transcript, the task and its audit history
    are small and stay.
    """
    now = now or timezone.now()
    removed, freed = 0, 0

    terminal_cutoff = now - dt.timedelta(days=AUDIO_RETENTION_DAYS)
    lost_cutoff = now - dt.timedelta(hours=LOST_RACE_RETENTION_HOURS)

    expired = list(Task.objects.filter(
        status__in=(states.APPROVED, states.CANCELLED, states.REJECTED, states.FAILED),
        updated_at__lt=terminal_cutoff,
    )) + list(Task.objects.filter(
        status=states.CANCELLED, updated_at__lt=lost_cutoff,
    ))

    for task in expired:
        for path in (cache_path_for(task),
                     os.path.join(cache_dir(), f'work-{task.id}.mp3')):
            if os.path.exists(path):
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    logger.warning('[WARM] could not remove %s: %s', path, exc)

    return {'files_removed': removed, 'bytes_freed': freed}
