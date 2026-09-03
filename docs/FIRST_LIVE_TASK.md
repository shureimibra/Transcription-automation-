# The first live task — how not to lose money on it

A rejection costs the minutes you paid for and the hour someone spent. This
is the order that makes that unlikely.

**The single most useful fact:** warming costs **no Eagle minutes**. Only
*claiming* spends money. So the entire document that would be submitted can
be rendered and read while the task is still sitting in the pool, untouched
by anyone.

```
warm  →  preflight  →  polish  →  preflight again  →  claim  →  submit
                                                       ↑
                                          the first and only time
                                            money is at risk
```

By the time you claim, you have already read the exact bytes that will be
sent. The claim exists to start the clock, not to find out whether the work
is good.

---

## Before the shift

**Pick the shortest task available.** Cost scales with audio length, and so
does the time to review it. A 20-minute task costs twice a 10-minute one and
proves exactly the same thing on a first run. If a 4–5 minute task appears,
take that.

```
manage.py eagle_preflight --ready
```

Lists every task far enough along to inspect, shortest first, and says
whether the translation has finished.

**Check the balance covers it, with room.** Minutes are billed as whole
minutes and rounded up. Claiming with barely enough leaves nothing for a
second attempt if something goes wrong.

---

## Step 1 — Read the document before touching anything

```
manage.py eagle_preflight 4492
```

You get, without spending anything:

- whether the balance covers the claim
- whether it renders at all
- the header checked line by line against a submission Eagle **accepted**
- for a transcription: timestamp count, speaker labels, end-of-recording
  marker, and any **name** used where `Male_1` belongs
- for a translation: the word-count line, that no timestamps leaked in, and
  whether a human has polished it or it is still raw machine output
- every quality finding, worst first

Add `--show` to print the whole thing and read it yourself. **Do that on the
first one.** The checks are good; your eyes are the reason they were written.

### What each verdict means

| Verdict | What to do |
|---|---|
| `BLOCKED` | Stop. Something critical. Fix it before anything else. |
| `submittable, but read the HIGH findings` | Read them. HIGH is the band that gets work sent back. |
| `looks submittable` | Proceed — after the polish step for a translation. |

---

## Step 2 — Polish (translation only)

Follow `TRANSLATION_POLISH_PROMPT.md`. Then **run preflight again.**

This is the step people skip, and it is the one that catches the damage.
Measured on a real run, the polish pass introduced two defects that were not
there before:

- an **invented website** (`planethunters.org`) the speaker never named
- a **Spanish word** (`decir`) where French needs `dire`

Both were caught mechanically, by checks that need nobody to read French.
Neither would have been noticed by eye.

---

## Step 3 — The last look

Read these yourself, whatever the checks say:

**Both kinds**
- Is `Translated By:` / `Transcribed By:` **your** name?
- Is the date today's, as `DD/MM/YYYY`?
- Does the first paragraph read like the audio starts?
- Does the last paragraph read like the audio *ends* — or does it stop
  mid-sentence? A truncated pipeline looks like a complete document until
  you read the final line.

**Transcription**
- Every speaker is `Male_1` / `Female_2`. **No names, no roles, no titles.**
- The `[END OF RECORDING --> HH:MM:SS.mmm]` line is present, and the time
  matches the audio length.
- Timestamps are continuous — each block starts where the previous ended.

**Translation**
- No timestamps anywhere. The deliverable is prose.
- The `(Number Of Words: N)` line is present.
- No English left in the French, and no "Here is the corrected version".

---

## Step 4 — Claim, then submit promptly

Claim only when everything above is done. The clock starts here and cannot
be stopped.

Claim → open the task → **Preview submission** one final time → Submit.

The preview is server-rendered, so it is the same document preflight showed
you. If it differs from what you expect, **stop and say so** — that
disagreement is information.

---

## If something goes wrong

**A claim is refused for minutes.** Correct behaviour, not a fault. Top up.

**The preview shows an error instead of a document.** The task is not ready
— usually the translation has not finished. Nothing was lost.

**The clock is nearly out.** Submit what you have. Good-enough submitted
beats perfect late; unsubmitted work scores zero and costs the minutes
anyway.

**It gets rejected.** Save the rejection reason verbatim. That is the only
direct evidence we have of what Eagle actually grades, and every reason you
capture makes the gate better. Nothing in this system was built from a
specification — it was built from two accepted documents and careful
guessing.

---

## What this cannot tell you

The checks are **deterministic**: format, structure, numbers, references,
language. They will not tell you the French is *good*, only that it is
well-formed and faithful in the ways a machine can verify.

For the first few tasks, that gap is worth closing by hand — have someone
who reads French look at one submission end to end. After that you will know
whether the machine checks are enough, and that is worth knowing before
fifty tasks a day depend on the answer.
