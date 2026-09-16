# The translation polish prompt

**What this is.** One prompt, copied into your own Gemini (or any capable
assistant), that takes the French our system produced and brings it to
Eagle's submission standard.

**Why it exists.** Our platform translates every task before you take it.
That draft is good but not final. Closing the last gap on our own API would
cost more per month than the whole operation earns, so that step is yours —
and it is the reason a task takes twenty minutes rather than five.

**How to use it:** on the actual screen, press **Copy prompt + translation**
— it copies this prompt with your task's real English source and French
draft already built in, ready to paste as one block. The version below is
the same prompt with placeholders where that real text goes, kept here so
the wording itself is reviewable outside the app. Full step-by-step in
`TASKER_GUIDE.md`.

---

## The prompt

```
====================== COPY FROM HERE ======================
You are a professional French translator preparing a document for
submission to a transcription and translation marketplace. Below is the
English source, then a machine-produced French translation of it. Your
job is to bring the French translation to professional publication
standard AND make it actually match what the English source says.

READ THE WHOLE OF BOTH DOCUMENTS BEFORE REWRITING. A dropped sentence, a
wrong number, or a term that reads perfectly well in French but means the
wrong thing are only visible next to the source — none of them look wrong
if you only ever read the French text on its own.

WHAT YOU MUST NOT DO — these cause automatic rejection:
- Do not add any commentary, preamble, heading, or closing remark.
  Do not write "Here is the corrected version" or anything similar.
  Your entire reply must be the corrected French text and nothing else,
  UNLESS a later instruction in this same prompt explicitly describes a
  different reply shape — in that case, and only that case, follow
  exactly the shape it describes and nothing more.
- Do not summarise, shorten, expand, or reorganise. Every idea in the
  input must appear in your output, in the same order.
- Do not leave any English word or phrase untranslated, unless it is a
  proper noun, a brand, an acronym, or a title that is conventionally kept
  in its original form.
- Do not invent, remove, or alter any number, date, percentage, currency
  amount, measurement, proper noun, or acronym. Every one of these in the
  input must appear in your output with the same value.
- KEEP EVERY NUMBER IN THE SAME FORM AND THE SAME PRECISION. Never round,
  never compute, and never turn a fraction or a spelled-out number into a
  decimal. "16 and 2 thirds percent" becomes "16 et deux tiers pour cent",
  NEVER "16,67" - the arithmetic is right and the translation is wrong,
  because it states a precision the speaker did not. A decade, a year or a
  citation is copied across unchanged.
- WHEN ONE QUANTITY PRECEDES SEVERAL CATEGORIES, PRESERVE THE SOURCE'S OWN
  READING, NOT WHICHEVER IS EASIER TO WRITE. "$100 in interest, dividend,
  and capital gains income" is ambiguous only if you read it quickly -
  check whether the source states or implies one amount split across all
  three, or that same amount applying to each one separately, and
  translate whichever reading the source actually supports. Never turn a
  shared total into a per-category amount, or the reverse.
- Do not add explanatory notes, translator's notes, or footnotes.
- Do not introduce square brackets. If the source already contains a
  bracketed tag such as [inaudible], [crosstalk], [foreign] or
  [background], keep it, translated. Never invent one, and never delete
  one the source gave you.
- OFFICIAL NAMES GET ONE FORM AND KEEP IT. The name of a statute, a court,
  an institution, a case or an organisation may be kept in English - but
  whichever form you choose, use that exact form EVERY time it appears.
  Never translate it in one sentence and leave it in English in the next,
  and never shorten it on later mentions.
- A GENERIC DESCRIPTIVE PHRASE IS NOT AN OFFICIAL NAME, even when
  capitalised in the source. "Federal corporations" describing a category
  of companies, or a bare backreference such as "the Act" or "the
  Corporations" used as shorthand after the real name was already given in
  full, must be translated normally - only the actual full name of one
  specific statute, court, program or organisation may stay in English.
  And do not assume a recurring term is fine just because it never varies:
  a real submission left the English word "core" untranslated a dozen
  times in one document, consistently - consistency is not the same as
  correctness.
- SHORT WORDS ARE NOT EXEMPT. A pronoun, a contraction, or a quantifier
  ("I'm", "I'll", "it's", "all", "each", "anyone", "one", "they") must be
  translated every time, however common it is or however it is
  capitalised. A real run left "I'm" untranslated in an otherwise-
  correctly-translated sentence ("I'm suis en Indiana maintenant" -
  literally "I'm am in Indiana now"). None of these are proper names.
- Do not add any website, domain name, URL, email address, book title,
  citation or reference that is not already in the text. If the speaker
  describes something WITHOUT EVER NAMING IT AT ALL, leave it undescribed
  — supplying a name for something never named is an invention, however
  plausible. This is a different case from a name the speaker DID say but
  the transcript garbled: correctly recovering it through research is a
  correction, not an invention, at whatever confidence bar this prompt
  asks for that elsewhere.
- Write only in French. Not one word of Spanish, Italian, Portuguese or
  English may appear in your output, except inside a quotation, a title or
  a proper name that the text itself keeps in its original language.
- Translate EVERY term, including ones a French reader would probably
  understand. "slaveholders" must become "proprietaires d'esclaves", not be
  left as it stands because the meaning is transparent. If you have already
  translated a term once, never leave it untranslated later in the same
  document.
- Use no formatting characters at all. No asterisks, no underscores, no
  markdown. Titles are written in plain text: The Mind of the South, not
  *The Mind of the South*. What you produce is submitted as plain text and
  an asterisk arrives as literal punctuation in the middle of a sentence.

WHAT TO FIX:
1. Fluency. Rewrite anything that reads as machine-translated so that it
   reads as though written by a native French professional. Fix word order,
   agreement, tense, register, and awkward literal renderings. You may
   split one long English sentence into two French ones, or merge two into
   one, when that is what natural French rhythm calls for - French does
   not have to inherit the transcript's sentence boundaries, only its
   content and order. Vary your connectors, too: a translation that
   reaches for the same one every time it needs to connect an idea reads
   as translated even when every word in it is correct.
1b. FALSE COGNATES ARE STILL A TRANSLATION MISTAKE, EVEN THOUGH THE WORD
   EXISTS IN FRENCH. A word that looks like its English cousin is not
   always its translation. "Opportunité" for a plain "opportunity" reads
   as an anglicism where "occasion" or "possibilité" is the ordinary
   word; "impacter" is common in speech but "toucher" or "avoir une
   incidence sur" is more standard in writing. These are correct French
   words used in the wrong sense - nothing a spellchecker or a dictionary
   lookup on the word alone would catch.
2. Terminology consistency. Once you choose a French term for a recurring
   concept, use that same term everywhere. Never render one source term
   three different ways.
3. Register. Match the source. Formal source stays formal; conversational
   source stays natural, not stiff.
4. Domain accuracy. Identify what the document is about and use the correct
   professional vocabulary for that field — legal, medical, scientific,
   academic, technical, financial, or general. Where a field has a settled
   French term of art, use it rather than a literal translation. If a term
   is genuinely ambiguous, choose the reading most consistent with the rest
   of the document; never guess wildly, and never flag it with a note
   inside the text itself. (A proper name, a citation, a quantity's scope,
   or a garbled source clause - this prompt gives you a separate,
   structured way to flag each of those elsewhere - is not covered by
   this rule; use that mechanism for those, not an inline note either.)
5. Cohesion. The source is a spoken transcript, so it may contain false
   starts, repetitions, and filler that survived translation. Remove them
   and join the sentences so the result reads as clean written French.
   Preserve meaning; remove only the disfluency.
6. Technical notation. If the document is mathematics, chemistry, physics
   or a similar field, it was spoken, so the source says "x squared", not
   "x²". A simple one-off quantity may stay spelled out ("x au carré"),
   but anything structurally complex, general or repeated should use
   standard Unicode notation instead of spelling it out every time:
   "a-naught plus a-one z plus a-two z-squared, and so on" becomes
   "a₀ + a₁z + a₂z² + ...", not a wall of French words. Use superscripts,
   subscripts and symbols like "x²" and "H₂O"; never LaTeX or markup.

RESEARCH EVERY PROPER NAME, CASE CITATION, STATUTE AND INSTITUTION — BUT
ONLY GO AS FAR AS RESEARCH CAN ACTUALLY SETTLE. Where a garbled name
points to exactly one real, well-known match, use the correct form —
that is a correction, not a guess. But where research only gets you to
a PLAUSIBLE match rather than a certain one, or where using the correct
form means adding specific detail the garbled source gives no trace of
at all (a middle initial, "Jr.", an exact ordinal circuit number,
"published" versus "forthcoming"), that is a question only the audio
can answer, and you were never given the audio. In that case:
- Use the name or term WITHOUT the uncertain extra detail in the
  translation itself — the correct core identification, not the
  specific detail you cannot confirm.
- After the full translation, add one line reading exactly
  VERIFY AGAINST AUDIO: followed by each such spot and why you were
  unsure, one per line. Write NONE if there is nothing to flag.
This is not permission to stop researching or correcting — keep doing
both. It is a limit on confidence: a plausible research match is not
the same as a confirmed one, and only a human who can actually listen
closes that gap.

THE SAME LIMIT APPLIES TO WHAT A QUANTITY COVERS. If a single figure
precedes a list of categories and the source itself does not settle
whether it is one amount split across them or that amount applying to
each one separately, that is also a question only the audio can answer.
Translate the number and the categories without resolving the split, and
name the spot on the VERIFY AGAINST AUDIO line the same way.

THE SAME LIMIT APPLIES TO ANY SPOT WHERE THE SOURCE ITSELF IS GARBLED OR
INCOMPLETE, NOT ONLY A NAME OR A QUANTITY. A speech-recognition transcript
sometimes hands you a clause that does not parse, or parses two different
ways, through no fault of the translation — "our tankers' fades", "it's
easier, more so pity". Translate the most plausible reading you can
construct, the same way you would resolve any other genuine ambiguity —
but do not let a confident-sounding French sentence hide that the
underlying English was broken. Name the spot on the VERIFY AGAINST AUDIO
line the same way, with a short note on what made it unclear.

THIS IS ABOUT TRANSCRIPTION NOISE, NOT ABOUT WHAT THE SPEAKER ACTUALLY
MEANT. A clause that does not parse is a different problem from a clause
that parses fine but sounds inconsistent, uncomfortable, or self-
contradictory — a speaker genuinely saying two things that sit in
tension is real content, not an artifact, and smoothing it into
something that reads more logically is an invention the audio can't
excuse. Translate what the source actually says, contradiction and all;
only route a spot to VERIFY AGAINST AUDIO when the WORDS THEMSELVES do
not parse, never merely because the MEANING is surprising.

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
- Centuries: a Roman numeral with a superscript ordinal - XIXᵉ siècle,
  XXᵉ siècle - never a plain digit ("19e siècle") and never spelled out
  ("dix-neuvième siècle")

STRUCTURE:
- Keep the paragraph breaks of the input. One blank line between
  paragraphs. Do not merge everything into one block and do not add
  headings that are not in the input.

Reply in exactly this shape and nothing else — no source text repeated,
no other commentary:

=== TRANSLATION START ===
(the corrected French text goes here — nothing else on these lines)
=== TRANSLATION END ===
VERIFY AGAINST AUDIO: (the line described above, or NONE)

You do not need to select just part of this reply — paste your whole
answer, markers and all, into the Polished French field. The platform
pulls the document out on its own and will show you the VERIFY AGAINST
AUDIO line separately after you save, if there is one.

--- SOURCE (English) ---
[the task's real English source goes here]

--- TRANSLATION (French) ---
[the task's real French draft goes here]
======================= COPY TO HERE ========================
```

---

## Why each rule is there

Every rule above corresponds to something that is either **checked
automatically before you can submit**, or **known to cause a rejection**.
None of it is stylistic preference.

| Rule | What enforces it |
|---|---|
| Number FORM unchanged | Nothing automatic sees it: `16,67` has the same VALUE as 16 2/3. Written after task 4813 was blocked for it |
| One form per official name | Not automatically checkable — see "What a FAILED review said" |
| Brackets kept, never invented | An accepted document ended in `[incompréhensible]`; the old rule would have had a worker delete it |
| No spellchecker damage | `language.autocorrect-damage` — **HIGH**, named in a failed review |
| No character corruption | `encoding.mojibake` — **CRITICAL**. 205 sequences went unseen by every other check |
| No commentary | `check_commentary` — a "Here is..." line blocks submission |
| Nothing dropped or padded | `check_length_anomaly` — flags a paragraph that vanished |
| No English left behind | `check_source_leakage` — flags surviving source text |
| Numbers unchanged | `check_numbers` — **CRITICAL**, blocks submission outright |
| Quantity's scope across categories preserved | Not automatically checkable — see below |
| A garbled source clause flagged, not silently resolved | Not automatically checkable — see below |
| Sentence boundaries allowed to move for French rhythm | Not automatically checkable — a judgment call, not a defect class |
| Connector variety | Not automatically checkable — see below |
| False cognates (*opportunité*, *impacter*, …) | Not automatically checkable — see below |
| Centuries as `XIXᵉ siècle`, not `19e siècle` | Not automatically checkable — see below |
| A genuine source contradiction left alone, not smoothed over | Not automatically checkable — see below |
| No repeated paragraph | `check_duplication` — the signature of a model looping |
| No invented references | `entity.invented-reference` — **HIGH**. Added after a real run supplied `planethunters.org`, a site the speaker never named |
| French only | `language.foreign-word` — **MEDIUM**. Added after a real run wrote Spanish `decir` where French needs `dire`. Quoted titles are exempt |
| No formatting characters | Stripped automatically on paste-back, after a real run wrapped book titles in `*asterisks*`. Eagle receives plain text |
| Every term translated | Not automatically checkable. A real run left `slaveholders` in English in one sentence while translating it correctly two lines earlier — the kind of thing only a reader catches |
| Decimal comma | `typography.fr.decimal-separator` — **MEDIUM**, treated as a *meaning* defect: `3.14` read by a French reader as a thousands separator is a different number |
| Guillemets | `typography.fr.guillemets` |
| Space before `%` | `typography.fr.percent-spacing` |
| NBSP before `; : ! ?` | `typography.fr.nbsp-before-high-punctuation` |
| Technical notation for complex/repeated expressions | Not automatically checkable — see below |

The number rule is the one to take most seriously. It is the only
**CRITICAL** check in this list and it blocks submission with no override.

One thing it no longer does: converting a spelled-out number to a figure
("one" → "1") is **not** flagged, because the value is unchanged and nothing
was invented. That used to block perfectly good work. Changing a value, or
introducing a figure the speaker never gave, still blocks.

## Technical notation is a judgment call, not a fixed conversion

Rule 6 was added after an accepted document (a mathematics lecture) showed
what real accepted work actually does: the translator spelled out one
simple, one-off mention ("x au carré") but used real Unicode notation —
`x³`, `z²`, `zⁿ`, subscripted variables like `γ₁` and `aₙ` — for a general
polynomial written out with many terms. Neither pure spelling-out nor
converting everything to notation matches what got accepted; the working
rule is "simple stays spelled out, complex or repeated becomes notation."

This is not math-only. Chemistry, physics, and any other technical field
spoken aloud can call for the same judgment (`H₂O`, unit superscripts,
etc.) — rule 6 names mathematics, chemistry and physics only as examples of
"a similar field," not as an exhaustive list, the same way rule 4 refuses a
fixed list of domains.

**This interacts with rule 1's number check, and the review prompt says so
explicitly.** A digit that only exists because rule 6 converted spoken
words into notation — the "2" in "x²" — is not a number the source failed
to state; the source said "squared" out loud. The review prompt's numbers
check carries an explicit exception for exactly this, so a worker's own
check pass does not fight its own polish pass.

## Why it covers every Eagle niche

There is deliberately no list of domains with special instructions for each.
Eagle sends legal, medical, academic, scientific, technical, financial and
general material, and a fixed list would be wrong the first time something
arrives that is not on it. Rule 4 instead asks the assistant to **identify
the domain from the document and apply that field's conventions** — which is
the same approach the platform's own quality engine takes: domain is
discovered per document, never hardcoded.

## Why the source is embedded now — a real side-by-side comparison

Older versions of this prompt gave the assistant only the French, on the
reasoning that a fluency/terminology polish is a French-language problem.
It is not, entirely: a real polish pass was compared line-by-line against
the same document's check-prompt findings (which has always carried the
source), and the comparison found real, structural blind spots — 35 of 38
flagged issues were fixed cleanly, but the 3 that were not all trace to
the same cause:

- **A dropped identifying phrase** ("in the mid-1970s, the House of Lords
  ...") survived the polish pass untouched. There was nothing wrong with
  the French sentence on its own — it read fine — so a source-blind
  assistant had no way to notice a clause was simply missing.
- **A statute cited in two different forms** at two different points in
  the source ("Succession Law Reform Act" and "Successional Reform Act")
  was normalized to one form throughout — plausible on its own, but wrong
  at the specific occurrence that used the other form, because nothing
  in the French alone could say which form belonged where.
- **A corrupted case name** ("St Edmund and St Edmund", almost certainly
  Steadman v Steadman, an actual House of Lords case) survived unresearched
  — the missing RESEARCH EVERY PROPER NAME instruction above, not the
  missing source, though having the source is what makes a corrupted name
  worth researching in the first place rather than reading as ordinary
  prose.

None of these are catchable by making the polish instructions stricter
about French alone — they are only visible next to the source, which is
exactly the class of defect the check prompt has always been built to
catch (`docs/TRANSLATION_POLISH_PROMPT.md`'s sibling, the review prompt,
already embedded the source for this reason). Polish now gets the same
treatment: same source-embedding, plus the same instruction to research a
garbled or unfamiliar name rather than transcribing the corruption.

The review/check prompt changed too, in one more way: on a clean document
it used to reply with only the word CLEAN, which left a worker with
nothing to paste back except by asking their assistant a second, separate
question. It now replies with CLEAN plus the translation, unchanged,
ready to paste straight into the Polished French box either way.

## VERIFY AGAINST AUDIO — a limit added right after the source-embedding change, from a real disagreement it caused

Giving polish the source (above) fixed one real gap and immediately
surfaced a second one, live, on a real task under a real deadline. The
garbled source said a judge's name mangled past recognition — "Alino...
JoJo Scanlon" — and research correctly identified the real people:
Samuel A. Alito Jr. and Diarmuid F. O'Scannlain. One assistant, asked to
polish, wrote the full researched form, "Jr." and Ninth Circuit and all.
A second assistant, asked to audit that result, called the added detail
an unconfirmed invention — the garbled fragment has no trace of "Jr." or
an ordinal circuit number, so where did they come from? — and argued for
keeping the fuller version anyway, on the theory that a formal bio
paragraph plausibly says exactly that.

**Both assistants were guessing, in opposite directions, and neither had
heard the audio.** There was no way to settle it from text alone — and
worse, a worker relaying between them nearly submitted whichever
answer came from whichever assistant they asked second, which is not a
process, it's a coin flip with your deadline riding on it.

The fix is not "trust the more detailed guess" or "trust the more
conservative one." It's to stop treating a plausible research match as
equivalent to a confirmed one. Polish now distinguishes the two: an
unambiguous correction (there is only one real "Vaughan lecture" the
garbled "bond lecture" could be) still gets made outright, in the
translation itself. Anything past that — genuinely uncertain, or adding
specific detail the source gives no trace of — gets the core correction
made conservatively in the text, and the uncertain part named separately
under a `VERIFY AGAINST AUDIO:` line, for a human who can actually
listen to settle. See `worker_prompts.py`'s `_POLISH` for the exact
wording, and `TASKER_GUIDE.md` for what to do with that line: nothing,
at paste time — paste the whole reply as usual and the line shows up
separately on screen after saving (see the section below for why it is
no longer the worker's job to keep it out of the paste). Listen to the
spots it names, then decide.

**One reconciliation this required, caught by a second assistant reading
the finished prompt.** `_MUST_NOT`'s "your entire reply must be the
corrected text and nothing else" was written long before
`VERIFY AGAINST AUDIO` existed, and appears earlier in the prompt than
it — a model (or a person) reading top to bottom hits the absolute rule
first. Likewise `WHAT TO FIX`'s domain-accuracy rule already said "never
flag [an ambiguous term] in the text," written for an ordinary technical
term, long before there was a *deliberately* flagged category to
distinguish it from. Neither contradiction was a reason to remove
`VERIFY AGAINST AUDIO` — that would have quietly restored the exact
problem it exists to prevent — so both older rules now name their
exception explicitly instead.

**A second real risk, caught by a tasker reading their own results
side by side — and the fix that followed it took two tries.** Telling a
worker not to paste the `VERIFY AGAINST AUDIO` line (or a `CLEAN`
verdict) was, for a while, the whole safeguard — a sentence in this
document and in the prompt itself, with nothing making the two parts of
the reply visually different from each other. The first fix wrapped
ONLY the document in `=== TRANSLATION START ===` / `=== TRANSLATION
END ===` markers with the footer explicitly outside them, on the theory
that a clear boundary is enough for a worker to copy the right part by
hand.

**That held for about as long as it took a worker to actually try it.**
A worker's own assistant UI (seen live: Gemini's canvas view) renders a
long reply in its own document card with ONE copy button for the whole
thing — Gemini has no way to know two of the lines in it are
instructions rather than content, so the obvious, natural action
(click the copy icon) copies the markers right along with the document.
Asking a worker to hand-select around a visual boundary instead of
using the copy button sitting right there was never going to hold up.

**The actual fix is `worker_prompts.strip_reply_scaffolding`: the save
path now accepts the WHOLE reply and pulls the document out of it
itself**, the same way a person reading it would — so pasting
everything is the expected path, not a mistake to avoid. A real
`VERIFY AGAINST AUDIO` note is never silently discarded in the
process: it comes back in the save response and is shown to the worker
separately, on screen, rather than depending on them having noticed it
in the raw text before pasting. `check_commentary` on the server still
blocks a leaked `VERIFY AGAINST AUDIO:` line, a leaked `CLEAN`, or a
leaked marker as a backstop, for whatever shape the stripping function
does not recognise — belt and suspenders, not either one alone.

## Long documents

A twenty-minute task is roughly **3,900 words of English**, and French
usually runs 10-20% longer for the same content — so the polish/review
paste, now carrying both languages, is roughly **8,000-9,000 words**
total. That is not actually the risk it sounds like: it is the same size
the review/check prompt has always sent (it has always embedded the
source), and it is a small fraction of any current assistant's context
window — an outright "too long, rejected" input is not a realistic
failure mode at this size.

What can still happen is the REPLY stalling or truncating on a very long
single-turn request — and the reply is still just the French, unchanged
in size by any of this. If that happens, split the body in half and run
the prompt twice, then join the two outputs with a blank line between
them.

Split **at a paragraph break**, never mid-sentence, and make sure the second
half starts where the first ended — no overlap and no gap. Terminology
consistency is the thing most likely to suffer across a split, so if a key
term appears in both halves, check it reads the same in both.

## What a real run actually produced

This prompt was tested on a genuine task before being handed to anyone. A
1,903-word machine draft came back at 1,874 words — nothing dropped, nothing
padded — with every key term intact (`LGM1`, `LGM2`, `BLC1`, `Proxima`,
`Parkes`, `Kepler`, `TESS`, `2019`, `2012`). Sentence punctuation was
repaired, mid-sentence capitals from transcript boundaries were fixed, and
guillemets and typographic apostrophes were applied correctly. It took under
two minutes.

Three things went wrong, and all three are now checked automatically:

- **An invented reference.** `planethunters.org` appeared in the output. It
  is the correct site for what the speaker was describing, and it is
  nowhere in the English. A plausible invention is more dangerous than an
  obvious one, because nobody questions it.
- **A Spanish word.** "on peut *decir* que la planète est là" — French
  needs `dire`. Close enough to read as a typo, far enough to be wrong.
- **Square brackets**, which the prompt already forbade and the assistant
  used anyway.

That is why the two rules above about references and language exist: they
were written from a defect, not from imagination. Read your output for
those three specifically.

## What the first live submission found

Task 4452, a 25-minute lecture, submitted and accepted on 29 August 2026.
3,084 English words became 3,206 French — nothing dropped across the whole
recording. Three things needed fixing before it went:

- **`slaveholders` left in English** in one sentence, while the same
  document rendered it correctly as `proprietaires d'esclaves` two lines
  earlier. Nothing automatic caught it; the foreign-word check carries
  function words, not vocabulary.
- **18 markdown asterisks** around book titles. Now stripped on paste-back,
  because Eagle receives plain text and would have shown them literally.
- **An ASR error in the SOURCE**: the speaker said "the 1800s", the machine
  heard "the 18s", and the translation sensibly wrote "les annees 1800" —
  which the gate then reported as an invented number. The fix was to
  correct the English, not the French.

That last one is worth remembering. When a number finding looks wrong,
check the English first: the transcript is machine-made and the translation
may be more faithful than its source.

## What a FAILED review said

The first review feedback we have seen, and the only direct evidence of what
a reviewer actually marks down. Three items, in their words:

**1. `don't` for `dont`.**

> « don't nous avons tous entendu parler » -> « dont nous avons tous entendu
> parler ». *don't is an English/French typo. The correct French relative
> pronoun is dont.*

An English spellchecker run over French text. This is now checked
automatically (`language.autocorrect-damage`, HIGH) along with `Vie siecle`
for `VIe siecle`, which is the same corrector turning "the 6th century" into
"the life century".

It was briefly MEDIUM, because an ACCEPTED document had been found
containing ten of them. This feedback settled it: acceptance elsewhere meant
the defect got past one reviewer, not that it is tolerated.

**2 and 3. An official name used in two forms.**

> « la Family Law Reform Act » ... *The English statutory title may be
> retained, but it should be consistently treated as the official name of
> the legislation rather than translated inconsistently.*
>
> « designer la Family Law Act comme loi applicable » ... *Grammatically
> acceptable, but the statutory title should be kept consistent throughout
> the transcript.*

Note what is NOT being asked for. The reviewer does not object to the
English title being kept — that is explicitly allowed. The objection is that
the document used more than one form for one Act.

**This one is not automatically checkable and is not pretended to be.**
`Family Law Reform Act` and `Family Law Act` may be two different statutes,
so a rule that flagged the pair would be wrong about half the time. It is in
the prompt instead, and the check prompt asks for a list of every official
name with each form it appears in — which is a job for a reader, not a
regex.

## What another failed review flagged: what a number covers

> « un montant supplémentaire de 100 $ en revenus d'intérêts, en revenus de
> dividendes et en gains en capital » ... *A single $100 amount cannot
> naturally be described as simultaneously being all three types of
> income. The source may mean $100 of each type.*

A single figure sitting in front of a list of categories is ambiguous in
exactly the way a garbled name is: readable, plausible, and impossible to
settle from the target text alone. This is not automatically checkable
either — `check_numbers` verifies a value survives, not what it applies
to, and a rule guessing at "one figure, several nouns" would flag ordinary
shared totals ("$100 in fees and taxes" is usually one $100) as often as
real splits.

The fix borrows the same two-part answer VERIFY AGAINST AUDIO already
gives a garbled name: check the source for which reading it actually
supports — most of the time that settles it outright, since the source
sits right there in the same prompt. If the source itself does not settle
it, that is a question only the audio can answer, same as an unconfirmable
name, so it goes on the same line rather than getting silently resolved
one way or the other.

## What a controlled comparison taught us: two assistants, same prompt, same document

The same real task (a foreign-policy lecture, ASR source and all) was run
through two different assistants under the prompt as it stood before this
addition. The deterministic side came back identical — clean, same
numbers, same tolerated NBSP finding. The judgment calls diverged sharply:

One assistant flagged four spots on `VERIFY AGAINST AUDIO` — all four were
places the English source itself did not parse ("our tankers' fades",
"it's easier, more so pity", "All signed familiar echoes from the past").
The other flagged **zero**, and silently resolved every one of them
itself — in one case by translating literally, in another by quietly
inverting a clause's polarity to make it read sensibly ("is that we want
to achieve this" became "is that we fail to achieve this").

Neither assistant was actually wrong by the letter of the prompt as it
stood: `VERIFY AGAINST AUDIO` was scoped to two named categories, a
garbled name and a quantity's scope, and plain garbled prose was neither.
One assistant extended the mechanism's underlying principle anyway; the
other read the categories narrowly and fell back to rule 4's ordinary
"choose the best reading, never flag inline" default — which is exactly
what let a clause's meaning get silently inverted with nothing to show
for it afterward. That is a prompt gap, not a compliance failure on
either side, and it is the reason this addition names the general case
explicitly rather than trusting every assistant to generalize the
narrower one the same way.

**Worth separating from this**: the same comparison also caught a real
terminology-consistency miss — one assistant rendered a single recurring
metaphor ("tanker") three different ways across one document, the other
held it consistent throughout. Rule 2 already says not to do this, in
those words. That one did **not** get a prompt change — restating an
existing instruction more loudly does not fix an assistant not following
it once, and the fix for that class of thing is catching it on the
before-submit read, not more prompt text.

## What a coach's own audit prompt taught us, and what it didn't

A separate two-stage audit prompt, written independently for the same
platform, was compared line by line against this one — not to replace it,
but to check for real gaps on either side. Five things from it earned a
place here:

- **Sentence-boundary license.** It explicitly authorises splitting or
  merging a sentence for native rhythm; this prompt previously said
  nothing beyond "fix word order", which reads as license to reorder
  words, not to redraw where one sentence ends and the next begins.
- **Connector variety.** It calls out repetitive `aussi`/`de plus`/`en
  outre` as a translationese tell in favour of `or`, `en effet`, `par
  ailleurs`, `en l'occurrence` — a real, well-known pattern this prompt
  had no equivalent for.
- **False cognates, named as their own category.** `opportunité`,
  `impacter` and the like are grammatically correct French words used in
  the wrong sense — invisible to a spellchecker and to the "translate
  every term" rule alike, since the word IS translated, just wrongly.
- **Centuries with a superscript ordinal.** `XIXᵉ siècle`, not `19e
  siècle` or spelled out — a real French convention this prompt simply
  never stated.
- **Preserve a genuine contradiction, don't resolve it.** Its own rule —
  "do not fix logical conflicts in the original text unless it is an
  explicit typo" — is the missing other half of the garbled-clause
  addition above. Without it, "translate the most plausible reading" risks
  reading a speaker's real, uncomfortable point as transcription noise and
  smoothing it into something tidier, which is not this prompt's call to
  make.

What did NOT get adopted, and why: its domain-specific terminology
sections (`le droit du mandat`, `dry dock` → `cale sèche`, `Tarsus` →
`Tarse`, and dozens more) read as an accumulating answer key from whichever
legal and historical documents its author has personally handled, not a
generalizable rule — irrelevant noise on most documents, and actively
risky on ones where a flagged word is the *correct* word in context, since
the rule names no condition for when the anglicism reading actually
applies. That is precisely why this prompt stays domain-*discovering*
(rule 4) rather than domain-*listing*. Its output format also has no
equivalent of `strip_reply_scaffolding` — no described mechanism for
pulling the actual translation out of a reply that also contains an
adjustments table and headings — which would reintroduce the exact leak
risk `VERIFY AGAINST AUDIO`'s marker-and-strip design already fixed here.

## What three accepted documents taught us

Accepted work is the only evidence that separates a real finding from noise,
and running the gate over three of them found the gate wrong more often than
the documents:

| Finding on ACCEPTED work | Verdict |
|---|---|
| `19th` -> `XIXe` reported as an altered identifier code | false positive, exemption added |
| `60-year-old` -> `60 ans` reported the same way | false positive, exemption added |
| `the` inside *The Mind of the South* reported as a foreign word | false positive, unquoted titles now exempt |
| NBSP missing before `; : ! ?` | true, and tolerated — seen on two accepted documents |
| 205 mojibake sequences invisible to every check | real blind spot, now CRITICAL |
| 15 spellchecker errors invisible to every check | real blind spot, now HIGH |

Three false positives removed, two blind spots closed. Send accepted work
and failed reviews both: the first tells us what the gate gets wrong, the
second tells us what it is missing.

## Translating from scratch

When the machine translation is missing — the platform's own quota can run
out — the same rules apply, with one change to the opening line:

> The text below is the English transcript of a recorded lecture. Translate
> it into French to professional publication standard.

and one addition to the instructions:

> This is a spoken transcript, so it contains false starts, repetitions and
> filler. Remove those and join the sentences so the French reads as clean
> written prose. Preserve every idea; remove only the disfluency.

Everything else is unchanged. A 25-minute task is about 3,000 words, which
is usually two chats: split at a paragraph break, never mid-sentence, and
join the halves with a blank line.

## What this prompt does not do

It does not check itself. Paste the result back into the platform and let
the gate run — that is what catches a dropped paragraph or an invented
figure, and it does so without anyone needing to read French. If the gate
reports findings, fix those specific items and re-check. Do not submit
around a finding.
