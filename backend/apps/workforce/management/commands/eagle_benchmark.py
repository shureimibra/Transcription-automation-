"""
How good is our transcription, measured against work Eagle accepted?

WHY THIS EXISTS. Every quality claim in this project so far is inferred:
the gate was written from two accepted documents, and nobody has ever
compared what our pipeline produces against a transcript a coach actually
signed off. Without that, "the machine does 80% of the work" is a hope.

WHAT IT MEASURES, in the order that decides whether the business works:

  1. SPEAKER IDENTIFICATION. On an Eagle transcription task this is the
     graded work -- a perfect transcript with the wrong speakers is a
     rejection. Reported as time-weighted agreement after the best possible
     label mapping, because our "Male_1" and theirs need not be the same
     person and a naive comparison would score a perfect result at zero.

  2. SPEAKER COUNT. Finding three people where there are two is a different
     failure from mislabelling, and needs a different fix.

  3. WORD ACCURACY. Word error rate over the whole document, normalised for
     case and punctuation, because those are style rather than hearing.

    manage.py eagle_benchmark --reference accepted.txt --job <uuid>
    manage.py eagle_benchmark --reference accepted.txt --audio recording.mp3
"""
import difflib
import os
import re
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

#: Male_1: / Female_12: -- the only speaker form Eagle permits.
SPEAKER_RE = re.compile(r'^\s*((?:Male|Female)_\d+)\s*:\s*$')
#: [HH:MM:SS.mmm --> HH:MM:SS.mmm]
STAMP_RE = re.compile(
    r'^\s*\[(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> '
    r'(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]\s*$')
END_RE = re.compile(r'^\s*\[END OF RECORDING')


def _seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_eagle_transcript(text: str):
    """
    An accepted Eagle transcript into (speaker, start, end, text) turns.

    Tolerant by design: a document a human typed will have stray blank
    lines and the occasional missing timestamp, and refusing to read it
    would make the benchmark useless on exactly the material worth
    measuring.
    """
    turns = []
    speaker = ''
    start = end = None
    words = []

    def flush():
        if words and start is not None:
            turns.append((speaker, start, end, ' '.join(words).strip()))

    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped or END_RE.match(stripped):
            continue
        speaker_match = SPEAKER_RE.match(stripped)
        if speaker_match:
            flush()
            words = []
            speaker = speaker_match.group(1)
            start = end = None
            continue
        stamp = STAMP_RE.match(stripped)
        if stamp:
            flush()
            words = []
            start = _seconds(*stamp.groups()[:4])
            end = _seconds(*stamp.groups()[4:])
            continue
        words.append(stripped)
    flush()
    return turns


def normalise(text: str) -> list:
    """Words, lowercased, without punctuation. Case and commas are style;
    hearing the wrong word is not."""
    return re.findall(r"[a-z0-9']+", (text or '').lower())


def word_error_rate(reference: list, hypothesis: list) -> float:
    """
    Edit distance over words, as a proportion of the reference.

    difflib rather than a hand-rolled Levenshtein: it is in the standard
    library, it is correct, and at transcript lengths the speed difference
    does not matter.
    """
    if not reference:
        return 0.0
    matcher = difflib.SequenceMatcher(a=reference, b=hypothesis, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    edits = max(len(reference), len(hypothesis)) - matched
    return edits / len(reference)


def speaker_at(turns, moment):
    for speaker, start, end, _ in turns:
        if start <= moment < end:
            return speaker
    return None


def speaker_agreement(reference, ours, step=0.25):
    """
    Time-weighted agreement, after the best label mapping.

    THE MAPPING MATTERS. Our diarizer numbers speakers in the order it
    hears them, and so does a human; there is no reason "Male_1" means the
    same person in both. Scoring them directly would report a flawless
    result as a total failure. So every one of our labels is assigned to the
    reference label it overlaps most, and the score is computed after that.

    Sampled every quarter second rather than compared segment-by-segment:
    segment boundaries never line up between two transcripts, and time is
    what actually matters -- being wrong for ten seconds is ten times worse
    than being wrong for one.
    """
    if not reference or not ours:
        return None

    horizon = max(end for _, _, end, _ in reference)
    overlap = defaultdict(lambda: defaultdict(float))
    moment = 0.0
    while moment < horizon:
        theirs = speaker_at(reference, moment)
        mine = speaker_at(ours, moment)
        if theirs and mine:
            overlap[mine][theirs] += step
        moment += step

    # Greedy assignment: each of our labels to whichever of theirs it shares
    # the most time with. Optimal assignment would be Hungarian, but with a
    # handful of speakers the two agree and this is readable.
    mapping = {}
    for mine, counts in overlap.items():
        mapping[mine] = max(counts, key=counts.get)

    agreed = total = 0.0
    moment = 0.0
    while moment < horizon:
        theirs = speaker_at(reference, moment)
        mine = speaker_at(ours, moment)
        if theirs is not None:
            total += step
            if mine is not None and mapping.get(mine) == theirs:
                agreed += step
        moment += step

    return {
        'agreement': agreed / total if total else 0.0,
        'mapping': mapping,
        'covered': total / horizon if horizon else 0.0,
    }


class Command(BaseCommand):
    help = 'Measure our transcription against an accepted Eagle transcript.'

    def add_arguments(self, parser):
        parser.add_argument('--reference', required=True,
                            help='The accepted transcript, as a text file.')
        parser.add_argument('--job', help='An existing job id to compare.')
        parser.add_argument('--audio', help='Audio to run through the pipeline first.')
        parser.add_argument('--diarize', action='store_true', default=True,
                            help='Speaker identification on (default).')

    def handle(self, *args, **options):
        path = options['reference']
        if not os.path.exists(path):
            raise CommandError(f'No such file: {path}')
        reference = parse_eagle_transcript(
            open(path, encoding='utf-8', errors='replace').read())
        if not reference:
            raise CommandError(
                'Could not read any turns. Expected Eagle format: a '
                '"Male_1:" line, then "[HH:MM:SS.mmm --> HH:MM:SS.mmm]", '
                'then the text.')

        job = self._job(options)
        ours = [
            (s.speaker_label or '', s.start_time, s.end_time, s.text or '')
            for s in job.segments.all().order_by('start_time')
        ]
        self._report(reference, ours)

    # ------------------------------------------------------------------ #


    @staticmethod
    def _working_copy(path: str) -> str:
        """
        A second name for the same bytes, for the pipeline to consume.

        Hard link where the filesystem allows it -- no extra disk, and the
        original survives the pipeline unlinking its own name. Falls back to
        a real copy across filesystems, where a link is impossible.
        """
        import shutil
        import uuid

        base, extension = os.path.splitext(path)
        working = f'{base}.work-{uuid.uuid4().hex[:8]}{extension}'
        try:
            os.link(path, working)
        except (OSError, AttributeError):
            shutil.copyfile(path, working)
        return working

    def _job(self, options):
        from apps.transcriptions.models import Project, TranscriptionJob
        from apps.transcriptions.tasks import run_transcription_pipeline

        if options.get('job'):
            job = TranscriptionJob.objects.filter(pk=options['job']).first()
            if job is None:
                raise CommandError(f'No job {options["job"]}')
            return job

        audio = options.get('audio')
        if not audio or not os.path.exists(audio):
            raise CommandError('Give --job <uuid> or --audio <path>.')

        # NEVER HAND THE PIPELINE THE ORIGINAL FILE.
        #
        # `run_transcription_pipeline` unlinks `job.file_path` when it
        # finishes (tasks.py) -- deliberate for a browser upload, where the
        # file is a one-shot. Pointing it at a file somebody gave us to
        # measure against destroys the very thing we were measuring, and it
        # is not recoverable.
        #
        # warmpool._working_copy exists for exactly this reason and carries
        # the same warning. I read that comment, wrote this command, and
        # deleted a benchmark recording anyway. A hard link costs nothing
        # and gives the pipeline its own name to remove.
        working = self._working_copy(audio)

        project, _ = Project.objects.get_or_create(name='Benchmark')
        job = TranscriptionJob.objects.create(
            project=project, input_source='upload', file_path=working,
            enable_diarization=bool(options['diarize']))
        self.stdout.write(f'Running the pipeline on a copy of {audio} ...')
        run_transcription_pipeline(job.id)
        job.refresh_from_db()
        if job.status != 'completed':
            raise CommandError(f'Pipeline finished as {job.status}: '
                               f'{job.error_message or "no reason given"}')
        return job

    def _report(self, reference, ours):
        w = self.stdout.write
        their_speakers = sorted({s for s, _, _, _ in reference if s})
        our_speakers = sorted({s for s, _, _, _ in ours if s})

        w('')
        w('SPEAKERS  (the graded work on a transcription task)')
        w(f'  accepted transcript : {len(their_speakers)}  {their_speakers}')
        w(f'  ours                : {len(our_speakers)}  {our_speakers}')
        if len(our_speakers) != len(their_speakers):
            w(f'  MISCOUNT: we found {len(our_speakers)} where there are '
              f'{len(their_speakers)}. Mislabelling and miscounting need '
              f'different fixes.')

        result = speaker_agreement(reference, ours)
        if result is None:
            w('  no overlap to score -- are the timings in the same units?')
        else:
            w(f'  time-weighted agreement : {result["agreement"] * 100:.1f}%')
            w(f'  of the recording covered : {result["covered"] * 100:.1f}%')
            w('  best label mapping (ours -> theirs):')
            for mine, theirs in sorted(result['mapping'].items()):
                w(f'      {mine:10} -> {theirs}')

        their_words = normalise(' '.join(t for _, _, _, t in reference))
        our_words = normalise(' '.join(t for _, _, _, t in ours))
        wer = word_error_rate(their_words, our_words)
        w('')
        w('WORDS')
        w(f'  accepted : {len(their_words):,} words')
        w(f'  ours     : {len(our_words):,} words')
        w(f'  word error rate : {wer * 100:.1f}%   '
          f'(accuracy {100 - wer * 100:.1f}%)')

        w('')
        w('TIMING')
        w(f'  accepted turns : {len(reference)}')
        w(f'  our segments   : {len(ours)}')
        if reference and ours:
            w(f'  accepted ends at : {max(e for _, _, e, _ in reference) / 60:.1f} min')
            w(f'  ours ends at     : {max(e for _, _, e, _ in ours) / 60:.1f} min')
        w('')
