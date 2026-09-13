"""
Submitting finished work to Eagle.

THE ORDERING RULE, AND WHY IT IS THE WHOLE FILE. Eagle is called first; our
task is marked SUBMITTED only once Eagle has acknowledged. A task recorded as
submitted that never reached the platform is worse than a loud failure,
because it looks like income that will never arrive and nobody goes looking
for it. The opposite order -- Eagle accepted, our commit failed -- is
recoverable by reconciliation. This one is not.

THE DOCUMENT IS RENDERED SERVER-SIDE. `core.services.export_formats` produces
it, and that renderer has been checked byte-for-byte against submissions Eagle
actually accepted. The browser never builds what gets submitted: two
implementations that can disagree, one used for preview and one for the real
thing, is a bug waiting for a deadline.

THE GATE RUNS FIRST, AND IT IS CHEAP. A rejection costs the purchased minutes
AND the worker's hour, so the deterministic checks are worth their
milliseconds. They are Tier 0 only -- no model calls -- because there is an
hour on the clock and a quality gate that phones an API is a quality gate that
can time out.

TIER 0 STAYS TIER 0 -- THIS FILE STILL MAKES NO MODEL CALLS. The gate also
surfaces a semantic audit's verdict now, but that audit already ran, on this
exact deliverable, when the warm-pool pass created it under a quality profile
(see warmpool._warm_translation) -- its result is a row already sitting in
the database. `_semantic_audit_findings` only reads it. A revision nobody
audited still reports quality_status='not_run' and contributes nothing here,
same as before this existed: no request is made, and none skipped that this
file would otherwise have made, because this file never made one.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional

from django.db import transaction

from core.services.ai_assist.quality import EDITED_VERBATIM

from . import services, states
from .eagle import (
    EagleAuthError, EagleIntegrationError, EagleNotConfigured,
    EagleSubmissionResult,
)
from .models import Task, TaskEvent

logger = logging.getLogger('eagle')

#: The name that appears on submissions, as `Transcribed by:` /
#: `Translated By:`.
#:
#: One value for the whole platform, not the individual tasker: Eagle sees a
#: single account, and putting a worker's real name on someone else's account
#: would be odd for them and confusing for the reviewer. Who actually did the
#: work is recorded in our own audit log, where it belongs.
#:
#: The accepted transcription sample on record carried the literal default
#: "Transcriber" and passed review, so leaving this unset is safe.
def submitter_name() -> str:
    """
    The name Eagle sees on the work: the account holder's, not a worker's.

    READ AT CALL TIME, NOT AT IMPORT. This used to be a module-level
    os.environ read, which freezes whatever the environment happened to hold
    when the process started -- so setting it and restarting only the web
    server left a long-running poller still submitting under the old value,
    with nothing on screen to say so. A wrong name on an accepted document
    is not something you find out about quickly.

    Settings first so it can be overridden in tests and in deployment
    without exporting anything.
    """
    from django.conf import settings
    configured = getattr(settings, 'EAGLE_SUBMITTER_NAME', None)
    if configured is None:
        configured = os.environ.get('EAGLE_SUBMITTER_NAME', '')
    return str(configured or '').strip()

#: Eagle's own accepted documents spell languages out -- "English", "French"
#: -- while our Task stores ISO codes. The header is compared by a human
#: reviewer, so it gets the words.
_LANGUAGE_NAMES = {
    'en': 'English', 'fr': 'French', 'es': 'Spanish', 'de': 'German',
    'it': 'Italian', 'pt': 'Portuguese', 'nl': 'Dutch', 'ru': 'Russian',
    'ar': 'Arabic', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
    'hi': 'Hindi', 'sw': 'Swahili', 'tr': 'Turkish',
}


def language_name(code: str, fallback: str = '') -> str:
    """`"fr"` -> `"French"`. An unknown code is shown as given."""
    if not code:
        return fallback
    return _LANGUAGE_NAMES.get(str(code).lower(), str(code))



class SubmissionRefused(Exception):
    """Not sent, and deliberately so."""

    def __init__(self, detail: str, safe_message: str = '', findings=None):
        self.detail = detail
        self._safe = safe_message
        self.findings = findings or []
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return self._safe or 'That work could not be submitted.'


class SubmissionFailed(Exception):
    """
    Reached the platform and did not succeed.

    Distinct from a refusal: the task may or may not be in a submitted state
    on Eagle's side, so this is escalated rather than retried blindly.
    """

    def __init__(self, detail: str, safe_message: str = ''):
        self.detail = detail
        self._safe = safe_message
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return self._safe or 'The platform did not accept the submission.'


@dataclass
class SubmissionPreview:
    """Exactly what will be sent, for a human to look at first."""
    content: str
    format_name: str
    character_count: int = 0
    word_count: int = 0
    findings: List[Dict] = field(default_factory=list)
    blocking: bool = False




def close_timestamp_gaps(segments):
    """
    Make every block start where the previous one ended.

    EAGLE'S FORMAT IS AN UNBROKEN CHAIN. Both accepted transcriptions run
    323 and 43 blocks with ZERO discontinuities -- each timestamp begins
    exactly where the last finished, with no gaps for the silence between
    sentences.

    Our ASR does not work that way. Whisper reports when speech happens and
    skips the pauses, so a warmed 25-minute transcript arrives with 16 gaps
    totalling 44 seconds. Every one is a HIGH finding against a rubric that
    grades timing, on a document that is otherwise correct.

    THE END TIMES ARE LEFT ALONE. A block still ends when the speech ends;
    only the START is pulled back to meet the previous block. The alternative
    -- stretching the end -- would claim someone was talking through a pause
    they did not fill, and the timings are what a reviewer checks against
    the audio.
    """
    closed = []
    previous_end = None
    for segment in segments:
        start = float(segment.start_time)
        end = float(segment.end_time)
        if previous_end is not None and start > previous_end:
            start = previous_end
        # A segment that starts before the last one ended (overlapping
        # speech) is left as it is: pulling it forward would reorder the
        # document.
        closed.append(SimpleNamespace(
            speaker_label=getattr(segment, 'speaker_label', ''),
            start_time=start,
            end_time=max(end, start),
            text=getattr(segment, 'text', ''),
        ))
        previous_end = max(end, start)
    return closed


def _prose_segments(text: str):
    """
    Paragraphs as segments, for a document that has no timings.

    `to_eagle_translation` takes segments and groups them back into
    paragraphs on a pause threshold. Giving each paragraph a gap wider than
    that threshold is what keeps the worker's paragraph breaks intact
    instead of collapsing the document into one block.
    """
    from core.services.transcript_formatting import PARAGRAPH_GAP_SECONDS

    step = max(PARAGRAPH_GAP_SECONDS * 2, 5.0)
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text or '') if p.strip()]
    return [
        SimpleNamespace(speaker_label='', start_time=i * step,
                        end_time=i * step + 1.0, text=paragraph)
        for i, paragraph in enumerate(paragraphs)
    ]


def _revision_segments(task: Task):
    """The machine translation, as the renderer expects it."""
    revision = task.deliverable_revision
    if revision is None:
        raise SubmissionRefused(
            f'Translation task {task.id} has no translated revision attached.',
            'This task has not been translated yet. The source transcript '
            'cannot be submitted as a translation.',
        )
    source_segments = list(revision.segments.all().order_by('start_time'))
    if not source_segments:
        raise SubmissionRefused(
            f'Revision {revision.id} has no segments.',
            'The translation is empty.',
        )
    # An AIRevisionSegment stores `original_text` and `processed_text`; the
    # renderers expect `.text`. `effective_text` is the model's own answer
    # to "which one counts".
    return [
        SimpleNamespace(
            speaker_label=seg.speaker_label,
            start_time=seg.start_time,
            end_time=seg.end_time,
            text=seg.effective_text,
        )
        for seg in source_segments
    ]


def render_for_eagle(task: Task) -> str:
    """
    The submission document, in the format Eagle accepts.

    Transcription gets speaker labels and millisecond timestamps; translation
    gets clean paragraphs and a word count. Both headers were validated
    against real accepted submissions.
    """
    from core.services.export_formats import to_eagle_transcription, to_eagle_translation

    job = task.job
    if job is None:
        raise SubmissionRefused(
            f'Task {task.id} has no transcript attached.',
            'There is no transcript to submit yet.',
        )

    # A TRANSLATION TASK MUST SUBMIT THE TRANSLATION, NOT THE SOURCE.
    #
    # This was found by rendering a real warmed task: the job's own segments
    # are the SOURCE transcript, so a translation task rendered the English
    # text under a header announcing "Target Language: French" -- and the
    # gate passed it, because every structural marker it looked for was
    # present. That submission would have been rejected and the purchased
    # minutes lost.
    #
    # The translated text lives on the deliverable AIRevision. Refusing when
    # it is absent is a STRUCTURAL check, not a language-detection guess,
    # which is why it cannot be fooled by an unusual transcript.
    if task.kind == Task.TRANSLATION:
        # THE POLISHED VERSION WINS. It is the same document after a human
        # ran it through the standard, and it is what the worker actually
        # reviewed. Falling back to the machine draft when one exists would
        # submit work nobody looked at.
        if (task.polished_text or '').strip():
            segments = _prose_segments(task.polished_text)
        else:
            segments = _revision_segments(task)
    else:
        # THE RENAME HAS TO REACH THE SUBMISSION.
        #
        # Renaming a speaker writes a job-level alias map rather than
        # touching the segments -- that is what makes it instant everywhere
        # and reversible. But this renderer read `speaker_label` straight
        # off the row and never consulted the map, so a worker who corrected
        # Male_1 to Female_1 saw the correction on screen and Eagle received
        # the ORIGINAL label.
        #
        # That is precisely the failure this module's own docstring warns
        # about -- editing one document and submitting another -- and on an
        # Eagle transcription task the speaker labels ARE the graded work.
        from core.services.export_formats import apply_speaker_aliases

        # CONTINUOUS TIMESTAMPS, because Eagle's accepted format has no gaps.
        segments = close_timestamp_gaps(
            apply_speaker_aliases(
                job.segments.all().order_by('start_time'),
                job.speaker_aliases,
            ))
        if not segments:
            raise SubmissionRefused(
                f'Job {job.id} has no segments.',
                'There is no transcript to submit yet.',
            )

    # THE TITLE LINE MEANS DIFFERENT THINGS FOR THE TWO PRODUCTS.
    #
    # A transcription header names the SUBJECT -- "Transcription Job: How to
    # Build Strong Habits" in the submissions Eagle accepted. Eagle's own
    # `title` is the media filename, which says nothing and reads as
    # unreviewed machine output, so a topic is generated from the transcript
    # during warming and used here.
    #
    # A translation header carries no title: the platform already shows the
    # task id beside it, and repeating a filename adds noise to a document a
    # human reads.
    if task.kind == Task.TRANSLATION:
        _title = ''
    else:
        _title = (task.topic or '').strip() or task.title or 'transcription job'

    metadata = {
        'title': _title,
        'client': 'Eagle Web',
        'date': None,  # renderer defaults to today, which is what we want
    }
    # WHOSE NAME GOES ON IT. The Eagle account holder's -- the work is
    # submitted through their account and graded against it. A tasker's name
    # would be wrong on both counts, and would also put a worker's identity
    # in front of an external platform.
    name = submitter_name()
    if name:
        metadata['transcriber'] = name
        metadata['translator'] = name

    if task.kind == Task.TRANSLATION:
        rendered = to_eagle_translation(
            # Empty on purpose: the positional title wins over the metadata,
            # so passing the filename here would put it back in the header
            # the moment the metadata said not to.
            segments, _title,
            # Spelled out, matching the documents Eagle has accepted -- a
            # human reviewer reads this header.
            source_lang=language_name(task.source_language, 'English'),
            target_lang=language_name(task.target_language, 'French'),
            metadata=metadata,
        )
    else:
        total = float(task.duration_seconds) if task.duration_seconds else None
        rendered = to_eagle_transcription(
            segments, _title,
            diarization_enabled=bool(job.enable_diarization),
            metadata=metadata, total_duration=total,
        )
    return rendered.decode('utf-8')


# Which of Eagle's two rubrics our transcripts are written to. A setting
# rather than a constant because the platform sends both kinds of work, and
# a transcript that is flawless under one is a guaranteed rejection under
# the other -- fillers removed is correct for edited verbatim and a defect
# for strict verbatim.
def transcription_standard_for(task: Task) -> str:
    from django.conf import settings
    return getattr(settings, 'EAGLE_TRANSCRIPTION_STANDARD', EDITED_VERBATIM)


def _transcript_standard_findings(task: Task, content: str) -> List[Dict]:
    """
    Translate the coach rules into gate findings.

    SEVERITY IS CARRIED THROUGH, NOT FLATTENED. `submit_task` refuses on
    critical alone, so promoting everything would block submissions on a
    style nit, and demoting everything would let a malformed speaker label
    through -- which is the single most common reason Eagle sends work back.

    Never raises. A defect in a checker must not be able to stop a worker
    submitting finished work; it degrades to "no extra findings".
    """
    try:
        from core.services.ai_assist.quality import run_transcript_checks
        issues = run_transcript_checks(content, transcription_standard_for(task))
    except Exception:  # pragma: no cover - a checker bug is not a blocker
        logger.exception('[SUBMIT] transcript checks failed for task %s', task.id)
        return []

    findings = []
    for issue in issues:
        findings.append({
            'severity': str(getattr(issue.severity, 'value', issue.severity)),
            'code': issue.rule or 'transcript-standard',
            'message': issue.correction and f'{issue.problem} {issue.correction}'
                       or issue.problem,
        })
    return findings



def translation_body(content: str) -> str:
    """
    The translated prose, without the header we generated ourselves.

    The checks below compare the output against the English source. Leaving
    "Translation Job: C 324.mp3" and "Client: Eagle Web" in would compare
    our own boilerplate against a transcript and report it as untranslated
    English.
    """
    lines = (content or '').splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.strip().rstrip(':').strip() == 'Translation':
            start = index + 1
            break
    end = len(lines)
    for index in range(start, len(lines)):
        if 'Number Of Words' in lines[index]:
            end = index
            break
    return '\n'.join(lines[start:end]).strip()


def _translation_findings(task: Task, content: str) -> List[Dict]:
    """
    The linguistic checks, on a translation, against its English source.

    WHY THIS MATTERS MORE HERE THAN ANYWHERE ELSE. On a transcription task
    the worker hears the audio and reads the text: they are a real reviewer
    and this gate is a safety net. On a translation, a worker who does not
    speak the target language cannot check meaning at all -- so these checks
    are not a net under the reviewer, they ARE the reviewer.

    The gate ran none of them. It checked that a word-count line existed and
    that a translation had been attached, and passed everything else. A
    dropped paragraph, an invented citation, a number changed from 10 to
    100, English left untranslated -- all submitted without comment.

    Every check here is deterministic and costs no provider requests, so
    there is no reason not to run them on every submission.

    Never raises: a defect in a checker must not stop a worker submitting
    finished work.
    """
    body = translation_body(content)
    if not body:
        return []

    job = task.job
    if job is None:
        return []
    source = '\n\n'.join(
        (segment.text or '').strip()
        for segment in job.segments.all().order_by('start_time')
    ).strip()
    if not source:
        return []

    try:
        from core.services.ai_assist.quality import run_deterministic_checks
        issues = run_deterministic_checks(
            source, body, task.target_language or 'fr', None,
            is_translation=True, submission_ready=True,
        )
    except Exception:  # pragma: no cover - a checker bug is not a blocker
        logger.exception('[SUBMIT] translation checks failed for task %s', task.id)
        return []

    findings = []
    for issue in issues:
        finding = {
            'severity': str(getattr(issue.severity, 'value', issue.severity)),
            'code': issue.rule or 'translation-quality',
            'message': (f'{issue.problem} {issue.correction}'.strip()
                        if issue.correction else issue.problem),
        }
        finding.update(_locate(issue, task))
        findings.append(finding)
    return findings


def _semantic_audit_findings(task: Task) -> List[Dict]:
    """
    Surfaces a semantic audit already paid for and already run -- this
    function performs no audit and no provider request of its own.

    THE GAP THIS CLOSES. A 20-document review of real translation
    submissions found dropped paragraphs, invented numbers, and untranslated
    passages reaching this preview with zero findings, because the gate only
    ever ran `_translation_findings`' deterministic checks. Those are
    exactly the defect classes the semantic audit (STAGE_AUDIT, in
    quality/engine.py) already checks for -- and for a translation the
    warm-pool pass produced (`warmpool._warm_translation`, under
    `quality_profile`), that audit already ran against this exact text and
    already wrote its verdict to `revision.quality_status` and an
    `AIQualityReport` row. Nothing here ever consulted it.

    WHY THIS DOES NOT COST A REQUEST. It reads `revision.quality_status` and
    the most recent `AIQualityReport`, both already in the database by the
    time a worker opens the preview. A revision nobody audited (no
    `quality_profile` requested, or a run still in progress) reports
    'not_run', 'pending' or 'auditing' and this returns nothing for it --
    the same as if this function did not exist.

    NOT RUN AGAINST A HUMAN-POLISHED DOCUMENT. The audit judged the AI
    revision's own text; once a worker has pasted a polished replacement
    (`task.polished_text`), that text is what will actually be submitted,
    and the audit's findings may describe a paragraph that no longer exists
    or miss one the human edit just introduced -- exactly the staleness
    `_segment_pairs` already refuses to guess through for deterministic
    findings. A worker actively polishing is already reading every line, so
    nothing is lost by staying quiet here.

    Never raises: same contract as `_translation_findings`.
    """
    if (task.polished_text or '').strip():
        return []

    revision = task.deliverable_revision
    if revision is None:
        return []

    try:
        from apps.transcriptions.models import AIRevision

        if revision.quality_status == 'not_run':
            return []
        if revision.quality_status in AIRevision.QUALITY_IN_PROGRESS_STATUSES:
            return []

        report = revision.quality_reports.order_by('-created_at').first()
        stored_issues = report.issues if report else []

        if not stored_issues:
            if revision.quality_status == 'validated':
                return []
            # The gate refused this revision but left no itemised issue --
            # a technical failure, most likely. Say so rather than showing
            # nothing, but as advice (HIGH, not CRITICAL): this is the audit
            # infrastructure coming up empty, not a confirmed defect in the
            # translation itself, and it must not strand a task on its own.
            return [{
                'severity': 'high',
                'code': ('semantic-audit-incomplete'
                         if revision.quality_status == 'technical_failure'
                         else 'semantic-review-required'),
                'message': revision.quality_reason or (
                    'The semantic quality check did not clear this '
                    'translation. Review it carefully before submitting.'
                ),
            }]

        from core.services.ai_assist.quality.issues import QualityIssue

        findings = []
        for raw in stored_issues:
            issue = QualityIssue.from_dict(raw)
            finding = {
                'severity': str(getattr(issue.severity, 'value', issue.severity)),
                'code': issue.rule or 'semantic-quality',
                'message': (f'{issue.problem} {issue.correction}'.strip()
                            if issue.correction else issue.problem),
            }
            finding.update(_locate_semantic(issue, task))
            findings.append(finding)
        return findings
    except Exception:  # pragma: no cover - a reporting bug is not a blocker
        logger.exception(
            '[SUBMIT] could not read the semantic audit for task %s', task.id)
        return []


def _segment_pairs(task: Task):
    """
    Aligned (start, source, target) lines to locate a finding against.

    THE TARGET MUST BE WHAT IS ACTUALLY BEING SUBMITTED. When a worker has
    pasted a polished version, that is what goes to the platform -- the
    machine draft is no longer the document. Showing them the draft's French
    beside a finding computed from their polished French would be this
    module's own warned-about failure in miniature: quoting one document
    while submitting another, with the worker left wondering why the line we
    quote does not appear anywhere on their screen.

    Polished text has no timings, so it is matched by paragraph against the
    source. Where the paragraph count does not line up, the French is left
    out rather than guessed at -- a wrong quote is worse than none.
    """
    if task.deliverable_revision_id is None and not (task.polished_text or '').strip():
        return []

    revision_rows = [
        (segment.start_time, (segment.original_text or '').strip(),
         (segment.effective_text or '').strip())
        for segment in task.deliverable_revision.segments.all().order_by('start_time')
    ] if task.deliverable_revision_id else []

    polished = (task.polished_text or '').strip()
    if not polished:
        return revision_rows

    paragraphs = [p.text for p in _prose_segments(polished)]
    if len(paragraphs) == len(revision_rows):
        return [(start, source, paragraphs[i])
                for i, (start, source, _) in enumerate(revision_rows)]
    # Counts disagree: keep the English, which is still authoritative, and
    # say nothing about the French rather than quote the wrong line.
    return [(start, source, '') for start, source, _ in revision_rows]


def _clock(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return '00:00'
    return f'{total // 60:02d}:{total % 60:02d}'


def _locate_semantic(issue, task: Task) -> Dict:
    """
    Where a semantic-audit finding lives, using the segment_index the audit
    itself attached -- no searching required.

    `_locate` (below) has to search for a deterministic finding's location
    because those checks run over the whole document joined into one
    string and never knew which segment they were looking at. A semantic
    finding is different: `engine.run()` built its `pairs` in the exact
    same per-segment, start-time order `_segment_pairs` returns here, so
    the audit's own `segment_index` already IS that position -- reusing it
    is a lookup, not a search, and cannot mis-locate a finding onto a
    coincidentally-similar line the way number-matching could.
    """
    if issue.segment_index is None:
        return {}
    pairs = _segment_pairs(task)
    if not (0 <= issue.segment_index < len(pairs)):
        return {}
    start, source_text, target_text = pairs[issue.segment_index]
    return {
        'segment_index': issue.segment_index,
        'at': _clock(start),
        'source_text': source_text,
        'target_text': target_text,
    }


def _locate(issue, task: Task) -> Dict:
    """
    Find the line a finding is about, and say what to do with it.

    WHY THE MESSAGE ALONE IS NOT ENOUGH. The checks run over the whole
    document joined into one string, so a finding could name the offending
    value -- "16, 2" -- without saying which of 67 segments it lived in. A
    worker was told a rule ("restore every quantity exactly as the source
    states it") and left to search for the place it applied.

    AND THE WORKER MAY NOT READ THE TARGET LANGUAGE. That is the normal
    case on a translation task, not the exception: telling somebody who
    does not read French that a quantity is wrong, without showing them
    which French sentence and which English one it came from, is not an
    instruction they can act on. Both sentences and the specific token are
    what make the edit mechanical instead of linguistic.

    Never raises and never invents a location: an issue whose value cannot
    be found in any segment simply keeps the document-level message it
    already had.
    """
    # MATCHED AS NUMBERS, NOT AS SUBSTRINGS.
    #
    # The obvious version -- `if value in sentence` -- finds "2" inside
    # "$62" and points the worker at a line that is perfectly correct,
    # which is worse than giving no location at all. It also misses the
    # value it is actually looking for: the checker reports "16.67" while
    # the French writes "16,67", so the literal never matches.
    #
    # Using the detector's own tokeniser removes both problems at once:
    # whatever it counted as a number here is what we search for, in the
    # same normalised form.
    from core.services.ai_assist.quality.deterministic import extract_numbers

    wanted = extract_numbers(issue.source_span or issue.target_span or '')
    if not wanted:
        return {}

    looking_in_source = bool(issue.source_span)
    for index, (start, source_text, target_text) in enumerate(_segment_pairs(task)):
        haystack = source_text if looking_in_source else target_text
        present = set(extract_numbers(haystack))
        hit = next((v for v in wanted if v in present), None)
        if hit is None:
            continue
        hit = format(hit.normalize(), 'f')

        at = _clock(start)
        # NAME BOTH SIDES. "16 is missing from the French" reads as nonsense
        # beside a French line that visibly contains 16,67 -- and the worker
        # who most needs this message is the one who cannot read the French
        # to resolve the apparent contradiction themselves.
        def _listed(text):
            found = extract_numbers(text)
            return ', '.join(format(v.normalize(), 'f') for v in found) or 'none'

        if not target_text:
            # We know the English line; we cannot honestly quote the French.
            what = (f'At {at} the English uses {_listed(source_text)}, and '
                    f'"{hit}" is missing from your translation. Write the '
                    f'quantity the same way the English does -- do not round '
                    f'it, and do not turn a fraction into a decimal.')
        elif looking_in_source:
            what = (f'At {at} the English uses {_listed(source_text)}; the '
                    f'French uses {_listed(target_text)}. "{hit}" is not '
                    f'there. Write the quantity the same way the English '
                    f'does -- do not round it, and do not turn a fraction '
                    f'into a decimal.')
        else:
            what = (f'At {at} the French uses {_listed(target_text)}, but the '
                    f'English only uses {_listed(source_text)}. "{hit}" was '
                    f'invented. Replace it with what the English line states.')

        return {
            'segment_index': index,
            'at': at,
            'source_text': source_text,
            'target_text': target_text,
            'token': hit,
            'action': what,
        }
    return {}


#: Findings that stop a submission whatever severity they carry.
#:
#: THE GATE BLOCKS ON `critical` ALONE, and that is right for almost
#: everything: a HIGH finding is advice, and an accepted task carried three
#: of them. But one HIGH is not advice.
#:
#: A run of ENGLISH left sitting in a French deliverable is about as certain
#: a rejection as this platform produces, and it is not a judgement call the
#: way a register or terminology finding is -- the source language is either
#: in the output or it is not. Seen on a real task, where it sat under the
#: heading "Nothing in this document blocks submission" and could have been
#: sent with one click.
#:
#: Kept as a NAMED SET rather than by promoting the finding to `critical`,
#: because severity means "how bad is this" everywhere else in the quality
#: layer, and the repair loop and the audit prompt both read it. This says
#: "do not send it", which is a different statement.
ALWAYS_BLOCKING = frozenset({
    'leakage.untranslated-source',
})


def run_quality_gate(task: Task, content: str) -> List[Dict]:
    """
    Tier 0 only: deterministic checks, no model calls.

    Returns findings. A finding is not automatically a refusal -- that
    decision belongs to `submit_task`, because a warning worth showing a
    worker is not always worth losing a task over.
    """
    findings: List[Dict] = []

    if not content.strip():
        findings.append({'severity': 'critical', 'code': 'empty',
                         'message': 'The submission is empty.'})
        return findings

    if task.kind == Task.TRANSCRIPTION:
        if '[END OF RECORDING' not in content:
            findings.append({'severity': 'high', 'code': 'missing-end-marker',
                             'message': 'The end-of-recording marker is missing.'})
        if '-->' not in content:
            findings.append({'severity': 'critical', 'code': 'missing-timestamps',
                             'message': 'No timestamps found. Eagle grades timing.'})
        # THE COACH'S OWN RULES, run against the document we are about to
        # send. These were written from Eagle's guide and were until now
        # unreachable code -- every check costs zero provider requests, and
        # each one corresponds to something the platform actually rejects
        # for: a speaker label that is a name instead of Male_1, a
        # discontinuous timestamp chain, a bracket tag Eagle does not
        # recognise, a numeral written the wrong way.
        findings.extend(_transcript_standard_findings(task, content))
        # THE SELF-CHECK IS REQUIRED, NOT ADVISORY. See
        # TranscriptCheckPromptView's own docstring for why: a hand-patched
        # rule catches what THIS document happened to get wrong, on THIS
        # topic; a worker's own assistant, reading the actual recording's
        # subject matter, catches what no rule was ever written for. That
        # is the only piece of this pipeline that scales to a niche nobody
        # has audited yet, and it costs nothing on our own token budget --
        # so it stops being optional the moment it exists.
        if task.accuracy_check_by_id is None:
            findings.append({
                'severity': 'critical', 'code': 'self-check-not-completed',
                'message': 'Copy the accuracy-check prompt, run it through '
                           'your own assistant, and paste back what it '
                           'found before submitting.',
            })
    else:
        if 'Number Of Words' not in content:
            findings.append({'severity': 'high', 'code': 'missing-word-count',
                             'message': 'The word count line is missing.'})
        # Structural, not linguistic: if there is no translated revision the
        # content can only be the source transcript, whatever it looks like.
        if task.deliverable_revision_id is None and not (task.polished_text or '').strip():
            findings.append({
                'severity': 'critical', 'code': 'not-translated',
                'message': 'This task has not been translated yet — the '
                           'submission would be the source transcript.',
            })
        # AND THE LINGUISTIC CHECKS. On a translation these are not a net
        # under the reviewer -- for a worker who does not read the target
        # language, they are the reviewer.
        findings.extend(_translation_findings(task, content))
        # AND WHATEVER THE SEMANTIC AUDIT ALREADY CONCLUDED. Free to read,
        # not free to compute -- see _semantic_audit_findings for why this
        # costs no additional request.
        findings.extend(_semantic_audit_findings(task))
        # THE SELF-CHECK IS REQUIRED, NOT ADVISORY -- same reasoning as the
        # transcription branch above. `polished_by` being set is what
        # proves the copy/paste-through-the-worker's-own-assistant loop
        # actually ran; a task can otherwise reach here having never left
        # the machine draft, which is exactly the vocabulary-blind spot a
        # hand-maintained blocklist can never keep up with on its own.
        if task.polished_by_id is None:
            findings.append({
                'severity': 'critical', 'code': 'self-check-not-completed',
                'message': 'Copy the prompt and the translation into your '
                           'own assistant, paste back what it found (or '
                           'the corrected version), and save it before '
                           'submitting.',
            })

    # A transcript far shorter than its audio is the signature of a
    # truncated pipeline run, and it is worth stopping before it costs the
    # minutes.
    if task.duration_seconds:
        words = len(content.split())
        expected_floor = (task.duration_seconds / 60.0) * 40  # very conservative
        if words < expected_floor:
            findings.append({
                'severity': 'critical', 'code': 'suspiciously-short',
                'message': f'Only {words} words for '
                           f'{task.duration_seconds // 60} minutes of audio.',
            })
    return findings


def preview_submission(task: Task) -> SubmissionPreview:
    """
    What Eagle will receive, plus what the gate thinks of it.

    Shown to the worker before they submit. Given the cost of a rejection,
    five seconds of a human looking at the real document is the cheapest
    check available.
    """
    content = render_for_eagle(task)
    findings = run_quality_gate(task, content)
    return SubmissionPreview(
        content=content,
        format_name='eagle',
        character_count=len(content),
        word_count=len(content.split()),
        findings=findings,
        blocking=any(f['severity'] == 'critical'
                     or f.get('code') in ALWAYS_BLOCKING
                     for f in findings),
    )


def submit_task(task_id, actor, service=None, force: bool = False,
                reason: str = '') -> Task:
    """
    Submit to Eagle, then record it.

    `force` exists for the deadline endgame: at T-2 minutes a mediocre
    submission that might pass beats a guaranteed loss of the minutes. It
    bypasses non-critical findings ONLY -- an empty or timestamp-less
    document is still refused, because submitting that is not a gamble, it
    is a certain rejection plus a mark against the account.
    """
    from .eagle import get_eagle_service

    service = service or get_eagle_service()
    task = Task.objects.select_related('job', 'claimed_by').get(pk=task_id)

    if task.status not in (states.READY_TO_SUBMIT, states.REVIEW, states.IN_PROGRESS):
        raise SubmissionRefused(
            f'Task {task.id} is {task.status}.',
            'That task is not ready to submit.',
        )

    preview = preview_submission(task)
    if preview.blocking:
        raise SubmissionRefused(
            f'Quality gate blocked task {task.id}: {preview.findings}',
            'This work cannot be submitted yet: ' + preview.findings[0]['message'],
            findings=preview.findings,
        )
    if preview.findings and not force:
        logger.info('[SUBMIT] task %s has %d non-blocking findings',
                    task.id, len(preview.findings))

    if not task.external_task_id:
        # Local task: nothing external to submit to. Record and move on.
        return services.mark_submitted(task.id, actor=actor, reference='local')

    # WALK TO READY BEFORE SPENDING A REQUEST, NOT AFTER.
    #
    # This function accepts a task in IN_PROGRESS or REVIEW, but the only
    # transition into SUBMITTED is from READY_TO_SUBMIT. So a worker pressing
    # Submit from IN_PROGRESS used to render the document, pass the gate,
    # POST it to Eagle -- and only then fail with "a task cannot move from
    # in_progress to submitted with your permissions."
    #
    # Eagle accepted the work; we refused to record it. The worker saw an
    # error, the platform had the document, and the two disagreed until
    # somebody reconciled by hand. That happened on task 4625.
    #
    # Every step below is a transition the worker is already allowed to make
    # -- it is exactly what the buttons would have done, in the order they
    # would have done it. Doing it here means the state is provably able to
    # receive the result BEFORE the irreversible call, and a task that cannot
    # get there is refused while a refusal is still free.
    task = _advance_to_ready(task, actor)

    # --- the external call. After this, work exists on Eagle's side. ---- #
    try:
        result = service.submit_task(
            task.external_task_id, preview.content, task.external_source,
            metadata={'task_id': str(task.id)},
        )
    except (EagleNotConfigured, EagleAuthError) as exc:
        raise SubmissionRefused(exc.detail, exc.safe_message) from exc
    except EagleIntegrationError as exc:
        # A TRANSPORT ERROR IS NOT PROOF THE WRITE DID NOT LAND.
        #
        # This is the failure that actually happened on task 4639. The POST
        # reached Eagle, Eagle recorded the submission -- status `submitted`,
        # a `submitted_at`, our document in `translated_text` -- and the
        # response came back unreadable. We told the worker "the platform
        # could not be reached, try again", which was false in the most
        # expensive possible direction: the retry it invites is a SECOND
        # submission of work already delivered, and the state divergence it
        # leaves has to be reconciled by hand.
        #
        # The comment three lines above already said "after this, work exists
        # on Eagle's side". The code did not act on its own warning. So now
        # it asks: one read, and the platform settles it.
        #
        # A failed confirmation means we genuinely do not know, and "do not
        # know" is reported as failure -- the safe direction, because a
        # worker who resubmits unnecessarily loses a minute and a worker who
        # believes a lost submission landed loses the task.
        if _confirm_landed(task, service):
            logger.warning(
                '[SUBMIT] task %s reported a transport error but the platform '
                'has it: treating as delivered. (%s)', task.id, exc.detail,
            )
            result = EagleSubmissionResult(
                accepted=True,
                reference=task.external_task_id,
                message='Confirmed on the platform after a transport error.',
            )
        else:
            _record_failure(task, actor, exc.detail)
            raise SubmissionFailed(
                exc.detail,
                'The platform could not be reached. Your work is saved -- '
                'try again.',
            ) from exc

    if not getattr(result, 'accepted', False):
        _record_failure(task, actor, getattr(result, 'message', 'not accepted'))
        raise SubmissionFailed(
            f'Platform refused submission for {task.external_task_id}.',
            'The platform did not accept the submission.',
        )

    # ONLY NOW does our state move.
    with transaction.atomic():
        submitted = services.mark_submitted(
            task.id, actor=actor, reference=result.reference or task.external_task_id,
        )
        services.record_event(
            submitted, TaskEvent.SUBMISSION_COMPLETED, actor=actor,
            from_status=states.READY_TO_SUBMIT, to_status=states.SUBMITTED,
            message=reason or 'Submitted to the external platform.',
            metadata={
                'external_task_id': task.external_task_id,
                'characters': preview.character_count,
                'words': preview.word_count,
                'findings': len(preview.findings),
                'forced': bool(force),
            },
        )
    logger.info('[SUBMIT] task %s submitted to %s as %s',
                task.id, task.external_source, result.reference)
    return submitted


#: External statuses that mean the platform HAS our work. `completed` maps to
#: approved and `rejected` means it was read and sent back -- both are proof
#: the submission arrived, which is the only question being asked here.
_LANDED_EXTERNAL_STATUSES = frozenset({
    'submitted', 'completed', 'rejected', 'reviewed', 'review_failed',
})


def _advance_to_ready(task: Task, actor):
    """
    Move a task to READY_TO_SUBMIT so the post-submission transition is legal.

    ONE STEP AT A TIME, THROUGH THE STATE MACHINE. Nothing here writes a
    status directly: each hop is the same service call the corresponding
    button makes, so the audit trail, the locking and the broadcasts all
    happen exactly as they would if a person had clicked through. The
    machine still refuses anything it would have refused.

    Raises SubmissionRefused if the task cannot get there -- while refusing
    is still free, which is the entire point of doing this before the
    external call rather than after it.
    """
    if task.status == states.READY_TO_SUBMIT:
        return task
    try:
        if task.status == states.IN_PROGRESS:
            task = services.begin_review(task.id, actor)
        if task.status == states.REVIEW:
            task = services.mark_ready(
                task.id, actor, revision=task.deliverable_revision)
    except Exception as exc:
        raise SubmissionRefused(
            f'Could not advance task {task.id} from {task.status}: {exc}',
            'That task is not ready to submit yet.',
        ) from exc

    if task.status != states.READY_TO_SUBMIT:
        raise SubmissionRefused(
            f'Task {task.id} is {task.status}, not ready to submit.',
            'That task is not ready to submit.',
        )
    return task


def _confirm_landed(task, service) -> bool:
    """
    Did the submission arrive, despite the error we got back?

    ONE READ, AND THE PLATFORM DECIDES. Our own state cannot answer this --
    it was never advanced, which is exactly the bug -- so the question goes
    to the only system that knows.

    ANY FAILURE HERE MEANS "NO". If the confirming read also fails we are no
    wiser than before, and reporting failure is the safe direction: an
    unnecessary retry costs a minute, while wrongly declaring a lost
    submission delivered costs the whole task and nobody finds out until the
    deadline passes.
    """
    if not task.external_task_id:
        return False
    try:
        external = service.get_task(task.external_task_id, task.external_source)
    except Exception as exc:
        logger.info('[SUBMIT] could not confirm task %s with the platform: %s',
                    task.id, exc)
        return False

    raw = getattr(external, 'raw', None) or {}
    status = str(raw.get('external_status') or '').strip().lower()
    mapped = str(raw.get('mapped_status') or '').strip().lower()
    landed = status in _LANDED_EXTERNAL_STATUSES or mapped in (
        states.SUBMITTED, states.APPROVED, states.REJECTED,
    )
    logger.info('[SUBMIT] platform reports task %s as %r (mapped %r): %s',
                task.external_task_id, status, mapped,
                'delivered' if landed else 'not delivered')
    return landed

def _record_failure(task: Task, actor, detail: str) -> None:
    """
    A failed submission must be loud in the record.

    The worker's hour and the account's minutes are already spent; the one
    thing that must not happen is for it to look like it succeeded.
    """
    try:
        # SUBMISSION_STARTED with success=False, not a separate FAILED type.
        # The model carries a `success` boolean exactly so that every event
        # does not need a failure twin, and the timeline already renders a
        # false one as "did not complete".
        services.record_event(
            task, TaskEvent.SUBMISSION_STARTED, actor=actor,
            from_status=task.status, to_status=task.status, success=False,
            message='Submission to the external platform failed.',
            metadata={'detail': detail[:500]},
        )
    except Exception:
        logger.exception('[SUBMIT] could not record a submission failure')
