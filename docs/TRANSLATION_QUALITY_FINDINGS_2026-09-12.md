# Translation quality: what's actually broken, and what to do about it

**Based on**: a real-sample review of 11 translation submissions pasted into this
session on 2026-09-12 (8 distinct documents, 3 repeats), reviewed line-by-line
against their English source where available. This is real evidence, not a
guess — every example below is a direct quote from an actual submitted
translation.

---

## 1. What's actually broken

**It is not the numbers, and it is not the meaning. It is a narrow, specific
category of words that the translation model sometimes leaves untranslated
inside otherwise-correct French — and the automated gate cannot see it at
all.**

Two things are true at once, and both matter:

- The AI **is** good at the hard part: understanding heavily garbled,
  ASR-mangled English and producing the *correct* French anyway. Confirmed
  dozens of times across the batch — "mining their own business" → correctly
  "s'occuper de leurs propres affaires" (minding); "acquittable execution"
  (nonsense) → correctly, consistently "exécution équitable" (equitable
  execution) across 5+ repetitions in one document; "strepable" → correctly
  "détachables" (detachable); "the eventor... the mortgageor" → correctly
  "le vendeur... le débiteur hypothécaire." This is the translator's real
  strength and it is reliable.
- The AI **unreliably** leaves certain short, common, or institution-shaped
  words untouched — in English, sitting inside a French sentence — and does
  this *intermittently*: the exact same word or phrase is sometimes
  translated correctly and sometimes leaked, including within the same
  document and sometimes the same sentence.

### The leaked-word list (every one confirmed from a real submission)

| Category | Confirmed examples |
|---|---|
| First/second-person contractions | I'm, I'll, I've, It's, That's, They, You're |
| Indefinite pronouns / quantifiers | All, Each, Anyone, One |
| Common adjectives/nouns | Canadian, United States, U.S., November |
| Institutional shorthand (a term established once, then referenced generically later) | Act / Acts, Duty, Corporations, Federal Corporations |
| Compound adjectives | Canadian-controlled, NEP-motivated |
| A whole recurring technical term | **core** — left untranslated ~13 times in one document, never once rendered as its own earlier-established French equivalent ("le taux de propriété canadienne") |
| A whole category of proper nouns | **court names** — Supreme Court, County District Court, small claims court, provincial court, unified family court all left in English, repeatedly, in one document |

A separate, distinct issue: **currency formatting is inconsistently
localized.** Some documents correctly render "$5,143" as "5 143 $" (French
convention); others leave a whole string of figures in raw English format —
"$60,000", "$27,500" (×4) — never converting to French style at all, in the
same document series.

### The "doubled marker" tell

The clearest proof this is a real defect and not a stylistic choice: several
leaks show the model translating *around* the leaked word correctly —
"**I'm** suis en Indiana" (literally "I'm am"), "**It's** est soit 100% soit
0%" (literally "It's is"), "**I'll** enlèverais" (literally "I'll would-
remove"). The rest of the sentence is right; only the pronoun/contraction
itself got left behind.

### Why the gate doesn't catch it

Read directly from the check code (`core/services/ai_assist/quality/deterministic.py`):

- `check_source_leakage` only flags **8-word-or-longer verbatim runs** of
  source text surviving into the target — a design choice made specifically
  to avoid false-positiving on short proper nouns and titles. A single
  leaked word is nowhere near that threshold.
- `check_foreign_language` explicitly **skips every capitalized token**
  (`if token[:1].isupper(): continue`) on the theory that a capitalized word
  is a name, not a language slip. Nearly every leaked word in this whole
  review is capitalized (sentence-initial pronouns, "I"-contractions,
  institutional shorthand) — so this guard excludes almost the entire
  category by design.

**There is currently no deterministic check, and (as far as this review
could tell) no semantic-audit finding, that catches this defect class at
all.** The gate can say "no findings" on a document that a French reader
would find visibly broken.

### Is AI Assist "effective"?

Yes, for what it's reliably good at — and no, not for this. It's not a
binary answer:

- **Trust it** for: preserving numeric values, repairing ASR mishearings
  into coherent meaning, applying grammatical agreement, and for many
  specialized terms it clearly has good training on (adjusted cost base →
  "prix de base rajusté", a "collar" → "tunnel", "ambulatory" (of a will) →
  "révocable").
- **Do not trust it** for: leaving a word untranslated when it resembles a
  name/quantifier/contraction, or for consistently formatting currency.
  These failures are invisible to the automated gate, so a human is
  currently the only safety net.

---

## 2. What taskers should do, every time, before submitting

The gate cannot see this defect class, so this is manual review, and it does
**not** require reading French fluently — every check below is
language-independent, because you are scanning for the *presence of English*,
not judging French quality.

1. **Read the whole translated document once, scanning specifically for any
   English word sitting inside a French sentence.** Capitalized English
   words mid-sentence are the easiest tell — French capitalizes far less
   than English, so a capitalized word that isn't the start of a sentence or
   a real proper noun is worth a second look.
2. **If you find a leaked term even once, search the whole document for
   every other occurrence of that same term.** The evidence strongly shows
   these repeat throughout a document once they start — "core" leaked 13
   times, not once.
3. **Check every institutional/technical term the document defines early on**
   (an Act, a court, a program, an acronym) — verify it's translated the
   same way, or correctly left in English, **every single time it recurs**,
   including shorthand backreferences ("the act," "the Duty," "the core")
   later in the document, not just its first full mention.
4. **Check every dollar/currency figure specifically for French format**:
   number first, space as the thousands separator, symbol last —
   "1 000 $", never "$1,000".
5. **Do not treat "no findings" from the platform's quality gate as proof
   the document is clean.** It currently checks structure, numeric values,
   and a few deterministic patterns — not this leakage class. A clean gate
   result and a genuinely clean document are not the same thing yet.

---

## 3. Custom instruction text taskers can use right now

The AI Assist custom-instruction field (available when generating or
regenerating a translation) reaches the real translation prompt at zero
extra cost. It will not guarantee 100% — this is a model tendency, not a
simple rule the model is failing to follow, so instructing it helps but does
not eliminate the need for the manual check above. Paste some or all of the
following:

```
Translate every single word into French, with no exceptions for pronouns,
contractions, or quantifiers. Do not leave "I'm", "I'll", "I've", "it's",
"that's", "they", "all", "each", "one", "anyone", or "you're" untranslated,
even when capitalized or positioned at the start of a sentence — none of
these are proper names.

If an institution, court, act, program, or defined term is translated (or
correctly kept in English) once in this document, apply that same decision
every time it recurs later, including short-hand references back to it
(e.g. "the Act", "the Duty", an abbreviation you introduced earlier) — do
not revert to leaving it in English partway through.

Format every currency amount using French convention: the number first,
with a space as the thousands separator, followed by the currency symbol
(e.g. "1 000 $", never "$1,000").
```

---

## 4. What this does not cover

This document is about one specific, now well-evidenced defect class
(word/phrase-level leakage and currency-format localization). It does not
cover, and should not be read as fixing:

- Cross-task name/spelling drift (the same person or term spelled
  differently across two *separate* Eagle tasks — confirmed live in this
  same batch: "Arthur Dredge" vs. "Arthur Dreych" for the same author).
  There is no shared state between separate tasks, so no per-task checklist
  item can catch this.
- Genuine source-transcript errors that the AI faithfully (and correctly)
  translated as-is, such as "was her or her" — the mishearing happened
  before translation ever touched it.
