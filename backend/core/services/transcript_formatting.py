"""
Groups timestamped ASR/diarization segments into human-readable paragraphs
for PRESENTATION only -- TXT/DOCX exports and the frontend transcript view.

Deliberately separate from VTT/SRT (see export_formats.to_srt/to_vtt),
which always render one cue per original TranscriptSegment for precise
timestamp accuracy, and separate from the database itself: TranscriptSegment
rows are never merged or rewritten by this. It's a pure, O(n) read-time
transform, applied on top of already-generated ASR/diarization output --
no model inference, no LLM call, no re-transcription.

Conceptual pipeline this sits at the bottom of:
    Audio -> ASR segments -> diarization alignment (if enabled) ->
    TranscriptSegment rows -> **paragraph grouping (this module)** ->
    human-readable UI / TXT / DOCX
"""
import re
from dataclasses import dataclass
from typing import Iterable, List

# A pause this long (seconds) between two segments is treated as a natural
# paragraph break on its own, regardless of punctuation or speaker.
PARAGRAPH_GAP_SECONDS = 2.0

# Once a paragraph's accumulated text reaches this length, the next segment
# that ends in sentence-final punctuation closes the paragraph -- stops a
# paragraph from growing indefinitely just because the speaker never pauses.
#: The shortest block worth standing alone, from the coach's own accepted
#: work: his first quartile is 38 characters. Below this a fragment is a
#: "Right." or a "Yes." and belongs with its neighbour.
PARAGRAPH_MIN_CHARS = 40

PARAGRAPH_SOFT_MAX_CHARS = 500

# Hard cap: a paragraph is always closed once it reaches this length, even
# without terminal punctuation, so a transcript with unusual/missing
# punctuation can never produce one giant wall of text.
PARAGRAPH_HARD_MAX_CHARS = 1000

_SENTENCE_END_SUFFIXES = ('.', '!', '?', '."', '!"', '?"', ".'", "!'", "?'")


@dataclass
class Paragraph:
    speaker_label: str  # '' whenever diarization is off/unavailable for this stretch
    start_time: float
    end_time: float
    text: str


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(_SENTENCE_END_SUFFIXES)


def _get(seg, key, default=None):
    """
    A missing key/attribute is a MISSING VALUE, not a crash.

    Every call site already treats the result as possibly-absent -- `or ''`,
    `or 0.0`, `or _get(seg, 'speaker')` -- which only works if a genuinely
    missing value comes back as something falsy rather than raising.
    `getattr(seg, key)` with no default broke that contract for the one
    non-dict case: a real transcription with every speaker_label blank (a
    diarization outage, not hypothetical -- confirmed live) reached the
    'speaker' fallback on line ~278, and a SimpleNamespace row has no such
    attribute at all. AttributeError, 500, submission blocked -- for every
    transcription task diarization failed on, not just an edge case.
    """
    return seg.get(key, default) if isinstance(seg, dict) else getattr(seg, key, default)


def group_segments_into_paragraphs(segments: Iterable, diarization_enabled: bool) -> List[Paragraph]:
    """
    segments: iterable of objects/dicts each exposing start_time, end_time,
    text, speaker_label -- TranscriptSegment, TranslatedSegment, and plain
    dicts all satisfy this.

    diarization_enabled: whether a speaker_label change is treated as a
    paragraph boundary at all. When False, every segment's speaker_label is
    IGNORED for grouping (even if it happens to be non-empty -- e.g. stale
    "Speaker 0"/"Speaker 1" data from a job transcribed before this fix
    existed): only pause/length/punctuation signals apply, and every
    resulting paragraph's speaker_label is forced to ''. This is what lets
    old jobs' leftover fake labels stop being displayed without any data
    migration -- the gate is the job's enable_diarization flag, never
    whatever happens to already be stored on the row.
    """
    paragraphs: List[Paragraph] = []
    cur_speaker = ''
    cur_start = 0.0
    cur_end = 0.0
    cur_parts: List[str] = []

    def flush():
        if cur_parts:
            paragraphs.append(Paragraph(
                speaker_label=cur_speaker,
                start_time=cur_start,
                end_time=cur_end,
                text=' '.join(cur_parts).strip(),
            ))

    for seg in segments:
        start = _get(seg, 'start_time')
        end = _get(seg, 'end_time')
        text = (_get(seg, 'text') or '').strip()
        speaker = (_get(seg, 'speaker_label') or '') if diarization_enabled else ''
        if not text:
            continue

        cur_text = ' '.join(cur_parts)
        starts_new = False
        if not cur_parts:
            starts_new = True
        elif diarization_enabled and speaker != cur_speaker:
            starts_new = True
        elif (start - cur_end) >= PARAGRAPH_GAP_SECONDS:
            starts_new = True
        elif len(cur_text) >= PARAGRAPH_HARD_MAX_CHARS:
            starts_new = True
        elif len(cur_text) >= PARAGRAPH_SOFT_MAX_CHARS and _ends_sentence(cur_text):
            starts_new = True

        if starts_new:
            flush()
            cur_speaker = speaker
            cur_start = start
            cur_end = end
            cur_parts = [text]
        else:
            cur_parts.append(text)
            cur_end = end

    flush()
    return paragraphs


_SENTENCE_END_SUFFIXES = (
    '.', '!', '?', '…', '...', '."', '!"', '?"', ".'", "!'", "?'",
    '.”', '!”', '?”', '.’', '!’', '?’'
)

_MID_SENTENCE_PUNCTUATION = (',', ';', ':', '-', '–', '—')

_CONTINUATION_WORDS = {
    'that', 'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how',
    'and', 'or', 'but', 'nor', 'for', 'yet', 'so',
    'because', 'since', 'as', 'although', 'though', 'even', 'if', 'unless',
    'while', 'whereas', 'whether',
    'of', 'in', 'to', 'for', 'with', 'on', 'at', 'from', 'by', 'about',
    'into', 'through', 'after', 'over', 'between', 'out', 'against',
    'during', 'without', 'before', 'under', 'around', 'among',
    'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did',
    'can', 'could', 'should', 'would', 'may', 'might', 'must',
}

_TRANSITION_STARTERS = (
    'all right', 'first', 'second', 'third', 'now', 'so', 'however',
    'well', 'then', 'therefore', 'in conclusion', 'finally', 'moreover',
    'furthermore', 'on the other hand', 'what about', 'let\'s turn',
    'in addition', 'for example', 'as a result'
)


def _is_sentence_complete(text: str) -> bool:
    t = text.rstrip()
    return any(t.endswith(s) for s in _SENTENCE_END_SUFFIXES)


def _is_mid_clause(text: str) -> bool:
    t = text.rstrip()
    if any(t.endswith(p) for p in _MID_SENTENCE_PUNCTUATION):
        return True
    return not _is_sentence_complete(t)


# Split on terminal punctuation ONLY when the next thing looks like a fresh
# sentence: whitespace then a capital or a digit. That leaves "A.J. Ayer",
# "1.95" and "Mr. Smith" intact, which a bare split on "." would shred.
_CLOSERS = '"”’\')]'
_OPENERS = 'A-Z0-9"“‘\'(\\['
_SENTENCE_SPLIT_RE = re.compile(
    '(?<=[.!?])[' + re.escape(_CLOSERS) + ']?\\s+(?=[' + _OPENERS + '])'
)


def split_paragraph_into_sentences(paragraph: 'Paragraph',
                                   min_chars: int = None) -> List['Paragraph']:
    """
    One sentence per block, with timings shared out across it.

    WHY THIS IS NEEDED AT ALL. Our ASR cuts on length, not on grammar: of 44
    segments in a five-minute lecture, 10 ended on terminal punctuation and
    32 began lower-case. Grouping them so that no sentence is ever cut in
    half therefore produces blocks holding four or five sentences -- and the
    coach's verdict on exactly that document was "the paragraph is too big".

    HIS OWN ACCEPTED WORK IS ONE SENTENCE PER BLOCK: 323 blocks with a
    63-character median, against a 59-character median sentence. Our speaker
    writes 76-character sentences, so the same shape is reachable -- but only
    by splitting the merged text back apart on sentence boundaries, because
    the ASR never offered those boundaries in the first place.

    TIMESTAMPS ARE SHARED OUT BY CHARACTER COUNT. Speech is not uniform, so
    this is an approximation -- but it is an approximation INSIDE a span the
    ASR already measured, so the error is bounded by one segment and lands
    well under a second in practice. The alternative is a block shape the
    coach has already rejected.

    The block's own start and end are preserved exactly: only the interior
    boundaries are interpolated, so the chain of timestamps stays unbroken.
    """
    if min_chars is None:
        min_chars = PARAGRAPH_MIN_CHARS

    text = (paragraph.text or '').strip()
    pieces = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    if len(pieces) <= 1:
        return [paragraph]

    # A fragment shorter than the coach's own first quartile is a "Right."
    # or a "Yes." and belongs with its neighbour rather than alone.
    merged: List[str] = []
    for piece in pieces:
        if merged and len(piece) < min_chars:
            merged[-1] = f'{merged[-1]} {piece}'
        elif merged and len(merged[-1]) < min_chars:
            merged[-1] = f'{merged[-1]} {piece}'
        else:
            merged.append(piece)
    if len(merged) <= 1:
        return [paragraph]

    total = float(sum(len(m) for m in merged)) or 1.0
    span = max(0.0, float(paragraph.end_time) - float(paragraph.start_time))
    out: List[Paragraph] = []
    cursor = float(paragraph.start_time)
    for index, piece in enumerate(merged):
        # The final piece takes the block's real end, so rounding can never
        # shorten or extend the span the ASR actually reported.
        if index == len(merged) - 1:
            end = float(paragraph.end_time)
        else:
            end = cursor + span * (len(piece) / total)
        out.append(Paragraph(
            speaker_label=paragraph.speaker_label,
            start_time=cursor,
            end_time=max(end, cursor),
            text=piece,
        ))
        cursor = max(end, cursor)
    return out


def group_segments_into_eagle_paragraphs(segments: Iterable) -> List[Paragraph]:
    """
    Intelligently merges consecutive ASR segments from the same speaker into
    coherent sentences and paragraphs for Eagle Web final submission format.

    Reconstructs natural, grammatically complete sentences/paragraphs rather than
    blindly preserving every raw ASR timestamp chunk as a separate block.

    Combines:
    - Punctuation (. ! ? …)
    - Grammatical continuity (mid-clause words, lowercase starters, conjunctions)
    - Speaker continuity (speaker changes always break)
    - Natural topic / discourse pauses (gap >= 2.5s fallback)
    """
    paragraphs: List[Paragraph] = []
    cur_speaker = ''
    cur_start = 0.0
    cur_end = 0.0
    cur_parts: List[str] = []

    def flush():
        if cur_parts:
            paragraphs.append(Paragraph(
                speaker_label=cur_speaker or 'Speaker 1',
                start_time=cur_start,
                end_time=cur_end,
                text=' '.join(cur_parts).strip(),
            ))

    for seg in segments:
        text = (_get(seg, 'text') or '').strip()
        if not text:
            continue
        start = float(_get(seg, 'start_time') or 0.0)
        end = float(_get(seg, 'end_time') or start)
        speaker = (_get(seg, 'speaker_label') or _get(seg, 'speaker') or '')

        starts_new = False
        if not cur_parts:
            starts_new = True
        elif speaker != cur_speaker:
            starts_new = True
        else:
            prev_text = ' '.join(cur_parts).strip()
            gap = start - cur_end
            ends_punct = _is_sentence_complete(prev_text)
            mid_clause = _is_mid_clause(prev_text)

            first_word = text.split()[0].lower().strip(" \t\n\r\"'([{") if text.split() else ""
            is_continuation_word = first_word in _CONTINUATION_WORDS
            starts_lower = text[0].islower() if text else False

            # A SENTENCE IS NEVER CUT IN HALF. THIS HAS TO BE CHECKED FIRST.
            #
            # This rule already existed, and already said "never break
            # mid-sentence" -- but it sat BELOW the size cap and the pause
            # rule, so both of those broke mid-sentence routinely and the
            # guard was unreachable exactly when it was needed. A real block
            # came out ending "...as though the purpose", with the next
            # opening "of philosophy was just getting clear...".
            #
            # Measured against work the platform accepted: across 366 blocks
            # in two accepted documents, exactly ONE does not end on terminal
            # punctuation, and that one ends on a deliberate colon. A block
            # that stops mid-phrase is not a style difference, it is a defect
            # a reviewer sees immediately.
            #
            # So the caps become what they should always have been: a signal
            # to break at the NEXT opportunity, not a licence to break here.
            # A CONTINUATION WORD IS ONLY A CONTINUATION IF THE SENTENCE
            # BEFORE IT DID NOT FINISH.
            #
            # `is_continuation_word` lowercases the next segment's first word
            # before matching, and the word list holds "so", "and", "but",
            # "now", "in", "to", "as". A lecture sentence opening "So..." or
            # "Now..." -- capitalised, plainly new -- therefore matched, and
            # nearly every sentence got glued to the one before it. That is
            # how a 63-character house style became a 1,234-character block.
            #
            # `starts_lower` still merges across a COMPLETED sentence,
            # deliberately: ASR drops spurious full stops mid-phrase, and
            # "...as though the purpose." followed by "of philosophy..." is
            # one sentence wearing a false ending.
            continues_sentence = (
                mid_clause
                or starts_lower
                or (is_continuation_word and not ends_punct)
            )

            # The runaway guard, and only that. A transcript with no
            # punctuation anywhere would otherwise become one block for the
            # whole recording, which is worse than a long one. Set well above
            # the caps so it is a genuine last resort rather than a routine
            # rule -- the longest block in either accepted document is 51
            # seconds of speech, far short of this.
            runaway = len(prev_text) >= PARAGRAPH_HARD_MAX_CHARS * 2

            # 1. Mid-sentence: hold on, whatever the size or the pause.
            if continues_sentence and not runaway:
                starts_new = False
            # 2. A FINISHED SENTENCE IS A BLOCK BOUNDARY.
            #
            #    The coach's own accepted transcript runs a 63-character
            #    median block -- first quartile 38, third 105, longest 330
            #    across 323 blocks. Ours was producing 1,100-character
            #    blocks, and his feedback on a real submission was exactly
            #    "the paragraph is too big".
            #
            #    So the default is now to break wherever a sentence ends,
            #    rather than to accumulate until a size cap notices. Only a
            #    fragment shorter than the coach's own first quartile is
            #    held back, which keeps a bare "Right." or "Yes." attached
            #    to the sentence it belongs with instead of standing alone.
            elif ends_punct and len(prev_text) >= PARAGRAPH_MIN_CHARS:
                starts_new = True
            # 3. Hard safety cap (oversized block prevention)
            elif len(prev_text) >= PARAGRAPH_HARD_MAX_CHARS:
                starts_new = True
            # 4. Significant audio pause/silence (natural topic or turn pause)
            elif gap >= 2.0:
                starts_new = True
            # 4. Short interjections & brief rapid sentences (e.g. "Right.", "Mike.", "Yes.", "Okay."):
            #    If previous text or current segment is short (< 280 chars total) and within same speaker turn without a long pause:
            #    Keep merged into the speaker's surrounding sentence block!
            elif len(prev_text) < 280 and gap < 1.8:
                starts_new = False
            # 5. Preceding sentence is grammatically complete and has reached natural paragraph size
            elif ends_punct:
                starts_new = True
            # 6. Fallback for unpunctuated long discourse with noticeable pause
            elif len(prev_text) >= PARAGRAPH_SOFT_MAX_CHARS and gap >= 1.2:
                starts_new = True

        if starts_new:
            flush()
            cur_speaker = speaker
            cur_start = start
            cur_end = end
            cur_parts = [text]
        else:
            cur_parts.append(text)
            cur_end = max(cur_end, end)

    flush()
    # ONE SENTENCE PER BLOCK -- see split_paragraph_into_sentences.
    split: List[Paragraph] = []
    for paragraph in paragraphs:
        split.extend(split_paragraph_into_sentences(paragraph))
    return split

