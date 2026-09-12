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

WRITTEN FOR SOMEBODY WHO DOES NOT READ THE TARGET LANGUAGE. That is the
normal case on a translation task, so every instruction has to be one the
assistant can carry out and the gate can then verify, rather than one the
worker is expected to check by eye.
"""

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
  reply must be the corrected {target} text and nothing else.
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
- Do not add explanatory notes, translator's notes, or footnotes.
- Do not introduce square brackets. If the source already contains a
  bracketed tag such as [inaudible] or [crosstalk], keep it, translated:
  [inaudible] becomes [inaudible] in French usage, [incompréhensible] where
  that is what the document uses. Never invent one, and never delete one
  the source gave you.
- Do not add any website, domain name, URL, email address, book title,
  citation or reference that is not already in the text. If the speaker
  describes something without naming it, leave it undescribed. Supplying
  the name you believe is correct is an invention, however plausible.
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
   order, agreement, tense, register, and awkward literal renderings.
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
   of the document; never guess wildly and never flag it in the text.
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
- Capitalise only the first word of a title, plus proper nouns""",
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
submission to a transcription and translation marketplace. The text below
is a machine-produced {target} translation of an {source} audio transcript.
Your job is to bring it to professional publication standard.

{must_not}

{what_to_fix}

{typography}

{structure}

Reply with the corrected {target} text only.

Here is the text:
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
     because the digit itself is not literally spoken in the source.
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

If you find nothing at all, reply with the single word: CLEAN.

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

    `review` embeds the source, because a comparison needs both documents
    and a worker pasting two blocks in the right order is a step that goes
    wrong. The other two are headers the worker pastes their text under.
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
    return {
        'translate': _TRANSLATE.format(**parts, **sections),
        'polish': _POLISH.format(**parts, **sections),
        'review': _REVIEW.format(source_text=(source_text or '').strip(),
                                 **parts, **sections),
    }
