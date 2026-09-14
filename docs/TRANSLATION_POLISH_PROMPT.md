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
  Your entire reply must be the corrected French text and nothing else.
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
  describes something without naming it, leave it undescribed. Supplying
  the name you believe is correct is an invention, however plausible.
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
   agreement, tense, register, and awkward literal renderings.
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
   of the document; never guess wildly and never flag it in the text.
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

RESEARCH EVERY PROPER NAME, CASE CITATION, STATUTE AND INSTITUTION. Where
one is garbled or unfamiliar, a moment's research usually identifies what
it almost certainly is — use the correct form rather than transcribing
the corruption or guessing. If you genuinely cannot identify it, leave it
exactly as the source states it rather than inventing a plausible-looking
substitute.

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

STRUCTURE:
- Keep the paragraph breaks of the input. One blank line between
  paragraphs. Do not merge everything into one block and do not add
  headings that are not in the input.

Reply with the corrected French text only — nothing else: no source text,
no commentary, no note about what you changed.

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
