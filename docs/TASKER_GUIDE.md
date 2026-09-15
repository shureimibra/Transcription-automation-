# Tasker guide — start to submission

Read this once before your first shift. Keep it open for the first week.

The single most important thing on this page: **claiming a task starts a
one-hour countdown that cannot be undone.** Everything else follows from
that.

---

## 1. What the platform is

Eagle publishes transcription and translation jobs. Our tool watches Eagle,
downloads the audio, and runs the machine work **before anyone claims a
task** — so when you take one, the transcript already exists and your hour
is spent reviewing, not waiting.

You never open Eagle. You never see a cookie, a password, or an Eagle URL.
Everything happens on our screens, and the tool submits on your behalf.

## 2. Signing in

Go to the address your administrator gave you and sign in with your own
account. You will see:

| Page | What it is |
|---|---|
| **My work** | Tasks you are holding, and what you finished recently |
| **Transcription** | Available transcription work |
| **Translation** | Available translation work |
| **All tasks** | Both kinds together |

Transcription and translation are separate pages because they are different
jobs with different rules. Check both — supply is uneven, and there are
usually far more translation tasks than transcription ones.

## 3. Choosing a task — do this properly

Two things on every card matter.

**"Ready now"** means the machine has finished: the transcript exists, and
for a translation task the French exists too. **Prefer these.** A task
without the badge still works, but you will spend part of your hour waiting
for the machine — with the clock already running.

**"Listen first"** plays the audio *before* you claim. Use it. Every time.

Thirty seconds of listening tells you what the length and filename cannot:

- How strong is the accent?
- Is the recording clear, or is it phone audio in a noisy room?
- Do people talk over each other?
- Is it one speaker or six?

These are what decide whether a task fits in an hour. A clean 20-minute
recording is easier than a messy 10-minute one.

> **Do not claim a task you have not listened to.** This is the single
> cheapest habit that protects your hour.

## 4. Claiming

Press **Claim**.

At that moment: the task becomes yours on Eagle, platform minutes are spent,
and **a one-hour deadline starts**. It disappears from everyone else's
queue immediately.

You may hold **three tasks at once**. Finish or release one to take another.
Tasks you have submitted no longer count — your slot frees the moment the
work leaves your hands, not when Eagle approves it.

If Claim refuses you, the message says why. The two you will actually see:

- *"There are not enough platform minutes"* — the account balance is too low
  for this task's length. Not your fault. Tell your administrator.
- *"Someone else already started that task"* — another worker or someone on
  Eagle took it first. Pick another.

**Release** a task you cannot finish. Releasing early is always better than
missing the deadline. It returns to the queue for someone else.

## 5. The clock

The countdown is on screen while you work. It changes what it tells you:

| Remaining | What to do |
|---|---|
| Plenty | Work normally |
| Getting low | Stop improving; start finishing |
| Under a few minutes | **Submit now.** Good-enough submitted beats perfect late |

Work not submitted before zero is not accepted. There is no extension.

---

## 6. Transcription tasks

**The graded work is speaker identification.** Eagle cares more about who is
speaking than about elegant prose.

The screen gives you:
- The audio player, with a ruler so you can see where you are
- The transcript, one line per turn, with timings
- **The line the audio is on is highlighted, and the list scrolls itself.**
  You do not have to keep your place by hand.
- Click any line to jump to that moment

### Driving the audio

You will spend most of the hour here, so learn four keys and you will not
need the mouse:

| Key | Does |
|---|---|
| `Space` | play / pause |
| `←` `→` | back / forward 5 seconds |
| `,` `.` | **previous / next turn** — jumps to an exact boundary |
| `Home` | back to the very start |
| `[` `]` | slower / faster |

`,` and `.` are the ones worth learning. Scrubbing to find the start of a
sentence is guesswork; stepping by turn lands on it exactly. The small
notches on the timeline are where the turns are, so you can see the shape of
the conversation before you go looking.

Speed matters: 25 minutes of audio does not fit in a one-hour deadline at
1×. Most people settle around 1.5×.

### What to fix, in priority order

1. **Speaker labels.** Only `Male_1`, `Female_2`, and so on are permitted.
   Never a name, a title, or a role — not "John", not "the witness".
   - Wrong for one turn → change it on that line
   - Wrong everywhere → rename the speaker once, it applies throughout
2. **Words that are actually wrong.** Names, technical terms, numbers.
   Listen to anything that reads oddly; ASR mishears proper nouns most.
3. **Everything else.** Only if time allows.

### The transcription standard

Unless told otherwise, we work to **edited verbatim**:

- Remove fillers and false starts — *um, uh, you know, I mean*
- Keep contractions exactly as spoken — *don't* stays *don't*, never
  *do not* (per Eagle's General Guidelines — this changed; it used to say
  the opposite)
- Standardise slang — *gonna* → *going to* (but never *yeah* — that is
  already the correct spelling, not something to fix)
- Sentences still start with a capital, but nothing else about the
  grammar is corrected — a run-on stays a run-on
- Only these tags: `[inaudible]` `[unclear]` `[crosstalk]` `[pause]`
  `[laughter]` `[applause]` `[foreign]` `[background]` — use `[foreign]`
  for a foreign-language stretch inside an English recording, and
  `[background]` when a section is only background noise
- Numbers zero to nine as words; 10 and above as figures
- Straight quotation marks
- Required spellings: *email*, *all right*, *okay*, *y'all*, *TV*, *US*,
  *UK* — not *e-mail*, *alright*, *O.K.*, *ya'll*, *T.V.*, *U.S.*, *U.K.*
- Never an ellipsis (`...`), for any reason, in any job

> There is a second standard, **strict verbatim**, used for legal work,
> where every one of those corrections is itself a mistake — the record must
> be word-for-word. **Do not switch standards on your own judgement.** Your
> administrator sets it.

### The required accuracy check

Before you can submit, there is one more step — a card labeled **Accuracy
check**, between the speaker list and the transcript.

1. Click **Copy accuracy check**. It copies a prompt together with the
   transcript, ready to paste into your own AI assistant.
2. Paste it in, read what comes back, and check anything it flags against
   the audio through the normal editing above — it has never heard this
   recording, so treat what it says as leads, not verdicts.
3. Paste what it found into the box under **What your assistant found**,
   and press **Save accuracy check**.

**As soon as you paste it, a checklist appears below the box** — one card
per correction, parsed straight out of the same text you just pasted,
nothing extra to copy anywhere. Each card gets two buttons: **Find it**
jumps the transcript straight to that phrase (same as searching for it
by hand), and **Apply** — once you've listened and agree — corrects the
segment in one click instead of retyping it. A card marked **✓ Applied**
was already handled, even if a later check re-flags the same thing; one
that says **Not found in the transcript** most likely means the wording
already changed since that report was written — nothing broken, just
nothing left to do there.

> **This is required, not optional.** Submit stays blocked — a critical
> finding reading *"Copy the accuracy-check prompt, run it through your own
> assistant, and paste back what it found before submitting"* — until this
> is saved once per task. It comes after every editing tool above and
> before Preview: one more pass, not a replacement for listening to the
> audio yourself.

What it actually buys: a hand-maintained rule only catches what this
platform has already been taught to look for. Your own assistant, reading
the real subject matter of *this* recording, catches the specific name or
term nobody wrote a rule for yet.

---

## 7. Translation tasks

**You are not expected to speak French.** Your job is to run the polish
step, check what the tool flags, and submit.

There are no speaker labels and no timestamps on a translation — the
deliverable is prose. That is correct, not a bug.

### The steps

1. **Open the task.** The French draft is already there.
2. **Play the audio and follow along.** The English and French sit side by
   side and the current line is highlighted. You are listening for one
   thing: does the English match what you hear?
3. **Press `Copy prompt + translation`** (or `Copy a check instead` if the
   draft already looks fine — see below). One click copies the whole
   thing — the prompt, the English source and the French draft, all
   correctly ordered — nothing to open, find, or assemble by hand.
4. **Paste it into your own assistant** (Gemini or similar).
5. **Copy the whole reply — do not bother selecting part of it — and
   paste it into the Polished French box, then save.** The platform
   pulls the actual translation out on its own, whether or not the reply
   still has the markers, a `CLEAN` verdict, or a `VERIFY AGAINST AUDIO:`
   line attached to it. If there was something to check against audio,
   it shows up on screen right after you save (see below).
6. **Let the check run.** It will flag problems.
7. **Fix what is flagged**, then preview and submit.

`Copy prompt + translation` rewrites the French, against Eagle's rules and
against the actual English — it now sees both, so it can restore a
dropped sentence or a wrong term, not just fix fluency. `Copy a check
instead` never rewrites; it only reports problems (or replies CLEAN, and
hands back the draft unchanged either way, ready to save). Use check when
you already suspect the draft is fine and just want a second opinion; use
polish when you know it needs real work.

### `VERIFY AGAINST AUDIO:` — read this after you save

Sometimes your assistant's reply ends with a line reading exactly
`VERIFY AGAINST AUDIO:`, followed by one or more specific spots. You do
not need to do anything with that line yourself — paste the whole reply
as usual in step 5 above, and it shows up on screen right after you
save, separately from the document that got saved. It means your
assistant found a name or detail it could only get to a plausible
research match on, not a certain one, and is handing the last step back
to you: the one thing research can never settle is what the recording
actually contains, and you're the one who can listen.

This exists because of a real, live disagreement: a garbled "Alino...
JoJo Scanlon" got correctly researched to Samuel A. Alito Jr. and
Diarmuid F. O'Scannlain, but two different assistants disagreed on
whether the "Jr." and the exact circuit number belonged in the
translation or not — and neither had heard the audio, so neither could
actually say. Listen to the specific spot named, use what you actually
hear, and save that — not either assistant's guess.

**You cannot get the paste itself wrong by pasting too much.** A
CLEAN verdict, the markers, the VERIFY AGAINST AUDIO footer — none of
it needs removing by hand, and none of it ends up in what gets
submitted; the platform pulls the real translation out of whatever you
paste. If something in a very unusual reply still slips through anyway,
the platform's own check catches it and blocks submission before it
reaches Eagle rather than letting it through silently.

### When the ENGLISH is wrong

This is the case that catches people out. If you hear the speaker say
"Kuwait" and the English line says "Kuwara", the French will have inherited
that mistake — it will confidently say the wrong thing. **Polishing the
French cannot fix it**, even now that your assistant sees the English too
— it corrects the French to match what the source says, not what the
speaker actually said, and the source itself is what's wrong here.

The repair has to happen upstream:

1. Click the line to hear it again and be sure.
2. Hover the English line and click **edit**. Fix it. Save.
3. You will see: *"English updated. The French has NOT changed."* That is
   the important part — **fixing the English does not re-translate anything.**
4. Copy the corrected English, translate it again in your assistant, and
   paste the new French into the polished box.
5. Preview and submit.

**Only do this when the meaning is wrong** — a wrong name, a wrong number, a
wrong place. A small wording slip in the English that still produced correct
French is not worth the minutes.

### Splitting a long one

A 20-minute task is about 3,900 words of English — and since
`Copy prompt + translation` and `Copy a check instead` both now paste the
English and French together, the actual paste is roughly double that.
That size alone is not the risk — it is well within what any current
assistant accepts. What can still happen on a long task is the *reply*
stalling or trailing off. If that happens, split the text in half:

- Split **at a paragraph break**, never mid-sentence
- No overlap, no gap — the second half starts exactly where the first ended
- Join the two results with a blank line between them
- Check any important term reads the same in both halves

### What the checks catch — and why you can trust them without French

These are mechanical. They do not need anyone to read French:

| Flag | What happened |
|---|---|
| **Numbers do not match** | A figure in the French is not in the English. **Blocks submission.** |
| **Corrupted text** | Accented letters garbled by a broken copy-paste (é → Ã©). **Also blocks submission** — invisible to every other check, and often to you too if you don't read French. |
| English text remaining | Something was not translated |
| A URL, email or identifier changed | These are never translated; the French must match the English exactly |
| A web address not in the source | Your assistant supplied one that sounds plausible but the speaker never said |
| Length anomaly | A paragraph was dropped, or padded out |
| Repeated paragraph | The assistant looped |
| Commentary detected | A "Here is the corrected version" line got pasted in |
| Decimal point | `3.14` should be `3,14` — a French reader reads a point as a thousands separator, so this changes the number |
| Straight quotes / apostrophes | Should be `« … »` and `’`, not `"..."` or `'` |
| `50%` | French writes `50 %` |
| Spellchecker damage | An English spellchecker altered a French word (`dont` → `don't`) |
| tu/vous mixed | The document addresses its audience both ways partway through |

**Numbers is the serious one.** It is the only check that blocks outright,
and it is what an assistant most often breaks — turning "one" into "1", or
rewriting a date. Check the flagged number against the English and fix it.

---

## 8. Preview and submit

**Always press "Preview submission" first.** It shows the exact document
Eagle will receive — rendered by the server, not a guess.

At the top of the preview you get a plain verdict:

- **"Nothing here blocks submission"** (grey) — the notes below are worth a
  glance, not worth stalling for. Work with findings at these levels has
  been accepted before.
- **"N findings block submission"** (red) — Submit is disabled. Fix them;
  there is no override.
- **An error instead of a document** — the task is not ready. Usually the
  translation did not finish.

You can press **Submit from anywhere** — you do not need to click "Move to
review" or "Mark ready" first. Those buttons exist for organising your own
work; Submit does the whole thing.

When it works you will see a green tick and **"Submitted to the platform"**,
with a note that review takes up to about 32 hours. That message is your
receipt.

**If you see it, the work is delivered. Do not submit again.** The task
moves to Approved or Rejected on its own — you do not wait on the screen.
Press **Take another task** and start the next one; your slot is already
free.

---

## 9. Do's and don'ts

### Do

- **Listen before claiming.** Every time
- **Prefer "Ready now"** tasks
- **Preview before submitting.** Every time
- **Release** anything you cannot finish
- **Submit on time.** Good-enough submitted beats perfect late
- **Fix the specific thing flagged**, then re-check
- **Run the accuracy check on transcription tasks.** Copy → your own
  assistant → paste back → save — it is required, not optional; Submit
  stays blocked without it
- **Tell your administrator** about a task that will not process, an
  audio that will not play, or minutes running out

### Don't

- **Don't claim without listening.** You cannot undo it
- **Don't hold three tasks you are not working on.** Release them
- **Don't use names as speaker labels.** `Male_1` only
- **Don't switch transcription standards** on your own judgement
- **Don't paste a "Here is the corrected version" line** into the platform
- **Don't submit around a finding.** If it is flagged, fix it
- **Don't split a translation mid-sentence**
- **Don't edit the header, the word count, or the end-of-recording line.**
  The platform generates those and will regenerate them anyway
- **Don't share your login.** Every action is recorded against your account

---

## 10. When something goes wrong

| Symptom | What it means | What to do |
|---|---|---|
| "Not found" on a task page | You do not have access, or it was retired | Go back to the queue |
| Audio will not play | Not downloaded yet | Wait, or pick another task |
| "Being prepared" | Machine has not finished | You can still listen; check back |
| Task vanished from the queue | Someone else took it — on our side or on Eagle | Normal. Pick another |
| Claim says not enough minutes | Account balance too low | Tell your administrator |
| Preview shows an error | Not ready to submit | Read the message; usually the translation did not finish |
| "self-check-not-completed", Submit blocked | The accuracy/polish check has not been saved yet | Copy the prompt into your own assistant, paste back what it found, and save it |
| Deadline nearly up | — | Submit what you have, now |

A task disappearing is normal and constant. Eagle's pool is shared with
workers outside our team, and the queue updates itself as they claim things.

---

## 11. What is recorded

Every action — claim, release, edit, submit — is recorded against your
account with a timestamp. Your administrator can see which worker handled
which task ID and what they did.

This is ordinary accountability, and it protects you too: when a submission
is queried, the record shows exactly what happened and when.

---

## Quick reference

```
Listen  →  Claim  →  Review  →  Preview  →  Submit
   ↑         ↑                     ↑          ↑
 always   starts the           always     frees your
          1-hour clock                       slot
```

- **3 tasks** at once, maximum
- **1 hour** from claim to submission
- **Male_1 / Female_2** — the only speaker labels
- **Preview** before every submit
- **Release** rather than miss a deadline
- **Fixing the English does not re-translate** — you must paste new French

### Keys

```
Space  play / pause          ,  .   previous / next turn
←  →   5 seconds             Home   back to the start
[  ]   slower / faster       /      search the queue
```
