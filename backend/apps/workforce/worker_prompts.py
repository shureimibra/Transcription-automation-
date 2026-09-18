"""
The prompts a tasker pastes into their own assistant.

SOURCE OF TRUTH: docs/TRANSLATION_POLISH_PROMPT.md. That document explains
why each rule exists and which automatic check enforces it; this module is
the same prompt made available from the screen, so nobody has to find a file
in a repository to do their job. Change one and change the other.

WHY THESE LIVE ON THE SERVER. The workspace told workers to paste the text
"under the polish prompt" and then supplied nothing, so six people brought
six prompts to six assistants. That is the same failure the project->standard
mapping had before the coach's list arrived: when the rule lives in
somebody's head you get one standard per head, and you find out which ones
were wrong when the reviews come back a day later.

EVERY RULE HERE CORRESPONDS TO A REAL DEFECT OR A REAL CHECK. None of it is
stylistic preference:

  * no commentary            -- check_commentary blocks the submission
  * no leaked scaffolding    -- a tasker flagged on 2026-09-15 that every
                                polish reply ends with a footer that reads
                                as ordinary text -- a real leak risk if the
                                whole reply gets copied. First fix wrapped
                                the document in START/END markers so there
                                was something unambiguous to copy; that
                                held only until a worker's own assistant
                                UI (Gemini's canvas view) turned out to
                                offer ONE copy button for its whole reply,
                                markers included. The real fix is
                                worker_prompts.strip_reply_scaffolding --
                                the save path now accepts the whole reply
                                and pulls the document out itself, so
                                pasting everything is the expected path,
                                not a mistake to avoid. check_commentary
                                stays on as a backstop for whatever that
                                function does not recognise.
  * nothing dropped or added -- check_length_anomaly
  * no source text left      -- check_source_leakage, and a HIGH finding
                                seen on a live task
  * numbers unchanged        -- check_numbers, the only CRITICAL, no override
  * no invented references   -- written after a run supplied planethunters.org,
                                a site the speaker never named
  * target language only     -- written after a run wrote Spanish "decir"
  * no formatting characters -- written after a run wrapped titles in
                                asterisks, which Eagle would print literally
  * number FORM preserved    -- written after task 4813 turned "16 and 2
                                thirds percent" into "16,67 pour cent":
                                right arithmetic, invented precision, two
                                blocking findings
  * one address form        -- written from a FAILED review: a sudden
                                tutoiement partway through a document
  * idioms, not words        -- the same review: "no contest" rendered
                                "indiscutable", where it meant unanimity
  * one form per official name -- written from a FAILED review: the same
                                Act appeared as "la Family Law Reform Act"
                                and "la Family Law Act" in one transcript,
                                and the reviewer marked it down for it
  * no spellchecker damage   -- the same failed review opened with
                                "don't" for "dont", called an
                                English/French typo in the reviewer's own
                                words
  * no invented word forms   -- written from a FAILED review: a real
                                submission used "modérément monastisée" --
                                not a real French word, however derivable
                                it looks from "monastère"
  * real wording for honours -- written from the same failed review: a
                                ceremonial phrase ("named to an order")
                                has fixed, established {target} wording,
                                the same way a statute name does, and a
                                close paraphrase reads as an error even
                                when the meaning survives

WRITTEN FOR SOMEBODY WHO DOES NOT READ THE TARGET LANGUAGE. That is the
normal case on a translation task, so every instruction has to be one the
assistant can carry out and the gate can then verify, rather than one the
worker is expected to check by eye.
"""
import re

#: Language names, so no prompt ever says "translate into fr".
_NAMES = {
    'en': 'English', 'fr': 'French', 'es': 'Spanish', 'de': 'German',
    'pt': 'Portuguese', 'it': 'Italian', 'nl': 'Dutch', 'ar': 'Arabic',
    'zh': 'Chinese', 'ja': 'Japanese', 'ru': 'Russian', 'sw': 'Swahili',
    'tr': 'Turkish', 'pl': 'Polish', 'sv': 'Swedish', 'ro': 'Romanian',
}

#: The things that cause a rejection or block the gate outright.
_MUST_NOT = """\
WHAT YOU MUST NOT DO — these cause automatic rejection:
- Do not add any commentary, preamble, heading, or closing remark. Do not
  write "Here is the corrected version" or anything similar. Your entire
  reply must be the corrected {target} text and nothing else, UNLESS a
  later instruction in this same prompt explicitly describes a
  different reply shape -- in that case, and only that case, follow
  exactly the shape it describes and nothing more.
- Do not summarise, shorten, expand, or reorganise. Every idea in the input
  must appear in your output, in the same order.
- Do not leave any {source} word or phrase untranslated, unless it is a
  proper noun, a brand, an acronym, or a title that is conventionally kept
  in its original form.
- Do not invent, remove, or alter any number, date, percentage, currency
  amount, measurement, proper noun, or acronym. Every one of these in the
  input must appear in your output with the same value.
- KEEP EVERY NUMBER IN THE SAME FORM AND THE SAME PRECISION. Never round,
  never compute, and never turn a fraction or a spelled-out number into a
  decimal. "16 and 2 thirds percent" becomes "16 et deux tiers pour cent",
  NEVER "16,67" — the arithmetic is right and the translation is wrong,
  because it states a precision the speaker did not. A decade, a year or a
  citation is copied across unchanged.
- WHEN ONE QUANTITY PRECEDES SEVERAL CATEGORIES, PRESERVE THE SOURCE'S OWN
  READING, NOT WHICHEVER IS EASIER TO WRITE. "$100 in interest, dividend,
  and capital gains income" is ambiguous only if you read it quickly --
  check whether the source states or implies one amount split across all
  three, or that same amount applying to each one separately, and
  translate whichever reading the source actually supports. Never turn a
  shared total into a per-category amount, or the reverse.
- Do not add explanatory notes, translator's notes, or footnotes.
- Do not introduce square brackets. If the source already contains a
  bracketed tag such as [inaudible], [crosstalk], [foreign] or
  [background], keep it, translated: [inaudible] becomes [inaudible] in
  French usage, [incompréhensible] where that is what the document uses.
  Never invent one, and never delete one the source gave you.
- Do not add any website, domain name, URL, email address, book title,
  citation or reference that is not already in the text. If the speaker
  describes something WITHOUT EVER NAMING IT AT ALL, leave it
  undescribed -- supplying a name for something never named is an
  invention, however plausible. This is a different case from a name
  the speaker DID say but the transcript garbled: correctly recovering
  it through research is a correction, not an invention, at whatever
  confidence bar this prompt asks for that elsewhere.
- Write only in {target}. Not one word of any other language may appear in
  your output, except inside a quotation, a title or a proper name that the
  text itself keeps in its original language.
- Translate EVERY term, including ones a {target} reader would probably
  understand. If you have already translated a term once, never leave it
  untranslated later in the same document.
- OFFICIAL NAMES GET ONE FORM AND KEEP IT. The name of a statute, a court,
  an institution, a case or an organisation may be kept in {source} — but
  whichever form you choose, use that exact form EVERY time it appears.
  Never translate it in one sentence and leave it in {source} in the next,
  and never shorten it on later mentions. A reviewer rejected a real
  submission for exactly this: the same Act appeared under two different
  names in one transcript.
- A GENERIC DESCRIPTIVE PHRASE IS NOT AN OFFICIAL NAME, even when
  capitalised in the source. "Federal corporations" describing a category
  of companies, or a bare backreference such as "the Act" or "the
  Corporations" used as shorthand after the real name was already given in
  full, must be translated normally — only the actual full name of one
  specific statute, court, program or organisation may stay in {source}.
  And do not assume a recurring term is fine just because it never varies:
  a real submission left the English word "core" untranslated a dozen
  times in one document, consistently — consistency is not the same as
  correctness.
- HONOURS, AWARDS AND CEREMONIAL TITLES HAVE REAL, FIXED {target} WORDING —
  research and use it, the same way you would for a statute or a court.
  Being appointed to an order, elected to a society, or awarded a prize
  each has an established phrase in {target} usage; a close paraphrase of
  your own construction reads as an error even when the meaning survives.
  A reviewer rejected a real submission partly for exactly this.
- NEVER COIN A {target} WORD THAT DOES NOT ALREADY EXIST, even when it
  looks like a plausible derivation from a real one. If you are not
  certain a word is standard, established {target} vocabulary, use a real
  synonym or a short descriptive phrase instead. A reviewer rejected a
  real submission for "modérément monastisée" — not a real French word,
  however derivable it looks from "monastère".
- SHORT WORDS ARE NOT EXEMPT. A pronoun, a contraction, or a quantifier
  ("I'm", "I'll", "it's", "all", "each", "anyone", "one", "they") must be
  translated every time, however common it is or however it is
  capitalised. A real run left "I'm" untranslated in an otherwise-
  correctly-translated sentence ("I'm suis en Indiana maintenant" —
  literally "I'm am in Indiana now"). None of these are proper names.
- Use no formatting characters at all. No asterisks, no underscores, no
  markdown. Titles are written in plain text. What you produce is submitted
  as plain text and an asterisk arrives as literal punctuation in the middle
  of a sentence."""

_WHAT_TO_FIX = """\
WHAT TO FIX:
1. Fluency. Rewrite anything that reads as machine-translated so that it
   reads as though written by a native {target} professional. Fix word
   order, agreement, tense, register, and awkward literal renderings. You
   may split one long {source} sentence into two {target} ones, or merge
   two into one, when that is what natural {target} rhythm calls for --
   {target} does not have to inherit the transcript's sentence boundaries,
   only its content and order. Vary your connectors, too: a translation
   that reaches for the same one every time it needs to connect an idea
   reads as translated even when every word in it is correct.
1b. FALSE COGNATES ARE STILL A TRANSLATION MISTAKE, EVEN THOUGH THE WORD
   EXISTS IN {target}. A word that looks like its {source} cousin is not
   always its translation. In French, for example: "opportunité" for a
   plain "opportunity" reads as an anglicism where "occasion" or
   "possibilité" is the ordinary word; "impacter" is common in speech but
   "toucher" or "avoir une incidence sur" is more standard in writing.
   These are correct {target} words used in the wrong sense -- nothing a
   spellchecker or a dictionary lookup on the word alone would catch.
1c. DO NOT LEAVE A MODIFIER SITTING WHERE IT COULD ATTACH TO TWO DIFFERENT
   THINGS. If an adjective or descriptive phrase placed next to a noun
   could plausibly describe either that noun alone or the larger phrase
   around it, and the source does not settle which, restructure the
   {target} sentence so only one reading survives -- even if that makes it
   a few words longer. A shorter, ambiguous rendering is not more faithful
   to the source than a longer, unambiguous one; it only moves the
   guesswork onto the reader, which in practice means onto the reviewer.
1d. WHEN TWO {target} WORDS ARE BOTH CORRECT, THE MORE COMMON ONE IS THE
   SAFER DEFAULT. {target} sometimes offers a more literary or elevated
   word alongside a plainer, more standard one for the same idea, and both
   can be genuinely correct. Where the source gives no specific reason to
   reach for the elevated option, prefer the plainer, more common word --
   save literary vocabulary for places where the source's own register
   clearly calls for it.
2. Terminology consistency. Once you choose a {target} term for a recurring
   concept, use that same term everywhere. Never render one source term
   three different ways.
3. Register, and ONE FORM OF ADDRESS THROUGHOUT. Match the source: formal
   stays formal, conversational stays natural rather than stiff. Where
   {target} distinguishes familiar from polite address (French tu/vous,
   Spanish tu/usted, German du/Sie), pick ONE and never switch. A review was
   failed for a sudden tutoiement partway through -- switching reads as two
   different people speaking.
3b. IDIOMS GET THE EQUIVALENT EXPRESSION, NOT THE WORDS. A figurative
   phrase translated literally produces a sentence that parses and means
   nothing. From the same failed review: "no contest" became
   "indiscutable" where the sense was unanimity, and "better luck next
   time" became "plus de chance la prochaine fois" instead of the ordinary
   "bonne chance". Also watch small determiners: "how many want a
   vegetarian pizza" is "une pizza", not "cette pizza".
4. Domain accuracy. Identify what the document is about and use the correct
   professional vocabulary for that field — legal, medical, scientific,
   academic, technical, financial, or general. Where a field has a settled
   {target} term of art, use it rather than a literal translation. If a term
   is genuinely ambiguous, choose the reading most consistent with the rest
   of the document; never guess wildly, and never flag it with a note
   inside the text itself. (A proper name, a citation, a quantity's scope,
   or a garbled source clause -- this prompt gives you a separate,
   structured way to flag each of those elsewhere -- is not covered by
   this rule; use that mechanism for those, not an inline note either.)
5. Cohesion. The source is a spoken transcript, so it may contain false
   starts, repetitions, and filler that survived translation. Remove them
   and join the sentences so the result reads as clean written {target}.
   Preserve meaning; remove only the disfluency.
6. Technical notation. If the document is mathematics, chemistry, physics
   or a similar field, it was spoken, so the source says "x squared", not
   "x²". A simple one-off quantity may stay spelled out ("x au carré"),
   but anything structurally complex, general or repeated should use
   standard Unicode notation instead of spelling it out every time:
   "a-naught plus a-one z plus a-two z-squared, and so on" becomes
   "a₀ + a₁z + a₂z² + ...", not a wall of {target} words. Use superscripts,
   subscripts and symbols like "x²" and "H₂O"; never LaTeX or markup."""

#: French typography is spelled out because every line of it is a check the
#: gate runs. Other languages get the general instruction: inventing a rule
#: list for a language nothing has been measured against would be guessing,
#: and a wrong rule is worse than none.
_TYPOGRAPHY = {
    'French': """\
FRENCH TYPOGRAPHY — apply all of these:
- Narrow non-breaking space before ; : ! and ?
- Guillemets with inner spacing for quotations: « comme ceci »
  Never straight ASCII quotes.
- Decimal comma, not a point: 3,14 — never 3.14
- Space before the percent sign: 50 % — never 50%
- Space as thousands separator: 2 582 — never 2,582 and never 2582
- Currency: the number first, space-grouped, symbol last — 1 000 $, never
  $1,000. A real submission got this right in one paragraph and then left
  eight figures in a row in the English format later in the same document.
- Apostrophes as ’ (typographic), not '
- Capitalise only the first word of a title, plus proper nouns
- Centuries: a Roman numeral with a superscript ordinal -- XIXᵉ siècle,
  XXᵉ siècle -- never a plain digit ("19e siècle") and never spelled
  out ("dix-neuvième siècle")""",
}

_TYPOGRAPHY_DEFAULT = """\
{target} TYPOGRAPHY:
- Use the quotation marks, decimal separator, thousands separator and
  spacing conventions of written {target}, not those of the source.
- Use typographic apostrophes and quotation marks, never ASCII substitutes.
- Capitalise titles the way {target} does, not the way the source does."""

_STRUCTURE = """\
STRUCTURE:
- Keep the paragraph breaks of the input. One blank line between
  paragraphs. Do not merge everything into one block and do not add
  headings that are not in the input."""


_POLISH = """\
You are a professional {target} translator preparing a document for
submission to a transcription and translation marketplace. Below is the
{source} source, then a machine-produced {target} translation of it. Your
job is to bring the {target} translation to professional publication
standard AND make it actually match what the {source} source says.

READ THE WHOLE OF BOTH DOCUMENTS BEFORE REWRITING. A dropped sentence, a
wrong number, or a term that reads perfectly well in {target} but means
the wrong thing are only visible next to the source -- none of them look
wrong if you only ever read the {target} text on its own.

{must_not}

{what_to_fix}

RESEARCH EVERY PROPER NAME, CASE CITATION, STATUTE AND INSTITUTION -- BUT
ONLY GO AS FAR AS RESEARCH CAN ACTUALLY SETTLE. Where a garbled name
points to exactly one real, well-known match, use the correct form --
that is a correction, not a guess. But where research only gets you to
a PLAUSIBLE match rather than a certain one, or where using the correct
form means adding specific detail the garbled source gives no trace of
at all (a middle initial, "Jr.", an exact ordinal circuit number,
"published" versus "forthcoming"), that is a question only the audio
can answer, and you were never given the audio. In that case:
- Use the name or term WITHOUT the uncertain extra detail in the
  translation itself -- the correct core identification, not the
  specific detail you cannot confirm.
- After the full translation, add one line reading exactly
  VERIFY AGAINST AUDIO: followed by each such spot and why you were
  unsure, one per line. Write NONE if there is nothing to flag.
This is not permission to stop researching or correcting -- keep doing
both, as instructed above. It is a limit on confidence: a plausible
research match is not the same as a confirmed one, and only a human who
can actually listen closes that gap.

THE SAME LIMIT APPLIES TO WHAT A QUANTITY COVERS. If a single figure
precedes a list of categories and the source itself does not settle
whether it is one amount split across them or that amount applying to
each one separately, that is also a question only the audio can answer.
Translate the number and the categories without resolving the split, and
name the spot on the VERIFY AGAINST AUDIO line the same way.

THE SAME LIMIT APPLIES TO ANY SPOT WHERE THE SOURCE ITSELF IS GARBLED OR
INCOMPLETE, NOT ONLY A NAME OR A QUANTITY. A speech-recognition transcript
sometimes hands you a clause that does not parse, or parses two different
ways, through no fault of the translation -- "our tankers' fades", "it's
easier, more so pity". Translate the most plausible reading you can
construct, the same way you would resolve any other genuine ambiguity --
but do not let a confident-sounding {target} sentence hide that the
underlying {source} was broken. Name the spot on the VERIFY AGAINST AUDIO
line the same way, with a short note on what made it unclear.

THIS IS ABOUT TRANSCRIPTION NOISE, NOT ABOUT WHAT THE SPEAKER ACTUALLY
MEANT. A clause that does not parse is a different problem from a clause
that parses fine but sounds inconsistent, uncomfortable, or self-
contradictory -- a speaker genuinely saying two things that sit in
tension is real content, not an artifact, and smoothing it into
something that reads more logically is an invention the audio can't
excuse. Translate what the source actually says, contradiction and all;
only route a spot to VERIFY AGAINST AUDIO when the WORDS THEMSELVES do
not parse, never merely because the MEANING is surprising.

{typography}

{structure}

Reply in exactly this shape and nothing else -- no source text repeated,
no other commentary:

=== TRANSLATION START ===
(the corrected {target} text goes here -- nothing else on these lines)
=== TRANSLATION END ===
VERIFY AGAINST AUDIO: (the line described above, or NONE)

You do not need to select just part of this reply -- paste your whole
answer, markers and all, into the Polished {target} field. The platform
pulls the document out on its own and will show you the VERIFY AGAINST
AUDIO line separately after you save, if there is one.

--- SOURCE ({source}) ---
{source_text}

--- TRANSLATION ({target}) ---
"""

_TRANSLATE = """\
You are a professional {target} translator preparing a document for
submission to a transcription and translation marketplace. The text below
is the {source} transcript of a recording. Translate it into {target} to
professional publication standard.

This is a spoken transcript, so it contains false starts, repetitions and
filler. Remove those and join the sentences so the {target} reads as clean
written prose. Preserve every idea; remove only the disfluency.

{must_not}

{what_to_fix}

{typography}

{structure}

Reply with the {target} text only.

Here is the text:
"""

_REVIEW = """\
You are checking a {target} translation of a spoken {source} transcript.
DO NOT REWRITE IT. Report only.

Below is the {source} source, then the {target} translation. Compare them
against the standard below and tell me every place the translation breaks
it. Read the whole of both documents before answering — a dropped paragraph
and an inconsistent term are only visible across the whole text.

{must_not}

{what_to_fix}

{typography}

{structure}

For each problem, give me exactly this and nothing else:

  * the {source} phrase
  * the {target} phrase
  * what is wrong with it, in one line
  * the corrected {target} phrase

Order them worst first. Be strict about these three, which are the ones
that cost a submission:

  1. Numbers. Check that every number in the source appears in the
     translation, in the same form and the same precision, and that the
     translation contains no number the source does not state. EXCEPTION:
     a digit that is part of Unicode notation standing in for spoken
     technical language (rule 6 -- "x squared" rendered as "x²") is not an
     invented number. Flag it only if the underlying quantity changed, not
     because the digit itself is not literally spoken in the source. Also
     check what a quantity COVERS: when one figure precedes a list of
     categories, does the translation preserve whether the source meant a
     shared total or a separate amount per category? Flag it if this is
     unclear, or if the translation resolved it in a way the source does
     not actually support.
  2. Untranslated {source}. Any word or passage left in {source} that is
     not a proper noun, brand, acronym or conventional title — including a
     pronoun, contraction or quantifier ("I'm", "it's", "all", "each",
     "one", "they") sitting inline in an otherwise-correctly-translated
     sentence. These are easy to miss precisely because the rest of the
     sentence around them reads as fine.
  3. Inventions. Any website, citation, title or reference in the
     translation that is not in the source.
  4. Official names used inconsistently, AND generic phrases wrongly kept
     in {source}. List every statute, court, institution, case and
     organisation named in the document, with each form it appears in. If
     any one of them appears in more than one form, say so — that alone
     has failed a review. Separately: for a term that stays in {source}
     EVERY time, check whether it is actually the name of one specific
     thing, or just a generic description or shorthand backreference that
     should have been translated instead — a term wrong the same way every
     time will not surface as an inconsistency, so absence of variation is
     not proof of correctness.
  5. English/{target} typos. Words that are neither {source} nor {target}
     because a spellchecker altered them: in French, "don't" for "dont",
     "Vie siècle" for "VIe siècle". Check every one.
  6. Address form. Does the document switch between familiar and polite
     address anywhere? Name every place it does.
  7. Anything that reads as translated rather than written. Literal
     renderings of idioms, and phrases a native speaker would not say.
  8. A person credited more than once. Scan any list of names — speakers,
     authors, honorees, committee members — for the same person appearing
     twice. A reviewer rejected a real submission for exactly this: the
     same name listed twice in one introduction, with nothing else wrong
     on the page. Also check any invented-looking word (does it actually
     exist in {target}?) and any honour, award or ceremonial title (does
     the translation use the real, established {target} phrase for it,
     rather than a plausible paraphrase?).

If you find nothing at all, reply with exactly this and nothing else:

CLEAN

=== TRANSLATION START ===
(the {target} translation exactly as given above, unchanged)
=== TRANSLATION END ===

You do not need to select just part of this reply -- if you paste
something back, paste the whole answer, the word CLEAN and all. The
platform pulls the document out on its own.

--- SOURCE ({source}) ---
{source_text}

--- TRANSLATION ({target}) ---
"""


def _language(code: str, fallback: str) -> str:
    """A name a person and an assistant both recognise, never a bare code."""
    code = (code or '').strip().lower()
    return _NAMES.get(code[:2], code.upper() if code else fallback)


def build(source_language: str, target_language: str, source_text: str = '') -> dict:
    """
    The three prompts for this task, ready to paste.

    `polish` and `review` both embed the source now, because a defect
    class neither can see without it -- a dropped sentence, a wrong
    number, a term that reads fine in {target} but means something the
    source never said -- is invisible from the {target} text alone,
    whether the assistant is fixing it (polish) or only reporting it
    (review). See docs/TRANSLATION_POLISH_PROMPT.md's "Why the source is
    embedded now" section for the real document this was confirmed
    against: 3 of 38 real defects survived a source-blind polish pass, and
    all 3 trace to exactly this gap. `translate` is the one prompt that
    never had a "target text to compare" problem in the first place, since
    its whole job starts from the source.
    """
    source = _language(source_language, 'English')
    target = _language(target_language, 'the target language')

    parts = {
        'source': source,
        'target': target,
    }
    sections = {
        'must_not': _MUST_NOT.format(**parts),
        'what_to_fix': _WHAT_TO_FIX.format(**parts),
        'typography': _TYPOGRAPHY.get(
            target, _TYPOGRAPHY_DEFAULT.format(**parts)),
        'structure': _STRUCTURE,
    }
    cleaned_source_text = (source_text or '').strip()
    return {
        'translate': _TRANSLATE.format(**parts, **sections),
        'polish': _POLISH.format(
            source_text=cleaned_source_text, **parts, **sections),
        'review': _REVIEW.format(
            source_text=cleaned_source_text, **parts, **sections),
    }


# --------------------------------------------------------------------- #
# Recovering the document from a worker's assistant's raw reply
# --------------------------------------------------------------------- #
#
# WHY THIS EXISTS, HAVING JUST BUILT THE OPPOSITE ANSWER. _POLISH and
# _REVIEW ask for the document wrapped in START/END markers specifically
# so there is an unambiguous boundary for a worker to copy -- and that
# held for about as long as it took a worker to actually use it. Gemini
# (and assistants like it) render a long reply in their own "canvas" or
# artifact view with ONE copy button for the whole block, because they
# have no way to know two of the lines in it are instructions rather than
# content. The natural action -- click the one copy icon on screen -- copies
# the markers right along with the document, which is exactly the leak
# this was supposed to prevent, just moved one step earlier.
#
# THE FIX IS TO STOP RELYING ON A WORKER SELECTING TEXT BY HAND AT ALL.
# The save path now accepts the WHOLE reply, markers and all, and pulls
# the document out of it the same way a person would read it -- so
# pasting the entire clipboard is not just tolerated, it is the expected
# path. check_commentary (deterministic.py) stays as a backstop for
# whatever this does not recognise; this is the first line of defence,
# not a replacement for it.
_TRANSLATION_START_RE = re.compile(
    r'^[ \t]*=+[ \t]*TRANSLATION[ \t]+START[ \t]*=+[ \t]*$',
    re.IGNORECASE | re.MULTILINE)
_TRANSLATION_END_RE = re.compile(
    r'^[ \t]*=+[ \t]*TRANSLATION[ \t]+END[ \t]*=+[ \t]*$',
    re.IGNORECASE | re.MULTILINE)
#: A leading CLEAN verdict, from a _REVIEW reply -- only at the very
#: start, so a document that happens to open with the word "Clean" is
#: never touched.
_CLEAN_LEADING_RE = re.compile(r'\A[ \t]*CLEAN[ \t]*\r?\n+')
#: Everything from "VERIFY AGAINST AUDIO:" to the end of whatever text it
#: is found in -- the footer is always last, never followed by more
#: document, so there is nothing after it worth keeping.
_VERIFY_AUDIO_RE = re.compile(
    r'VERIFY AGAINST AUDIO:[ \t]*(.*)', re.IGNORECASE | re.DOTALL)


def strip_reply_scaffolding(text: str) -> tuple:
    """
    Recover (document_text, verify_note) from a worker's assistant's raw
    reply, tolerating the ENTIRE reply having been pasted -- markers,
    CLEAN verdict, VERIFY AGAINST AUDIO footer and all -- rather than
    requiring a worker to hand-select only the interior text first.

    `verify_note` is '' when there is nothing to check: no VERIFY AGAINST
    AUDIO line was found, or it explicitly said NONE. When it is not
    empty, the caller must still surface it -- stripping it from the
    saved document and then saying nothing about it would silently
    discard the one signal this whole mechanism exists to preserve. See
    _POLISH's own "RESEARCH EVERY PROPER NAME" section for what put it
    there in the first place.
    """
    text = (text or '').strip()
    text = _CLEAN_LEADING_RE.sub('', text, count=1).strip()

    start_m = _TRANSLATION_START_RE.search(text)
    end_m = _TRANSLATION_END_RE.search(text, start_m.end()) if start_m else None
    if start_m and end_m:
        document = text[start_m.end():end_m.start()]
        tail = text[end_m.end():]
    else:
        document, tail = text, ''

    verify_note = ''
    verify_m = _VERIFY_AUDIO_RE.search(tail) or _VERIFY_AUDIO_RE.search(document)
    if verify_m:
        verify_note = verify_m.group(1).strip()
        document = _VERIFY_AUDIO_RE.sub('', document).strip()
    if verify_note.upper() == 'NONE':
        verify_note = ''

    # DEFENSIVE, NOT THE NORMAL PATH: a stray unmatched marker (only one
    # of the pair present -- a truncated paste, or an assistant that
    # dropped one) would otherwise survive into the saved document
    # verbatim. Idempotent no-op when both were already consumed above.
    document = _TRANSLATION_START_RE.sub('', document)
    document = _TRANSLATION_END_RE.sub('', document)

    return document.strip(), verify_note


# --------------------------------------------------------------------- #
# The transcript accuracy check -- report only, on purpose
# --------------------------------------------------------------------- #
#
# WHY THIS IS REPORT-ONLY, WITH NO REWRITE VARIANT AT ALL, UNLIKE THE
# TRANSLATION PROMPTS ABOVE. A translation worker's assistant only ever
# has to judge language -- is this correct {target}? -- and correcting
# that needs no access to anything outside the text. A transcript's
# accuracy is a claim about what a real person actually said, and an
# assistant reading only the page has never heard the audio. It can spot
# that "ciphate surface" does not look like a real term and that "Seifert
# surface" fits the sentence around it, but it cannot know that is what
# was actually said -- only the worker, listening, can confirm that. A
# rewrite button here would let a worker paste in a plausible-sounding
# guess without ever pressing play, which is a worse failure than the one
# this exists to catch: a confident, uncaught wrong word becoming a
# confident, uncaught DIFFERENT wrong word. So this hands back leads to
# go verify, never a document to paste over the original.
#
# EVERY ITEM BELOW TRACES TO A REAL, CONFIRMED DEFECT. A 19-document
# review of real transcription submissions -- see
# docs/TRANSCRIPTION_QUALITY_FINDINGS_2026-09-12.md -- found that the
# mechanical rubric (numbers, contractions, fillers) is caught well by
# the platform's own checks, but proper nouns and technical terms are
# not checked by anything at all, and were wrong constantly: real
# scholars' names (Milnor -> "Milner", Dirac -> "Durac", de Broglie ->
# "debris"), real terms (Sierpinski -> "Pinsky" AND "Spinsky", two
# different wrong spellings in the same document), and even the literal
# subject of one talk (bore -> "ball" for the first half of the
# document, correct for the rest). None of these are hypothetical.
_TRANSCRIPT_CHECK = """\
You are checking an English transcript of a recorded lecture or talk for
likely mishearings by the speech-recognition software that produced it.
Report only -- do not rewrite anything, and do not produce a corrected
version of the document.

This is a machine transcript of real spoken audio. Every word on the page
is a guess at what was said, made by software with no idea what field this
is or who these people are. Your job is to use your own general knowledge
of the subject to spot places where that guess is probably wrong -- a real
person's name, a technical term, a book, a place or an institution --
misheard as a different real word or as a plausible-sounding non-word.

WHAT TO LOOK FOR, IN ORDER OF VALUE:
1. A capitalized word or short phrase that is not a real name, place or
   term you recognize, especially where a name plainly belongs (introducing
   a person, citing a result, naming a place or an institution). If context
   nearby points to what the real name or term almost certainly is, say so.
2. The SAME name or term spelled two different wrong ways within this one
   document. That is a strong, self-contained sign of a mishearing even
   when you are not sure what the correct spelling is.
3. A sentence that does not quite parse, or a single word that does not
   fit its sentence, where a different, similar-sounding real word would
   read naturally in its place.
4. Numbers: zero through nine should be spelled out as words, ten and
   above as figures -- except money, a time, a date, a percentage, a
   measurement, or an age, which are always figures regardless of size.
   A measurement includes a plain duration ("2 years ago", "5
   kilometers" are both figures, the same as "2 days" or "5 km"), and a
   legal or document citation ("Rule 8", "Section 4", "No. 1") is always
   a figure too -- it names a specific numbered thing, not a spoken
   count. A scale word riding on an already-correct figure ("50
   million", "3 billion") is not itself a defect -- only the count in
   front of it is, if that count is ALSO spelled out ("fifty million":
   flag "fifty", leave "million" alone). Do NOT flag a digit that is
   part of an identifier or notation rather than a spoken count -- a
   variable like "K1", a musical note like "E4", a dimension like "3D",
   an exponent read aloud like "ten to the minus six" -- none of those
   are numbers being spoken, so this rule does not apply to them. A
   PLAIN COUNT stays spelled out even when it is ordered or grouped --
   "the first three" pictures is still "three" as a word, because
   nothing there is naming or labelling a specific numbered thing. But a
   LABEL WORD immediately followed by its number is always a figure,
   whatever the number is: "step 1", "line 3", "page 23", "paragraph
   14", "size 36", "verse 2", "Chapter 4", "Quiz 2", "number 1" naming
   which face came up on a die -- these identify one specific numbered
   item by that number, the same as "Rule 8" or "Section 4" does, so
   "step one" and "number six" are defects here, not correct spelled-out
   counts.
4b. Special characters Eagle's house style never uses, regardless of how
   the source name is normally spelled: an accent or diacritic ("fiance"
   never "fiance" with an acute accent, "El Nino" never with a tilde),
   an ellipsis, or an exclamation mark (except as part of a brand name
   that genuinely has one, like "Yahoo!"). None of these is a
   mishearing to flag when the transcript already omits them -- that is
   the correct spelling under this house style, not a defect.
5. Informal words ("gonna", "kinda", "gotta") that should be replaced
   with their formal equivalent for a professional transcript --
   {standard_note} Do NOT flag "yeah" -- Eagle's own Writing Rules table
   names it as the required correct spelling (not "yep", not "yup"), not
   a defect to fix. Do NOT flag a contraction ("it's", "don't", "I'm")
   at all, under either standard -- Eagle's General Guidelines are
   explicit that contractions are kept exactly as spoken, always: "If a
   speaker says 'cannot,' do not change it to 'can't.'" A word ending in
   's is not automatically a contraction in the first place: "Archie
   Cox's office" is a possessive (an office belonging to Cox), not
   "Archie Cox is office". Likewise "cause" after "to", "will", "can",
   "would" or another modal is the ordinary verb (to cause something),
   not the informal "'cause" short for "because".
6. Whether the pattern of speaker labels looks right. If one label (say
   Male_1) only ever produces short transitional lines and a different
   label (Male_2) always immediately continues with the substantive
   content, with no real back-and-forth between them anywhere, say so --
   that shape usually means one continuous speaker was split into two
   labels, not that there are genuinely two people talking.

ALSO, IF ANY PART OF THIS RECORDING IS TECHNICAL OR MATHEMATICAL -- on any
subject, not just the ones you recognise, since a future recording may be
about a subject nobody has written a rule for yet: look for places where
spoken mathematical or scientific language should be converted to Eagle's
written notation, and report those the same way as everything above.

This is not about mishearings -- the words themselves may be exactly
right -- it is about whether a genuinely WRITTEN-OUT equation is rendered
in the symbols this house style requires. A casual number in ordinary
speech ("with two kids plus three dogs, it's chaotic") stays as words; an
equation being dictated does not. The conventions:
  * Variables and coefficients: digits and letters together, never spelled
    out -- "3x", "12y".
  * Superscripts and subscripts: ^ and _ -- "x^4", "y_2".
  * Molecules: subscript digits -- "H_2O", "CO_2".
  * Greek letters: spelled out as a capitalized word, never the actual
    glyph -- "Sigma", "Mu", "Pi", not "Σ", "μ", "π".
  * Trigonometric functions: lowercase words -- "sine", "cosine", "tangent".
  * Operators: the real symbol, not the word -- "+", "-", "*", "/", "=", "()".
    EXCEPT factorial: write it as the word "factorial", never the "!"
    symbol. Eagle's house style bans exclamation marks outright (see
    item 4b above) with no exception for mathematical notation, so "2!"
    is not a valid correction here even though "!" is the standard
    symbol everywhere else -- "2 factorial" is.

Flag a phrase this way only where the surrounding sentence is clearly
dictating a formula, not just using a number in conversation. Put the
word "Notation:" at the very start of the REASON for every finding of
this kind, and nothing else -- not on the corrected value, which must
stay exactly the corrected text and nothing more, since it goes straight
into the transcript if a worker applies it. That is what tells the
checklist this is a formatting fix, not a content guess, so it does not
need to say so again in your own words. A worker can act on it without
listening to the audio first, unlike a name or a term.

WHAT NOT TO DO:
- Do not rewrite the transcript, or any part of it. You were not there and
  cannot hear the audio, so a guess at a name is a lead for the person who
  CAN listen and confirm it -- never a fact to commit to the document.
- Do not flag a name or term just because you personally have not heard of
  it. Only flag it if something about its spelling looks phonetically
  garbled, or if it contradicts itself elsewhere in this same document.
- Do not comment on writing style, word choice, or anything outside the
  seven things above.

For each thing you flag, give me exactly this and nothing else:

  * the phrase as transcribed
  * what you think it should probably be, if you have a real guess --
    say "not sure" rather than inventing one you are not confident in
  * why, in one line

Only list something here if you are proposing an actual change. If a
number, a word or a phrase is already correct, do not include it just to
show your work -- that pads the list without giving the worker anything
to act on.

Order them worst first: the ones you are most confident about first.

If you find nothing at all, reply with the single word: CLEAN.

--- TRANSCRIPT ---
"""

#: The item-5 sentence needs to say the OPPOSITE thing under strict
#: verbatim, where every filler, informal word and repetition is required
#: to survive exactly as spoken -- flagging them there would be asking
#: the worker to break the very standard the task is graded on. NOTE:
#: this now governs INFORMAL WORDS ONLY (gonna/kinda-style slang) --
#: contractions are excluded above, under both standards, per Eagle's
#: General Guidelines.
_STANDARD_NOTES = {
    'edited_verbatim': (
        'flag every informal word you see. This document follows the '
        'edited verbatim (professional / academic) standard.'
    ),
    'strict_verbatim': (
        'do NOT flag any of these here. This document follows the strict '
        'verbatim standard, which requires every filler, informal word and '
        'repetition to be preserved exactly as spoken -- removing one '
        'would itself be the defect.'
    ),
}
_STANDARD_NOTE_DEFAULT = (
    'flag every informal word you see, unless you are told this document '
    'follows the strict verbatim standard, in which case leave all of '
    'them alone.'
)


def build_transcript_check(standard_name: str = '') -> str:
    """
    The one prompt offered for a transcription task: report-only, and
    aware of which verbatim standard applies so it does not ask a worker
    to "fix" fillers a strict-verbatim transcript is required to keep.

    `standard_name` is the SAME string TaskSerializer.get_transcription_standard
    already resolves (`'edited_verbatim'` or `'strict_verbatim'`) -- passed
    in rather than re-derived, so this never disagrees with what the
    worker's own screen already told them the task's standard is.
    """
    note = _STANDARD_NOTES.get(
        (standard_name or '').strip(), _STANDARD_NOTE_DEFAULT)
    return _TRANSCRIPT_CHECK.format(standard_note=note)
