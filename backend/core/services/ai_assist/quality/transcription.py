"""
Eagle Web's two transcription standards, as checkable rules.

THE TWO STANDARDS CONTRADICT EACH OTHER, and that is the whole reason this
module exists:

                        EDITED VERBATIM        STRICT VERBATIM
    fillers (um, uh)    remove                 preserve
    contractions        expand (I'm -> I am)   preserve (don't stays)
    slang (gonna)       standardize            preserve
    grammar, run-ons    correct                do NOT correct
    mispronunciations   correct obvious slips  preserve ("supposably")
    false starts        remove                 preserve
    vocalizations       omit ([cough])         record ([coughs])

A transcript that is flawless under one standard is a guaranteed rejection
under the other. This is not a quality gradient -- it is a mode switch, and
getting it wrong fails the whole document no matter how good the words are.
So the standard is DATA (`TranscriptionStandard`), selected per job, and
every check below reads its polarity from that object rather than from an
`if legal:` branch. Same architecture as DomainProfile, same reason.

WHY THESE CHECKS ARE DETERMINISTIC. Almost everything the two standards
grade is mechanically decidable: whether a speaker label matches
`Male_1:`, whether timestamps are continuous, whether "um" is present,
whether a bare 7 was written as a digit. None of it needs a language model,
none of it costs a request, and none of it can hallucinate. That puts the
entire format dimension of Eagle's rubric in Tier 0 -- free, and running on
every job.

What is NOT here, deliberately: whether the words are RIGHT. Fidelity to
the audio is not decidable from the text alone (see the note in engine.py
about the transcript being the gate's source of truth). These checks
establish that a transcript is well-formed under its standard, which is
necessary and very far from sufficient.
"""
import re
from typing import Dict, List, Optional, Sequence

from .issues import IssueCategory, QualityIssue, Severity

# --------------------------------------------------------------------- #
# The standards
# --------------------------------------------------------------------- #

EDITED_VERBATIM = 'edited_verbatim'
STRICT_VERBATIM = 'strict_verbatim'


class TranscriptionStandard:
    """
    One transcription standard's policy, as flags rather than branches.

    Every field is a polarity that some check reads. Adding a third
    standard (a broadcast house style, say) is a new instance here and no
    change to any checker.
    """

    __slots__ = (
        'name', 'label', 'remove_fillers', 'expand_contractions',
        'standardize_slang', 'correct_grammar', 'capitalize_sentences',
        'record_vocalizations', 'paragraph_monologues', 'description',
    )

    def __init__(
        self, name, label, remove_fillers, expand_contractions,
        standardize_slang, correct_grammar, record_vocalizations,
        capitalize_sentences=None, paragraph_monologues=False, description='',
    ):
        self.name = name
        self.label = label
        self.remove_fillers = remove_fillers
        self.expand_contractions = expand_contractions
        self.standardize_slang = standardize_slang
        # FIXING GRAMMAR/RUN-ONS AND CAPITALIZING A SENTENCE START ARE NOT
        # THE SAME RULE, even though one flag used to gate both. Eagle's
        # General Guidelines say two different things about them: Writing
        # Rules is blanket and unconditional -- "do not fix the speaker's
        # grammar" -- while the Capitalization section separately and just
        # as unconditionally lists "first word of a sentence or quote" as
        # required. Reading `correct_grammar` for the capitalization check
        # meant the only way to keep sentences capitalized under Clean Read
        # was to also tell the model to "correct grammar, punctuation and
        # obvious slips of the tongue" (see warmpool.py's
        # standard_instruction) -- exactly what the guideline forbids.
        # `correct_grammar` now means only that instruction; capitalization
        # has its own flag below.
        self.correct_grammar = correct_grammar
        self.capitalize_sentences = (
            correct_grammar if capitalize_sentences is None
            else capitalize_sentences
        )
        self.record_vocalizations = record_vocalizations
        # Edited verbatim breaks long monologues up for readability; strict
        # verbatim must not, because paragraphing is an editorial judgement
        # about where a thought ends and the record is meant to carry the
        # speech, not an editor's reading of it.
        self.paragraph_monologues = paragraph_monologues
        self.description = description


STANDARDS: Dict[str, TranscriptionStandard] = {
    EDITED_VERBATIM: TranscriptionStandard(
        name=EDITED_VERBATIM,
        label='Edited verbatim (professional / academic)',
        # SUPERSEDED, 2026-09-13 -- CONTRACTIONS NO LONGER EXPAND. This
        # standard used to expand every contraction here, on the strength
        # of "the one transcript we know Eagle accepted has 8 contractions
        # in 7,966 words". That count was wrong: re-run against the actual
        # file (benchmark/coach_reference.txt) with this module's own
        # (already-fixed) possessive-vs-contraction regex, the true count
        # is 0, not 8 -- every one of the original "8" was a possessive
        # ("plaintiff's", "court's"), not a contraction. Zero occurrences
        # proves the document was accepted with none in it; it does not
        # prove one was ever encountered and expanded, which is what the
        # old claim rested on.
        #
        # Eagle's General Guidelines, released today and now the policy
        # baseline for this codebase, are explicit and unconditional:
        # "Contractions should be kept exactly as spoken... If a speaker
        # says 'cannot,' do not change it to 'can't.'" -- the same
        # direction the old evidence never actually supported.
        #
        # This does NOT erase the coach's saved prompts or the tasker
        # guide, which independently say the opposite (expand, under
        # edited verbatim) -- see docs/COACH_PROMPTS_REFERENCE.md and the
        # evidence audit this change is based on. That is a real, standing
        # conflict between two real sources, not evidence vs. invention.
        # Per explicit instruction, the newer, external General Guidelines
        # are the authority for this implementation pass; a job-specific
        # instruction that legitimately needs the old behavior back is a
        # new PROJECT_STANDARDS entry (or a third TranscriptionStandard),
        # not a reason to keep this one wrong by default for everyone.
        #
        # GRAMMAR CORRECTION IS OFF for the same reason -- Writing Rules:
        # "Do not fix the speaker's grammar" -- but sentence-start
        # capitalization is NOT the same rule (Capitalization: "first word
        # of a sentence or quote", stated separately and unconditionally),
        # so it is split out to its own flag below rather than silently
        # lost along with grammar-fixing. See TranscriptionStandard's own
        # docstring for why the two were never the same thing.
        #
        # STILL EVIDENCED, UNCHANGED: the category mapping itself (which
        # projects land here at all) traces to the coach's own "Project ->
        # standard mapping (as given)" list, a different question from
        # what this standard DOES once selected -- see PROJECT_STANDARDS
        # below, not touched by this change.
        remove_fillers=True,
        expand_contractions=False,
        standardize_slang=True,
        correct_grammar=False,
        capitalize_sentences=True,
        record_vocalizations=False,
        paragraph_monologues=True,
        description=(
            'Preserves meaning and wording. Fillers and false starts are '
            'removed; slang is standardized; contractions and grammar are '
            'kept exactly as spoken, per Eagle\'s General Guidelines.'
        ),
    ),
    STRICT_VERBATIM: TranscriptionStandard(
        name=STRICT_VERBATIM,
        label='Strict verbatim (legal / court-admissible)',
        remove_fillers=False,
        expand_contractions=False,
        standardize_slang=False,
        correct_grammar=False,
        capitalize_sentences=False,
        record_vocalizations=True,
        paragraph_monologues=False,
        description=(
            'Word-for-word and sound-for-sound. Fillers, false starts, '
            'repetitions, slang and mispronunciations are all preserved; '
            'nothing is corrected.'
        ),
    ),
}


def get_standard(name: Optional[str]) -> Optional[TranscriptionStandard]:
    return STANDARDS.get((name or '').strip().lower()) if name else None


# --------------------------------------------------------------------- #
# Which standard a task uses: the PROJECT decides
# --------------------------------------------------------------------- #
#
# THE COACH'S OWN LIST, verbatim. This is the answer to the question the
# three accepted transcripts raised -- same transcriber, three tasks, two
# standards -- and it is not something to be reasoned out from the audio.
#
# READ THE LABEL, NEVER GUESS FROM THE CONTENT. Two of our own reference
# documents contradict what their subject matter would suggest:
#
#   * `benchmark/coach_reference.txt` is unmistakably a law class -- Federal
#     Rules, pleadings, Rule 8 -- which this table sends to strict. It was
#     transcribed EDITED (contractions expanded throughout) and accepted.
#   * The coach's second document is a philosophy lecture, and "lectures"
#     sends to edited. It is STRICT verbatim from end to end.
#
# On top of that the header titles are demonstrably unrelated to content: one
# accepted document about truth and lying is headed "The Ethics of Artificial
# Intelligence". Nothing a listener can hear is a reliable signal. The
# project label on the task is, so that is the only input here.
PROJECT_STANDARDS: Dict[str, str] = {
    'legal dictation':   STRICT_VERBATIM,
    'legal proceedings': STRICT_VERBATIM,
    'law':               STRICT_VERBATIM,
    'law class':         STRICT_VERBATIM,
    'all':               STRICT_VERBATIM,

    'speech':            EDITED_VERBATIM,
    'lecture':           EDITED_VERBATIM,
    'lectures':          EDITED_VERBATIM,
    'zoom meeting':      EDITED_VERBATIM,
    'news report':       EDITED_VERBATIM,
    'news reports':      EDITED_VERBATIM,
}

# "All" is a project NAME, and it is three letters long. Matched loosely it
# would claim "small group discussion" and "Hall lecture" for strict
# verbatim, which is the expensive direction to be wrong in. Exact only.
_EXACT_ONLY_PROJECTS = frozenset({'all'})

_PROJECT_NORMALISE_RE = re.compile(r'[^a-z ]+')


def standard_for_project(
    project: Optional[str], default: Optional[str] = EDITED_VERBATIM,
) -> Optional[str]:
    """
    The standard a project is transcribed under, or `default` if unknown.

    MATCHING IS TOLERANT because the label is a human-entered string on
    someone else's platform: "Law Class", "law classes" and "Law  class"
    are the same project, and a table that only matched one of them would
    silently fall back to the default and look like it was working.

    Longest key first, so "legal proceedings" is never claimed by "legal"
    and "law class" is never claimed by "law".

    WHY THE DEFAULT IS EDITED VERBATIM: most transcription work on the
    platform is lectures, so it is right far more often than not. It is
    still a guess, and a guess in this particular place produces a document
    that is wrong in every line rather than wrong in a few -- so a caller
    that can tell the difference between "the project said edited" and "we
    assumed edited" should say so on screen.
    """
    cleaned = _PROJECT_NORMALISE_RE.sub(' ', (project or '').lower())
    cleaned = ' '.join(cleaned.split())
    if not cleaned:
        return default

    if cleaned in PROJECT_STANDARDS:
        return PROJECT_STANDARDS[cleaned]

    for key in sorted(PROJECT_STANDARDS, key=len, reverse=True):
        if key in _EXACT_ONLY_PROJECTS:
            continue
        # Word-boundary containment: "chemistry lecture" matches "lecture",
        # "small" does not match "all".
        if re.search(r'\b' + re.escape(key) + r'\b', cleaned):
            return PROJECT_STANDARDS[key]

    return default


# --------------------------------------------------------------------- #
# Shared format rules (identical under both standards)
# --------------------------------------------------------------------- #

# Male_1: / Female_12: -- the ONLY permitted speaker form. Both standards
# forbid names, titles and court roles.
SPEAKER_LABEL_RE = re.compile(r'^(Male|Female)_\d+:$')
_SPEAKER_LINE_RE = re.compile(r'^\s*([A-Za-z_][\w ]*?)\s*:\s*$')

# [HH:MM:SS.mmm --> HH:MM:SS.mmm]
TIMESTAMP_RE = re.compile(
    r'^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]$'
)

# The line that closes every Eagle transcript: [END OF RECORDING --> HH:MM:SS.mmm].
#
# IT IS NOT A MALFORMED BLOCK TIMESTAMP. It borrows the arrow but carries a
# single time -- the total duration -- rather than a range, so TIMESTAMP_RE
# cannot match it and a naive check reports the correct closing line of an
# accepted submission as a defect. Found by running these checks over a real
# rendered document for the first time.
END_MARKER_RE = re.compile(
    # THE SPACE BEFORE THE ARROW IS OPTIONAL. Two documents Eagle accepted
    # write this line differently -- "[END OF RECORDING --> 00:20:14.030]"
    # and "[END OF RECORDING--> 00:25:14.000]" -- so requiring the space
    # reports the closing line of a paid submission as malformed.
    r'^\[END OF RECORDING\s*--> ?(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]$'
)

ALLOWED_TAGS = frozenset({
    'inaudible', 'unclear', 'crosstalk', 'pause', 'laughter', 'applause',
    'gavel bangs', 'door opens', 'coughs', 'clears throat', 'chuckles',
    'sigh', 'gasp',
    # Eagle's Audio Rules name these two directly: [foreign] for a
    # foreign-language stretch inside an otherwise-English file, and
    # [background] for audio that is only background noise (classroom
    # chatter, etc.) with no discernible words. Both were absent, so a
    # tasker correctly following that rule was told by this same check
    # that the tag they were instructed to use is "not agreed" -- the
    # exact "checker that fires on correct work" failure this module's
    # own comments elsewhere warn about. THE CANONICAL SOURCE: this set
    # is the one place the tag vocabulary is defined; warmpool.py's
    # generation prompt now imports it rather than repeating its own
    # copy (see standard_instruction()).
    'foreign', 'background',
})
_TAG_RE = re.compile(r'\[([^\]]+)\]')
# A tag may carry a timestamp: [inaudible 00:04:12.005]
_TAG_TIMESTAMP_RE = re.compile(r'^(.*?)\s+\d{2}:\d{2}:\d{2}\.\d{3}$')


def _seconds(match, offset: int) -> float:
    h, m, s, ms = (int(match.group(offset + i)) for i in range(4))
    return h * 3600 + m * 60 + s + ms / 1000.0


def check_speaker_labels(text: str) -> List[QualityIssue]:
    """
    Every speaker label is `Male_N:` or `Female_N:` on its own line.

    Both standards state this identically, and both forbid names, titles and
    court roles -- so "THE COURT:" or "Attorney Smith:" is a defect even
    though it reads as more informative. CRITICAL because it is graded on
    every line of the document: get the form wrong and the whole transcript
    is wrong, not one line of it.
    """
    issues = []
    for number, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or not line.endswith(':'):
            continue
        if SPEAKER_LABEL_RE.match(line):
            continue
        match = _SPEAKER_LINE_RE.match(line)
        if not match:
            continue
        issues.append(QualityIssue(
            category=IssueCategory.STRUCTURE, severity=Severity.CRITICAL,
            problem=(
                f'Line {number + 1} uses the speaker label "{line}". Only '
                f'Male_N: and Female_N: are permitted; names, titles and '
                f'court roles are not.'
            ),
            correction='Replace with the gender-numbered label for that speaker.',
            target_span=line[:60],
            rule='transcript.speaker-label-format',
            detector='deterministic',
        ))
    return issues


def check_timestamps(text: str) -> List[QualityIssue]:
    """
    Format, ordering, and CONTINUITY -- each timestamp's end must equal the
    next one's start.

    Continuity is the chain-of-custody requirement in the legal standard,
    and it is the one a human proofreader will never catch: spotting that
    00:04:12.005 should have been 00:04:12.006 across two hundred blocks is
    exactly the work a machine should be doing.
    """
    issues = []
    stamps = []
    for number, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line.startswith('['):
            continue
        # Only consider lines that are ONLY a timestamp; an inline
        # [inaudible 00:04:12.005] tag is not a block timestamp.
        if '-->' not in line:
            continue
        # The closing marker is well-formed by its own rule, and it is not a
        # block, so it takes no part in the continuity chain either.
        if END_MARKER_RE.match(line):
            continue
        match = TIMESTAMP_RE.match(line)
        if not match:
            issues.append(QualityIssue(
                category=IssueCategory.STRUCTURE, severity=Severity.HIGH,
                problem=(
                    f'Line {number + 1} is not a valid timestamp. The required '
                    f'form is [HH:MM:SS.mmm --> HH:MM:SS.mmm].'
                ),
                correction='Reformat to [HH:MM:SS.mmm --> HH:MM:SS.mmm].',
                target_span=line[:60],
                rule='transcript.timestamp-format',
                detector='deterministic',
            ))
            continue
        stamps.append((number + 1, _seconds(match, 1), _seconds(match, 5)))

    for index, (line_no, start, end) in enumerate(stamps):
        if end < start:
            issues.append(QualityIssue(
                category=IssueCategory.STRUCTURE, severity=Severity.HIGH,
                problem=f'Timestamp on line {line_no} ends before it starts.',
                correction='Correct the timing values.',
                rule='transcript.timestamp-reversed',
                detector='deterministic',
            ))
        if index + 1 < len(stamps):
            next_line, next_start, _ = stamps[index + 1]
            # Millisecond tolerance: the format carries 3 decimal places, so
            # anything under 1ms is a rounding artifact rather than a gap.
            if abs(next_start - end) > 0.0005:
                # LOW, AND IT USED TO BE HIGH. THREE ACCEPTED DOCUMENTS SAY SO.
                #
                # The coach supplied three transcripts from three separate
                # tasks, all accepted. Two run an unbroken chain. The third
                # does not: 23 discontinuities across 32 blocks, a 60-second
                # hole, and one block that starts BEFORE the one printed
                # above it. The platform paid for it anyway.
                #
                # At HIGH this check produced 23 findings on that document.
                # A tasker with forty minutes left cannot tell a finding that
                # costs the task from one that costs nothing, so 23 loud ones
                # on work that was going to be accepted is not a safety net,
                # it is a reason to stop reading findings at all.
                #
                # The old correction line -- "each block must start exactly
                # where the previous one ended" -- also stated a rule the
                # accepted corpus contradicts. It says something true now.
                #
                # `close_timestamp_gaps()` in eagle_submit still closes these
                # on OUR submissions: the majority of accepted work is
                # continuous, it costs nothing, and matching the common shape
                # is free. That is a house style, not a requirement, and this
                # severity is what says so.
                issues.append(QualityIssue(
                    category=IssueCategory.STRUCTURE, severity=Severity.LOW,
                    problem=(
                        f'Timestamps are not continuous: the block on line '
                        f'{line_no} ends at {end:.3f}s but the next (line '
                        f'{next_line}) starts at {next_start:.3f}s.'
                    ),
                    correction=(
                        'Most accepted transcripts run an unbroken chain, so '
                        'closing the gap matches house style — but accepted '
                        'work exists with gaps, and this alone will not fail '
                        'a submission.'
                    ),
                    rule='transcript.timestamp-continuity',
                    detector='deterministic',
                ))
    return issues


def check_bracket_tags(text: str) -> List[QualityIssue]:
    """Bracketed tags must come from the agreed vocabulary."""
    issues = []
    seen = set()
    for raw in _TAG_RE.findall(text or ''):
        tag = raw.strip()
        stripped = _TAG_TIMESTAMP_RE.match(tag)
        if stripped:
            tag = stripped.group(1).strip()
        if '-->' in tag or TIMESTAMP_RE.match(f'[{raw}]'):
            continue  # a block timestamp, handled above
        key = tag.lower()
        if key in ALLOWED_TAGS or key in seen:
            continue
        seen.add(key)
        issues.append(QualityIssue(
            category=IssueCategory.STRUCTURE, severity=Severity.MEDIUM,
            problem=f'"[{tag}]" is not one of the agreed transcript tags.',
            correction=(
                'Use one of: ' + ', '.join(f'[{t}]' for t in sorted(ALLOWED_TAGS))
            ),
            target_span=f'[{tag}]',
            rule='transcript.unknown-tag',
            detector='deterministic',
        ))
    return issues


# --------------------------------------------------------------------- #
# Number rules (identical under both standards)
# --------------------------------------------------------------------- #

_NUMBER_WORDS = {
    'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
    'seventeen', 'eighteen', 'nineteen', 'twenty', 'thirty', 'forty',
    'fifty', 'sixty', 'seventy', 'eighty', 'ninety', 'hundred', 'thousand',
    'million', 'billion',
    # Eagle's own number guideline names it explicitly alongside million
    # and billion ("Millions, billions, trillions +"); the set stopped one
    # short of that.
    'trillion',
}
#: A digit touching a letter directly, either side, with no space between
#: them: E4, A3 (musical pitch notation), 3D (dimensional shorthand), D1,
#: D0, K1, K2 (variable/identifier labels), 4G (an acceleration multiplier
#: read as "four G"). A spoken NUMBER never has a letter fused onto it like
#: this in real prose -- that shape is specifically what an identifier or a
#: piece of notation looks like, in a genuinely general way, not a list of
#: the specific tokens seen so far. Confirmed on five separate real
#: instances across a 19-document review, none of them a spoken count.
_SMALL_DIGIT_RE = re.compile(r'(?<![\d:.\-/A-Za-z])([0-9])(?![\d:.\-/%A-Za-z])')

#: An exponent read aloud: "ten to the minus six", "ten to the sixteen".
#: The digit here names a power, not a count, and check_verbatim's sibling
#: exception for math notation elsewhere in this codebase makes the same
#: call for "x squared" -- this is the transcription side of that same
#: judgment, for a phrase shape rather than a symbol.
_EXPONENT_PHRASE_RE = re.compile(
    r'\bto\s+the(?:\s+(?:minus|negative))?\s*\Z', re.IGNORECASE,
)

#: A digit inside a spoken mathematical expression, not a count: "2 pi r
#: equals 1" and "2 pi I" (Euler's identity, read aloud twice in the same
#: lecture series), "1 over alpha" (a fraction named the way a speaker
#: says it), "a 3, 4, 5 triangle" (a named Pythagorean triple). Five real
#: instances across four documents in one lecture series had a digit here
#: flagged as though it were an ordinary count that needed spelling out --
#: writing "two pi r" or "one over alpha" garbles the notation being
#: recited, the same call _EXPONENT_PHRASE_RE already makes for "ten to
#: the minus six".
#:
#: Anchored on the specific math vocabulary next to the digit ("pi", a
#: digit-over-word fraction shape) or on three digits chained by commas
#: (a triple), not on "any digit near a letter" -- that would swallow
#: ordinary prose ("I bought 2 or 3 apples" is not a formula).
_MATH_NOTATION_RE = re.compile(
    r'\bpi\b|\d\s+over\s+\w|\w\s+over\s+\d|\d\s*,\s*\d\s*,\s*\d',
    re.IGNORECASE,
)

#: Quantifiers that make a following bare scale word an approximation,
#: not a stated count: "several hundred days" -- the singular counterpart
#: of the "hundreds of days" plural idiom already exempted below. A real
#: submission had exactly this flagged as though "hundred" were an
#: unconverted count; nobody writes "several 100 days".
#:
#: DELIBERATELY EXCLUDES "a"/"an". "A million dollars" is the shape the
#: scale-word exemption above already discusses and leaves un-exempted on
#: purpose -- "a" reads as the exact count one, not an approximation, and
#: widening this set to include it would silently reverse that existing,
#: deliberate call rather than fix the new defect this was written for.
_APPROXIMATE_QUANTIFIER_RE = re.compile(
    r'\b(?:some|several|many|few)[\s-]*\Z', re.IGNORECASE,
)
_SPELLED_LARGE_RE = re.compile(
    r'\b(' + '|'.join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)
# Contexts where a digit is always correct regardless of magnitude.
#
# "years" sits in the SAME duration-unit group as days/weeks/months/hours,
# not carved out as an age-only exception. A real submission had "he gave
# this talk 2 years ago" flagged -- "2 years ago" is a duration, exactly
# the shape "2 days ago" or "2 weeks ago" already exempt one line down.
# There was no principled reason "years" alone required the word "old"
# right after it while every other duration unit needed nothing; that was
# an oversight, not a distinction Eagle's rubric draws. The bare "years?"
# here also still covers an age ("he is 5 years old"), so nothing is lost.
#
# SPELLED-OUT DISTANCE AND WEIGHT UNITS, not just their abbreviations. A
# real submission had "5 kilometers" flagged -- "km" was already exempt
# one line down, but a speaker saying the unit out loud produces
# "kilometers", not "km", and nothing here matched that shape. The time
# units beside these (hours, minutes, days...) were already spelled out
# in full; the distance/weight units were not, for no principled reason.
_ALWAYS_FIGURES_RE = re.compile(
    r'[$£€]\s?\d|\d\s?(?:%|percent|dollars?|pounds?|euros?|'
    r'a\.?m\.?|p\.?m\.?|'
    r'kg|kilograms?|km|kilometers?|cm|centimeters?|mm|millimeters?|'
    r'lbs?|pounds?|oz|ounces?|ft|feet|foot|mi|miles?|'
    r'hours?|minutes?|seconds?|days?|weeks?|months?|years?)\b',
    re.IGNORECASE,
)


def _strip_uncheckable(text: str) -> str:
    """
    Removes regions where the number rules do not apply: timestamps, tags,
    and speaker labels all legitimately contain digits.
    """
    without_stamps = re.sub(r'\[[^\]]*\]', ' ', text or '')
    return re.sub(r'^\s*(?:Male|Female)_\d+:\s*$', ' ', without_stamps,
                  flags=re.MULTILINE)


#: A small digit that belongs to a citation rather than a count: "Rule 8",
#: "Section 4", "Part 2". Both accepted documents write these as figures,
#: because that is how the source names them -- rewriting "Rule 8" as
#: "Rule eight" would be wrong, not merely unusual.
#:
#: CHANNEL/UNIT/QUIZ/STEP/VERSE/SIZE/LINE WERE ADDED FROM EAGLE'S OWN
#: EXAMPLES, not invented: the General Guidelines name every one of them
#: directly -- "Channel 12", "Unit 20", "Quiz 2" under "item in sequence",
#: and "line 3, page 23, paragraph 14, size 36, step 1, verse 2" as the
#: capitalization section's own exceptions list. Verified against real
#: low-number cases the old list mishandled: "Channel 3", "Unit 2", "Quiz
#: 1", "Step 1", "Verse 2" and "Size 6" were all incorrectly flagged as
#: needing to be spelled out before this, exactly the shape "Rule 8" was
#: already protected from.
_CITED_NUMBER_RE = re.compile(
    r'\b(?:Rule|Section|Article|Chapter|Part|Paragraph|Figure|Table|'
    r'Exhibit|Amendment|Clause|Item|Page|No\.?|Channel|Unit|Quiz|Step|'
    r'Verse|Size|Line)\s+\d+\b', re.IGNORECASE)


def check_number_rules(text: str) -> List[QualityIssue]:
    """
    0-9 in words, 10+ in figures -- with the standing exception that money,
    time, dates, percentages, measurements and ages are ALWAYS figures.

    Reported at MEDIUM. These are real rubric points, but they are also the
    most mechanically fixable thing in the document, and a reviewer can
    correct one in a keystroke.
    """
    body = _strip_uncheckable(text)
    issues = []

    for match in _SMALL_DIGIT_RE.finditer(body):
        window = body[max(0, match.start() - 20):match.end() + 20]
        if _ALWAYS_FIGURES_RE.search(window):
            continue
        # "Rule 8", "Section 4" -- the number names a thing, it does not
        # count one. Both accepted documents write these as figures, and
        # "Rule eight" would be wrong rather than merely unusual.
        if _CITED_NUMBER_RE.search(window):
            continue
        # "ten to the minus 6" -- an exponent, not a count. Checked against
        # what comes immediately BEFORE the digit specifically, not a
        # symmetric window, because the phrase has to land right there for
        # this digit to be the power in it.
        before = body[max(0, match.start() - 20):match.start()]
        if _EXPONENT_PHRASE_RE.search(before):
            continue
        # "2 pi r equals 1", "1 over alpha", "a 3, 4, 5 triangle" -- a
        # digit inside spoken mathematical notation, not a count. See
        # _MATH_NOTATION_RE.
        if _MATH_NOTATION_RE.search(window):
            continue
        issues.append(QualityIssue(
            category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
            problem=(
                f'"{match.group(1)}" is written as a figure. Numbers zero '
                f'to nine are written as words unless they are money, a '
                f'time, a date, a percentage, a measurement or an age.'
            ),
            correction='Write it as a word.',
            target_span=window.strip()[:60],
            rule='transcript.number-small-as-figure',
            detector='deterministic',
        ))
        break  # one finding per document; the rule is systematic, not local

    for match in _SPELLED_LARGE_RE.finditer(body):
        window = body[max(0, match.start() - 30):match.end() + 30]
        # "hundreds of people" and "a thousand times" are idiom, not counts.
        if re.search(r'\b(?:hundreds|thousands|millions|billions|trillions)\b',
                     window, re.IGNORECASE):
            continue
        # "50 million", "fifty million" -- a real submission had "50
        # million Hispanics" flagged, as though "million" itself were an
        # unconverted count. It is not: "50" is already the figure: the
        # count. "million" is a SCALE WORD riding on that count, and
        # spelling it out is the standard, correct way to write it --
        # nobody writes "50,000,000 Hispanics". Guarded on an actual
        # numeral immediately before it, digit or spelled out, so a bare
        # "a million dollars" with nothing to scale is unaffected and
        # still gets its own finding if the rubric wants one.
        word = match.group(1).lower()
        if word in ('hundred', 'thousand', 'million', 'billion', 'trillion'):
            before = body[max(0, match.start() - 15):match.start()]
            if re.search(
                r'(?:\d|' + '|'.join(_NUMBER_WORDS) + r')[\s-]*\Z',
                before, re.IGNORECASE,
            ):
                continue
            # "several hundred days" -- the singular counterpart of the
            # "hundreds of days" plural idiom exempted above. See
            # _APPROXIMATE_QUANTIFIER_RE for why "a"/"an" stay excluded.
            if _APPROXIMATE_QUANTIFIER_RE.search(before):
                continue
        issues.append(QualityIssue(
            category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
            problem=(
                f'"{match.group(1)}" is spelled out. Numbers of ten and above '
                f'are written as figures.'
            ),
            correction='Write it as a figure.',
            target_span=window.strip()[:60],
            rule='transcript.number-large-as-word',
            detector='deterministic',
        ))
        break

    return issues


# --------------------------------------------------------------------- #
# Standard-dependent rules -- the ones that INVERT
# --------------------------------------------------------------------- #

_FILLERS = ('um', 'uh', 'er', 'ah', 'mm', 'hmm')
_FILLER_RE = re.compile(r'\b(' + '|'.join(_FILLERS) + r')\b[,.]?', re.IGNORECASE)
# Deliberately excludes "uh-huh"/"mm-hmm", which BOTH standards keep: they
# are affirmations carrying meaning, not hesitation noise.
_AFFIRMATION_RE = re.compile(r'\b(?:uh-huh|mm-hmm|uh-uh|mm-mm)\b', re.IGNORECASE)

_SLANG = {
    'gonna': 'going to', 'wanna': 'want to', 'gotta': 'got to',
    'kinda': 'kind of', 'sorta': 'sort of', 'dunno': "do not know",
    'cause': 'because', 'nope': 'no',
    # "yeah" DELIBERATELY ABSENT. Eagle's General Guidelines' own Writing
    # Rules table names "yeah" (not yep, not yup) as the REQUIRED correct
    # spelling -- it is the answer, not a defect: "Are you free tonight?
    # Yeah, I am." This entry used to map it to "yes", which had the
    # standard actively rewrite a word Eagle's own table says is already
    # correct. No evidence anywhere (Eagle's guide, the coach's saved
    # prompts, or the tasker guide) supports this specific mapping; it
    # was an unevidenced addition, unlike the rest of this table, which
    # traces to the coach's explicit "gonna -> going to"-style examples.
    # The other entries are left exactly as they were: the guide doesn't
    # address them either way, and removing them without evidence would
    # be the same mistake in the opposite direction.
}
#: "cause" is genuinely ambiguous once the apostrophe is dropped: 'cause
#: (short for "because", a conjunction) and the ordinary verb "to cause"
#: share a spelling. A real submission had "to cause this walker to move
#: along this logarithmic spiral" flagged as informal; a second had "will
#: cause the landlord to evict the grandmother" flagged the same way --
#: same verb, different auxiliary in front of it, still not "'cause".
#:
#: The conjunction "'cause" is never preceded by an infinitive "to" or by
#: a modal auxiliary -- "he will 'cause I said so" and "he can 'cause I
#: said so" are not English -- so excluding "cause" after any of them
#: removes the whole class of false positive, not just the one instance
#: that happened to be seen first.
_CAUSE_VERB_PRECEDERS = (
    'to ', 'will ', 'would ', 'can ', 'could ',
    'may ', 'might ', 'must ', 'should ', 'shall ',
)
_SLANG_RE = re.compile(
    r'\b(' + '|'.join(k for k in _SLANG if k != 'cause') + r')\b'
    r'|' + ''.join(f'(?<!{w})' for w in _CAUSE_VERB_PRECEDERS) + r'\bcause\b',
    re.IGNORECASE,
)

#: Hosts after which 's IS a contraction of "is" or "has" -- pronouns,
#: demonstratives and question words, where English has no competing
#: possessive reading ("it's", "that's", "who's", "there's", "let's").
#:
#: EVERYTHING ELSE ENDING IN 's IS A POSSESSIVE, NOT A CONTRACTION. A real
#: submission had "Archie Cox's office" -- a correctly-formed possessive --
#: picked as the example finding for "contractions remain", with the
#: correction text literally reading "Expand contractions: I'm -> I am,
#: don't -> do not". A worker taking that at face value turns a correct
#: sentence into "Archie Cox is office". The old pattern matched \w+'s
#: unconditionally, so every possessive proper noun and common noun in the
#: document (Cox's, the judge's ruling, the Justice's position) inflated
#: the count and was eligible to be chosen as the example -- on a document
#: full of names and titles, that is most of what "'s" is doing.
#:
#: The other suffixes (n't, 're, 've, 'll, 'd, 'm) have no possessive
#: reading at all -- there is no word "dog're" or "table've" -- so they stay
#: unrestricted.
_CONTRACTION_S_HOSTS = (
    'i', 'you', 'he', 'she', 'it', 'we', 'they',
    'that', 'this', 'there', 'here',
    'what', 'who', 'where', 'when', 'how', 'why',
    'one', 'someone', 'somebody', 'everyone', 'everybody', 'let',
)
_CONTRACTION_RE = re.compile(
    r"\b(?:" + '|'.join(_CONTRACTION_S_HOSTS) + r")'s\b"
    r"|\b\w+'(?:t|re|ve|ll|d|m)\b",
    re.IGNORECASE,
)
# (?!')  A GENUINE REPEAT, NOT THE START OF A CONTRACTION SHARING A STEM.
#
# A real submission had "he thought that that's a better story" -- the
# complementizer "that" followed by the demonstrative contraction "that's",
# both grammatically required, not a stutter. \b matches between "that"
# and the apostrophe that follows it (an apostrophe is a non-word
# character), so the bare pattern read "that" immediately by "that's" as
# the word "that" repeated twice. The lookahead refuses a match wherever
# the second occurrence is immediately followed by an apostrophe -- i.e.
# wherever it is actually the stem of a longer word, not a repeat of the
# same word.
_REPEATED_WORD_RE = re.compile(r"\b(\w+)\s+\1(?!')\b", re.IGNORECASE)

#: Words that legitimately double in English, where a shape-only detector
#: cannot tell intentional repetition from a stutter -- it can only be
#: told which specific words are known to double on purpose:
#:
#:   * "that that" -- a demonstrative or relative pronoun following the
#:     complementizer "that" ("critics that that point to..."), or a
#:     nested clause ("we think that that is a new problem"). Confirmed
#:     across FOUR separate real submissions, with no apostrophe involved
#:     in most of them, so the contraction-lookahead above does not cover
#:     this shape -- it is a different word entirely on the second side.
#:   * "had had" -- ordinary past perfect ("segregation had had an
#:     effect"), not a stutter.
#:   * "long long" -- emphatic doubling of an adjective for effect ("a
#:     long, long time"), a normal spoken-English intensifier.
#:
#: Evidenced, not invented: this stays a short list of the words actually
#: seen doubling on purpose, not a guess at every word that ever could.
#: A genuine stutter on any OTHER word ("the the point", "I I think")
#: still fires exactly as before.
_LEGITIMATE_DOUBLED_WORDS = frozenset({'that', 'had', 'long'})

# Below this length, the ABSENCE of something proves nothing. People do
# speak fluently for a sentence or two, so a short excerpt containing no
# filler is unremarkable. Only the "wrongly removed / wrongly expanded"
# checks consult this -- the PRESENCE of a defect is evidence at any length.
MIN_WORDS_FOR_ABSENCE_CLAIM = 200


def _count_outside_tags(pattern, text: str) -> List[str]:
    body = re.sub(r'\[[^\]]*\]', ' ', text or '')
    return [m.group(0) for m in pattern.finditer(body)]


def check_standard_policy(
    text: str, standard: TranscriptionStandard,
) -> List[QualityIssue]:
    """
    The rules whose polarity FLIPS between the two standards.

    Every finding here names the standard, because the same observation is
    a defect under one and required under the other -- and a reviewer
    reading "filler words present" with no context cannot tell which.
    """
    issues = []
    if standard is None:
        return issues

    fillers = _count_outside_tags(_FILLER_RE, _AFFIRMATION_RE.sub(' ', text))
    slang = _count_outside_tags(_SLANG_RE, text)
    contractions = _count_outside_tags(_CONTRACTION_RE, text)
    # See _LEGITIMATE_DOUBLED_WORDS: "had had", "that that", "long long"
    # are words known to double on purpose, not stutters.
    repeats = [
        r for r in _count_outside_tags(_REPEATED_WORD_RE, text)
        if r.split()[0].lower() not in _LEGITIMATE_DOUBLED_WORDS
    ]

    if standard.remove_fillers and fillers:
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.HIGH,
            problem=(
                f'{len(fillers)} filler word(s) remain (e.g. "{fillers[0]}"). '
                f'The {standard.label} standard removes them.'
            ),
            correction='Remove fillers, false starts and word repetitions.',
            target_span=fillers[0],
            rule='transcript.fillers-not-removed',
            detector='deterministic',
        ))
    elif (
        not standard.remove_fillers
        and not fillers
        and len(text.split()) > MIN_WORDS_FOR_ABSENCE_CLAIM
    ):
        # The inverse defect, and the one nobody thinks to look for: a
        # strict-verbatim transcript with no hesitation in it has been
        # cleaned up, which is precisely what that standard forbids.
        #
        # LENGTH-GUARDED, because absence is only evidence at scale. A
        # two-sentence excerpt containing no "um" is entirely ordinary --
        # people do speak fluently for a sentence or two -- and firing there
        # made every short strict-verbatim sample report a false defect.
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.MEDIUM,
            problem=(
                f'No filler words appear anywhere. The {standard.label} '
                f'standard preserves them, so this transcript may have been '
                f'cleaned up.'
            ),
            correction='Preserve hesitations, false starts and repetitions.',
            rule='transcript.fillers-wrongly-removed',
            detector='deterministic',
        ))

    if standard.standardize_slang and slang:
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.HIGH,
            problem=(
                f'Informal forms remain (e.g. "{slang[0]}"). The '
                f'{standard.label} standard standardizes them.'
            ),
            correction=(
                'Expand to the formal equivalent, e.g. '
                + ', '.join(f'{k} -> {v}' for k, v in list(_SLANG.items())[:3])
            ),
            target_span=slang[0],
            rule='transcript.slang-not-standardized',
            detector='deterministic',
        ))

    # A RATE, NOT A COUNT.
    #
    # The one transcript we know Eagle accepted contains 8 contractions in
    # 7,966 words -- about 1 per 1,000 -- and firing on it at HIGH is
    # exactly the false alarm that teaches people to ignore the gate. A
    # transcript where the standard was never applied looks nothing like
    # that: raw speech runs 40-50 per 1,000.
    #
    # So the question is not "are there any" but "was the standard applied
    # at all". Ten per thousand sits an order of magnitude above the
    # accepted document and well below untouched speech.
    words = len(re.findall(r"[A-Za-z0-9']+", text)) or 1
    dense = (len(contractions) / words) * 1000 >= 10.0
    if standard.expand_contractions and contractions and dense:
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.HIGH,
            problem=(
                f'{len(contractions)} contraction(s) remain (e.g. '
                f'"{contractions[0]}"). The {standard.label} standard expands '
                f'them.'
            ),
            correction='Expand contractions: I\'m -> I am, don\'t -> do not.',
            target_span=contractions[0],
            rule='transcript.contractions-not-expanded',
            detector='deterministic',
        ))
    elif not standard.expand_contractions and not contractions and len(text.split()) > 200:
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.MEDIUM,
            problem=(
                f'No contractions appear in a transcript of this length. The '
                f'{standard.label} standard preserves them exactly as spoken.'
            ),
            correction='Do not expand contractions; transcribe as uttered.',
            rule='transcript.contractions-wrongly-expanded',
            detector='deterministic',
        ))

    if not standard.remove_fillers and repeats:
        pass  # repetitions are correct under strict verbatim
    elif standard.remove_fillers and repeats:
        issues.append(QualityIssue(
            category=IssueCategory.TRANSCRIPT, severity=Severity.MEDIUM,
            problem=(
                f'Repeated word(s) remain (e.g. "{repeats[0]}"). The '
                f'{standard.label} standard removes unintended repetition.'
            ),
            correction='Remove the duplicated word.',
            target_span=repeats[0],
            rule='transcript.repetition-not-removed',
            detector='deterministic',
        ))

    return issues


# --------------------------------------------------------------------- #
# Capitalization, paragraphing, quotes, speaker numbering
#
# The four rubric rules that are decidable from the text alone and were
# previously unenforced. All Tier 0: no provider call, no cost.
#
# Two are edited-verbatim only. Strict verbatim explicitly forbids adding
# capitalization to tidy a run-on, and forbids paragraphing, because both
# are an editor's reading of where a thought ends -- and the legal record is
# meant to carry the speech, not the reading.
# --------------------------------------------------------------------- #

# Abbreviations whose full stop does NOT end a sentence. Without these,
# "e.g. this" and "Dr. smith" each report a capitalization defect.
_ABBREVIATIONS = (
    'e.g', 'i.e', 'etc', 'vs', 'mr', 'mrs', 'ms', 'dr', 'prof', 'st',
    'jr', 'sr', 'no', 'approx', 'cf', 'al', 'inc', 'ltd', 'co',
)
# A sentence-ending mark followed by a lower-case letter. The lookbehind
# excludes decimals (3.14) and ellipses, which end nothing.
_SENTENCE_START_RE = re.compile(r'(?<![.\d])([.!?])\s+([a-z])')
_BLOCK_START_RE = re.compile(r'\A\s*([a-z])')

# Curly quotation marks, and single quotes used AS quotation marks (opened
# after whitespace, so an apostrophe inside a word is never matched).
_CURLY_QUOTE_RE = re.compile('[“”‘’]')
_SINGLE_QUOTED_RE = re.compile(r"(?<=\s)'[^']{3,}'")

_SPEAKER_NUMBER_RE = re.compile(r'^(Male|Female)_(\d+):$', re.MULTILINE)

# A single speaker block longer than this, with no blank line in it, is a
# wall of text. Eagle asks for logical paragraphs "for academic
# readability"; 180 words is roughly a dense half-page.
MAX_UNBROKEN_BLOCK_WORDS = 180


def _prose_blocks(text):
    """
    The speech, with speaker labels and timestamp lines removed.

    Split on timestamps rather than blank lines, because a timestamp is what
    actually starts a new turn -- and paragraphing WITHIN a turn is exactly
    what check_paragraphing measures.
    """
    blocks, current = [], []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if SPEAKER_LABEL_RE.match(line) or ('-->' in line and line.startswith('[')):
            if current:
                blocks.append('\n'.join(current))
                current = []
            continue
        current.append(raw)
    if current:
        blocks.append('\n'.join(current))
    return [b for b in blocks if b.strip()]


#: The final .!? of a block, allowing a trailing quote/bracket after it --
#: but NOT a trailing ellipsis (three dots, or the single unicode
#: character), because that specifically means the block was cut off
#: rather than finished. The two lookbehinds are independently
#: fixed-width, which Python's re allows chained like this even though
#: neither alone covers both ellipsis spellings.
_TERMINAL_PUNCTUATION_RE = re.compile(r'(?<!\.\.)(?<!…)[.!?]["\')\]]?\s*\Z')

#: A leading ellipsis, three dots or the single unicode character -- the
#: explicit signal that THIS block picks up mid-sentence. Only the first
#: block of a document gets to rely on this signal alone: a bare lower-
#: case first block with no such marker is not evidenced anywhere in the
#: documents reviewed, so it stays a real, flaggable defect rather than
#: getting the same blanket benefit of the doubt every later block gets
#: from checking what came before it.
_LEADING_ELLIPSIS_RE = re.compile(r'\A\s*(?:\.\.\.|…)')


def check_capitalization(text):
    """
    RECORDINGS START MID-SENTENCE, and both accepted submissions do. Eagle
    chops long lectures into 25-minute parts, so part two opens on half a
    clause -- "lawyer in your future." -- with a lower-case first word that
    is exactly right.

    LOW, therefore, not MEDIUM: worth mentioning, never worth acting on
    without looking. Both documents the platform paid for trip this check.

    A LOWER-CASE BLOCK START IS NOT ALWAYS THIS DEFECT -- TWO CONFIRMED
    SHAPES, ONE RULE EACH. The FIRST block of a document gets the benefit
    of the doubt only when it carries the explicit signal, a leading
    "...": the recording itself can start mid-sentence, exactly as this
    docstring's opening paragraph says, but a bare lower-case first block
    with nothing marking it that way is still a real, flaggable defect --
    that shape is not evidenced in anything reviewed here, and staying
    silent on it would blind the check to the first line of every
    genuinely uncapitalized transcript. EVERY LATER block is judged
    differently, by what came before it rather than by its own leading
    character: a block that opens "he said God made the whole numbers",
    continuing the PREVIOUS block's "...but even to Kronecker", carries no
    ellipsis of its own anywhere -- the tell there is that the PREVIOUS
    block never reached genuine terminal punctuation, so this one cannot
    be starting a new sentence. A block that itself trails off on "..."
    also does not count as having ended, so the block after IT gets the
    same pass.

    Sentences start with a capital. EDITED VERBATIM ONLY.

    Strict verbatim forbids this outright -- "do NOT add artificial periods,
    semicolons, or capitalization to fix run-on sentences" -- so running it
    there would report a defect on a correct legal transcript.

    One finding per document: a transcript that does not capitalize has one
    systematic problem, not forty.
    """
    blocks = _prose_blocks(text)
    for index, block in enumerate(blocks):
        first = _BLOCK_START_RE.search(block)
        if first:
            if index == 0:
                starts_a_real_new_sentence = not _LEADING_ELLIPSIS_RE.match(block)
            else:
                previous = blocks[index - 1].strip()
                starts_a_real_new_sentence = bool(
                    _TERMINAL_PUNCTUATION_RE.search(previous)
                )
            if starts_a_real_new_sentence:
                return [QualityIssue(
                    category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                    problem='A speaker block begins in lower case.',
                    correction='Capitalize the first word of each block.',
                    target_span=block.strip()[:60],
                    rule='transcript.block-not-capitalized',
                    detector='deterministic',
                )]
        for match in _SENTENCE_START_RE.finditer(block):
            preceding = block[max(0, match.start() - 12):match.start()].lower()
            word = re.split(r'[\s(]', preceding)[-1] if preceding else ''
            if word.rstrip('.') in _ABBREVIATIONS:
                continue
            return [QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
                problem=f'A sentence begins in lower case after "{match.group(1)}".',
                correction='Capitalize the first word of each sentence.',
                target_span=block[max(0, match.start() - 20):match.end() + 20].strip()[:60],
                rule='transcript.sentence-not-capitalized',
                detector='deterministic',
            )]
    return []


def check_paragraphing(text):
    """
    Long single-speaker turns are broken into paragraphs. EDITED ONLY.

    Reported once per document: a transcript that never paragraphs anything
    has one systematic problem, not fifteen.
    """
    for block in _prose_blocks(text):
        if '\n\n' in block.strip():
            continue
        words = len(block.split())
        if words > MAX_UNBROKEN_BLOCK_WORDS:
            return [QualityIssue(
                category=IssueCategory.STRUCTURE, severity=Severity.MEDIUM,
                problem=(
                    f'A single speaker block runs to {words} words with no '
                    f'paragraph break.'
                ),
                correction=(
                    'Break long monologues into logical paragraphs for '
                    'readability.'
                ),
                target_span=block.strip()[:60],
                rule='transcript.monologue-not-paragraphed',
                detector='deterministic',
            )]
    return []


#: A closed, evidenced list of Writing Rules spellings Eagle's General
#: Guidelines name explicitly as required -- each pair copied from the
#: guideline's own worked example, not invented. Mirrors
#: deterministic.py's check_autocorrect (built for the equivalent French
#: problem): no judgement involved, each form is simply not the required
#: spelling in this position. BOTH STANDARDS: this is Writing Rules, not
#: the Clean-Read-vs-Verbatim rubric -- it holds regardless of which
#: standard the task is under.
_REQUIRED_SPELLINGS = (
    (re.compile(r'\be-mail\b', re.IGNORECASE), 'email',
     '"email" is the required spelling, not "e-mail"'),
    (re.compile(r'\balright\b', re.IGNORECASE), 'all right',
     '"all right" is the required spelling, not "alright"'),
    (re.compile(r'\bO\.K\.?\b'), 'okay',
     '"okay" is the required spelling, not "O.K."'),
    (re.compile(r'\byep\b|\byup\b', re.IGNORECASE), 'yeah',
     '"yeah" is the required spelling, not "yep"/"yup"'),
    (re.compile(r"\bya['’]ll\b", re.IGNORECASE), "y'all",
     '"y\'all" is the required spelling, not "ya\'ll"'),
    (re.compile(r'\bT\.V\.(?!\w)'), 'TV',
     '"TV" is the required spelling, not "T.V."'),
    (re.compile(r'\bU\.S\.(?!\w)'), 'US',
     '"US" is the required spelling, not "U.S."'),
    (re.compile(r'\bU\.K\.(?!\w)'), 'UK',
     '"UK" is the required spelling, not "U.K."'),
)


def check_required_spellings(text):
    """
    Eagle's own named Writing Rules spelling table, checked directly.

    ONE FINDING PER DOCUMENT, matching this module's existing convention
    for a systemic-not-local defect (see check_capitalization,
    check_paragraphing): a document that writes "e-mail" throughout has
    one habit to fix, not a finding per occurrence.
    """
    body = '\n'.join(_prose_blocks(text))
    for pattern, correct, explanation in _REQUIRED_SPELLINGS:
        match = pattern.search(body)
        if not match:
            continue
        return [QualityIssue(
            category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
            problem=f'{explanation}.',
            correction=f'Use "{correct}" throughout.',
            target_span=match.group(0),
            rule='transcript.required-spelling',
            detector='deterministic',
        )]
    return []


def check_quote_style(text):
    """
    Standard double quotation marks. BOTH standards require this.

    Curly quotes are the common failure: they arrive from a word processor
    or from a model that prefers typographic quotes, and they survive
    invisibly because they look correct to a human reader.
    """
    issues = []
    body = '\n'.join(_prose_blocks(text))
    curly = _CURLY_QUOTE_RE.search(body)
    if curly:
        issues.append(QualityIssue(
            category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
            problem='Curly quotation marks are used instead of standard double quotes.',
            correction='Use straight double quotes ("...") throughout.',
            target_span=body[max(0, curly.start() - 25):curly.end() + 25].strip()[:60],
            rule='transcript.non-standard-quotes',
            detector='deterministic',
        ))
    single = _SINGLE_QUOTED_RE.search(body)
    if single:
        issues.append(QualityIssue(
            category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
            problem='Single quotes appear to be used for quoted speech.',
            correction='Quoted speech takes standard double quotes.',
            target_span=single.group(0)[:60],
            rule='transcript.single-quotes-for-speech',
            detector='deterministic',
        ))
    return issues


def check_speaker_numbering(text):
    """
    Numbering per gender starts at 1 and does not skip. BOTH standards.

    A gap means either a speaker was dropped from the transcript or the
    numbering restarted partway through. Consistent numbering throughout is
    explicitly required, because a reader tracks who is who by that number
    and nothing else -- there are no names to fall back on.
    """
    seen = {}
    for gender, number in _SPEAKER_NUMBER_RE.findall(text or ''):
        seen.setdefault(gender, set()).add(int(number))

    issues = []
    for gender, numbers in sorted(seen.items()):
        expected = set(range(1, max(numbers) + 1))
        missing = sorted(expected - numbers)
        if missing:
            issues.append(QualityIssue(
                category=IssueCategory.STRUCTURE, severity=Severity.MEDIUM,
                problem=(
                    f'{gender} speaker numbering skips '
                    + ', '.join(f'{gender}_{n}' for n in missing[:5])
                    + f' but uses {gender}_{max(numbers)}.'
                ),
                correction=(
                    f'Number {gender} speakers consecutively from 1 with no gaps.'
                ),
                rule='transcript.speaker-numbering-gap',
                detector='deterministic',
            ))
    return issues


def run_transcript_checks(
    text: str, standard_name: Optional[str] = None,
) -> List[QualityIssue]:
    """
    Every transcript-format check, in one call. Zero provider requests.

    `standard_name` selects the polarity of the rules that invert; the
    shared format rules run either way, because a malformed speaker label
    or a discontinuous timestamp is wrong under both standards.
    """
    issues = []
    issues += check_speaker_labels(text)
    issues += check_speaker_numbering(text)
    issues += check_timestamps(text)
    issues += check_bracket_tags(text)
    issues += check_number_rules(text)
    issues += check_quote_style(text)
    issues += check_required_spellings(text)

    standard = get_standard(standard_name)
    if standard is not None:
        issues += check_standard_policy(text, standard)
        # Edited-verbatim only. Strict verbatim forbids both: adding
        # capitalization to tidy a run-on, and imposing paragraph breaks,
        # are an editor's reading of the speech rather than the speech.
        # `capitalize_sentences`, NOT `correct_grammar` -- see
        # TranscriptionStandard's own docstring for why sentence-start
        # capitalization and fixing grammar/run-ons are different rules.
        if standard.capitalize_sentences:
            issues += check_capitalization(text)
        if standard.paragraph_monologues:
            issues += check_paragraphing(text)
    return issues


def run_transcript_content_checks(
    text: str, standard_name: Optional[str] = None,
) -> List[QualityIssue]:
    """
    The transcript-rubric checks that hold on raw segment text -- no speaker
    labels, no timestamps, no bracket tags rendered yet.

    `run_transcript_checks` is for the fully rendered submission document
    (eagle_submit.py). The quality ENGINE only ever has the joined segment
    text of an in-progress pass, so the label/timestamp/paragraph structure
    checks would find nothing there and are left out -- the standard pass
    edits wording, it does not touch labels or timing, so there is nothing
    of theirs for it to break. What it CAN get wrong is the polarity rubric
    (fillers, contractions, slang) and number/quote style, and those are
    exactly what runs here.
    """
    issues = list(check_number_rules(text))
    issues += check_quote_style(text)
    issues += check_required_spellings(text)
    standard = get_standard(standard_name)
    if standard is not None:
        issues += check_standard_policy(text, standard)
        if standard.capitalize_sentences:
            issues += check_capitalization(text)
    return issues


def describe_standards() -> List[Dict]:
    """What the capabilities endpoint advertises, so the UI renders the
    server's list rather than a copy that can drift."""
    return [
        {
            'value': standard.name,
            'label': standard.label,
            'description': standard.description,
        }
        for standard in STANDARDS.values()
    ]
