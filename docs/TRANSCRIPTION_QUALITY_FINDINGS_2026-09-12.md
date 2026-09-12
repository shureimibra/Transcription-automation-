# Transcription quality: synthesis of a 19-document real-sample review

**Based on**: 19 real, accepted-format transcription submissions reviewed
line-by-line on 2026-09-12, several confirmed as sequential parts of the
same underlying recordings (split across separate Eagle tasks). Companion
to `TRANSLATION_QUALITY_FINDINGS_2026-09-12.md` — same method, same day,
transcription side.

---

## 1. Is speaker diarization working?

**Labeling format: yes, consistently.** A dedicated deterministic check
(`check_speaker_labels`, CRITICAL severity) verifies every line is exactly
`Male_N:` or `Female_N:` — no names, no titles. Across all 19 documents,
zero format violations. The "does the '1' in Male_1 ever get wrongly
flagged as a number needing to be spelled out" concern was checked
directly in code: `check_number_rules` strips speaker-label lines and
everything in brackets *before* scanning for digits
(`_strip_uncheckable`, `transcription.py:469`). Confirmed correct as
designed — not a live bug.

**Boundary accuracy is a different question, and less certain.** One
document ("Understanding Longitudinal Waves in Pipes") shows a suspicious
pattern: `Male_1` gives only short transitional lines ("I am trying to
generate it...", "Let me see.") and `Male_2` immediately continues with
the substantive explanation, every time, with no dialogue between them —
reading as one continuous lecturer split across two diarization labels.
Correct labeling convention does not guarantee correct speaker-change
detection; this is the kind of case a tasker needs to actually listen to,
not something any check can resolve from text alone.

---

## 2. Is AI Assist effective, for transcription?

Same calibrated, two-sided answer as the translation side:

**Reliable**: mechanical rubric compliance — number formatting (0-9 as
words, 10+ as figures), contraction expansion, filler removal, repeated-
word detection, timestamp continuity — is caught well by the deterministic
checks *when it's wrong*, and several documents show it done correctly
throughout. **Also reliable, and genuinely impressive**: repairing heavily
garbled ASR into coherent, *correct* meaning — "mining their own business"
→ "minding," "acquittable execution" (nonsense, repeated 5+ times) →
consistently "exécution équitable"-equivalent correct readings, "eventor"/
"mortgageor" → "vendor"/"mortgagor." This is the standout strength.

**Unreliable, and the real cost driver**: proper-noun and domain-term
accuracy. See §4 — this is worse and more consequential on the
transcription side than it was even for translation, because a
transcript's whole authority rests on accurately capturing what a named
expert actually said.

**Roughly half of documents never received an edited-verbatim contraction
pass at all.** Documents with 49-81 unexpanded contractions: 2, 5, 7, 10,
12, 15, 17 — seven of nineteen, a severe and completely unaddressed
violation each time, sitting right next to other documents that are
genuinely clean on this exact point. Confirms the same "intermittent, not
systematic" character found throughout the translation review — the
capability clearly exists (proven by the clean documents) and simply
isn't applied consistently.

---

## 3. Does it follow the required guidelines?

Checked against `docs/TASKER_GUIDE.md` §7 (edited verbatim standard) point
by point, across the batch:

| Rule | Verdict |
|---|---|
| Speaker labels `Male_N`/`Female_N` only | **Consistently correct**, all 19 documents |
| Numbers 0-9 as words, 10+ as figures | Correct roughly half the time; **severely undercounted by the gate** whenever multiple small numbers appear in one list or equation (see §5) |
| Contractions expanded | Correct in ~12 of 19; **severely violated** (49-81 instances) in 7 |
| Fillers/false starts removed | Generally clean — no leftover "um"/"uh" found anywhere in the batch |
| Only the six permitted bracket tags | No misuse found; one ambiguous case (a possible missing `[pause]` on a 36-second silent gap) |
| Timestamp continuity | Clean in every document spot-checked |
| `[END OF RECORDING -->]` marker present | Present and correctly formatted in all 19 |

**Bottom line**: the rules that are mechanically checkable are followed
inconsistently but *detectably* — a worker reading the findings panel and
acting on it will catch nearly all of these. The rule that matters most
and is never checked at all — did the transcript correctly capture every
name and technical term — is a different story entirely.

---

## 4. What's actually broken: the same lesson as translation, confirmed harder

**Proper-noun and domain-term accuracy is the real, uncaught defect
class**, and this batch produced dramatically more and higher-stakes
examples than the translation review did, because these are lectures by
named experts on specific, verifiable subjects. A partial list of
confirmed real errors, none flagged by any check:

- Real scholars' names mangled: **Milnor** ("Milner"), **Dirac**
  ("Durac"), **de Broglie** ("De Bruy," "debris" ×3), **Thurston**
  ("Firston," "thirsten"), **Sommerfeld** ("Summerfeldt"), **Riemann**
  ("reman"), **Aspect** ("Alan Haspeh" — a Nobel laureate), **Couder**
  ("Eve"/"Eves").
- Real terms mangled: **Seifert surface** ("ciphate"), **Sierpinski**
  ("Pinsky"/"Spinsky" — two different wrong spellings in one document),
  **Delaunay** ("Deloni"/"Deloini"), **Voronoi** ("Voronoid"), **Faraday**
  ("ferritate"/"ferreter" — despite being spelled correctly a dozen times
  elsewhere in the *same* document), **Friedel oscillations** ("Fridel"),
  **quantum corral** ("crown," in the same document that also gets
  "corral" right), **sphericity** ("severity"/"fericity"), **quartic**
  ("cortic"), **Nesquik** ("Nest Quick"), **Play-Doh** ("Play Do"),
  **SCHOTT** (the real glass manufacturer, → "Shot"), **Hammarby**
  ("Hammabee"), **water striders** ("striters"), **Nature's** (favorite
  numbers, → "Nietzsche's"), **tabla** ("tabler").
- A whole subject noun wrong for half a document: **"bore"** (the literal
  subject of the talk) transcribed as **"ball"** for the first ~40% of one
  document, then correctly for the rest.

**A new, sharper piece of evidence beyond what translation showed**:
cross-task drift proven with literally duplicated text, not just inferred
from context. Two documents in this batch end/begin with the *identical*
sentence — "the Lorentz [Dirac/Durac] equation... has runaway solutions"
— rendered correctly in one task and incorrectly in the very next. Same
audio, same words, two separate transcription passes, two different
outcomes. This is the cleanest possible proof that there is no shared
state between adjacent Eagle tasks, even at a literal sentence boundary —
and that a name can independently come out right or wrong on each pass.

---

## 5. Real, fixable gaps in the deterministic checks themselves

1. **No exception for alphanumeric technical notation** in
   `check_number_rules` — confirmed five separate ways: musical pitch
   ("E4," "A3"), dimensional shorthand ("3D"), variable/identifier labels
   ("D1," "D0," "K1," "K2"), exponents ("10 to the minus 6"), and a
   multiplier ("4G"). Same shape of gap as the "1L" identifier exception
   already built for translation — needs the same treatment here.
2. **`_SLANG` conflates "'cause" (informal "because") with the ordinary
   verb "cause"** — confirmed live in this batch ("to cause this walker to
   move" flagged as informal). The regex matches the bare word regardless
   of which one it is.
3. **The lowercase-block-start check has no way to know a block continues
   the previous one** unless the previous block happened to end with a
   trailing "…". Confirmed a case with *no* ellipsis at all where a
   sentence was still split across two timestamp blocks — the real fix
   needs to check whether the *previous* block ended with terminal
   punctuation, not just look for a leading ellipsis on the current one.
4. **Every "one example shown" finding likely understates the real
   count**, most dramatically for number-lists (a single sequence like
   "2, 9, 10, 11, 13, 16" can carry 2-6 real violations, one shown) and for
   long-monologue/paragraph-break findings (multiple 300+-word
   punctuation-light blocks in one document, one flagged).

---

## 6. Does everything meet submission requirements? What's the rejection rate?

**Meets the gate's own bar**: yes, all 19 documents had `blocking: false`
— nothing CRITICAL, nothing that stops submission under the platform's
current rules. By that literal definition, 19/19 "meet requirements."

**That is not the same claim as "19/19 are correct."** Two documents in
this batch showed "No findings... matches the format the platform has
accepted" while containing multiple real, high-confidence proper-noun
errors including a Fields Medalist's name and the literal subject of the
talk. "No findings" measures compliance with the checks that exist, not
correctness.

**Rejection rate: I don't have this, and can't respond to that with a
number.** This review sample is drawn from the submission-preview gate's
own output — documents that already passed local checks and were shown to
a worker before submission — not from actual Eagle review outcomes. What
happens after submission (accepted, rejected, and why) is data this
review has no visibility into. Answering that would need the platform's
own real submission-outcome records, not this audit.
