import React, { useState } from 'react';
import { Button } from '../components/ui/index.jsx';

/**
 * The complete transcription-job walkthrough, on the screen where the job
 * actually happens -- the transcription counterpart to
 * TranslationGuideModal.jsx, held back until a comparable review of
 * transcription submissions actually existed to write it from.
 *
 * IT NOW EXISTS BECAUSE THAT REVIEW HAPPENED -- TWICE. See
 * docs/TRANSCRIPTION_QUALITY_FINDINGS_2026-09-12.md (19 documents) and the
 * second round that followed it (15 more, whose fixes are what
 * ReviewWorkspace's "Copy accuracy check" button and the number/contraction/
 * repetition rules in transcription.py now reflect). Every scenario below
 * traces to something one of those documents actually did, the same
 * evidence-over-invention rule the translation guide and the worker prompts
 * both already follow.
 *
 * THE HEADLINE FINDING FROM BOTH ROUNDS, AND WHY IT SHAPES THIS GUIDE'S
 * STRUCTURE: speaker-LABEL format (Male_N / Female_N) is essentially never
 * wrong -- a dedicated CRITICAL check catches it, and 19/19 documents in
 * the first round were clean on it. What the checks cannot see at all is
 * whether a NAME or TECHNICAL TERM was heard correctly, and that is where
 * nearly every real, consequential defect in both rounds actually was: a
 * Fields Medalist's name, the literal subject of one talk wrong for the
 * first 40% of the document, the same term misspelled two different wrong
 * ways in one document. So the "what the checks still miss" section here
 * carries the same weight it does in the translation guide, for the same
 * reason -- it is the one thing a worker has to catch by ear, because
 * nothing else will.
 */
export default function TranscriptionGuideModal({ onClose }) {
  const [open, setOpen] = useState('overview');
  const toggle = (id) => setOpen((current) => (current === id ? '' : id));

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="xguide-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <div className="border-b border-edge px-5 py-4">
          <h2 id="xguide-title" className="text-sm font-semibold text-ink">
            Transcription job guide
          </h2>
          <p className="mt-0.5 text-2xs text-ink-mute">
            Every step, and every scenario that does not fit the happy path --
            drawn from two real-submission reviews, not guessed.
          </p>
        </div>

        <nav aria-label="Sections" className="flex flex-wrap gap-1.5 border-b border-edge px-5 py-3">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setOpen(s.id);
                document.getElementById(`xguide-${s.id}`)?.scrollIntoView({ block: 'start' });
              }}
              className="rounded-lg bg-overlay px-2.5 py-1 text-2xs font-medium text-ink-dim
                         transition-colors hover:bg-overlay/70 hover:text-ink"
            >
              {s.label}
            </button>
          ))}
        </nav>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {SECTIONS.map((s) => (
            <Section
              key={s.id}
              id={s.id}
              title={s.label}
              expanded={open === s.id}
              onToggle={() => toggle(s.id)}
            >
              {s.body}
            </Section>
          ))}

          <p className="mt-6 border-t border-edge pt-4 text-2xs text-ink-mute">
            This screen covers transcription tasks only. For everything the
            platform does — claiming, the one-hour clock, minutes, releasing
            a task — see <code className="text-ink">docs/TASKER_GUIDE.md</code>.
            For the two real-submission reviews this guide is built from, see{' '}
            <code className="text-ink">docs/TRANSCRIPTION_QUALITY_FINDINGS_2026-09-12.md</code>.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-edge px-5 py-3">
          <Button variant="primary" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

function Section({ id, title, expanded, onToggle, children }) {
  return (
    <section id={`xguide-${id}`} className="border-b border-edge/60 py-3 last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        <span aria-hidden="true" className="text-ink-mute">{expanded ? '−' : '+'}</span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2.5 text-xs leading-relaxed text-ink-dim">
          {children}
        </div>
      )}
    </section>
  );
}

function P({ children }) {
  return <p>{children}</p>;
}

function Ol({ children }) {
  return <ol className="list-decimal space-y-1.5 pl-5">{children}</ol>;
}

function Ul({ children }) {
  return <ul className="list-disc space-y-1.5 pl-5">{children}</ul>;
}

function Btn({ children }) {
  return (
    <span className="rounded-md bg-overlay px-1.5 py-0.5 font-medium text-ink">
      {children}
    </span>
  );
}

const SECTIONS = [
  {
    id: 'overview',
    label: 'Overview',
    body: (
      <>
        <P>
          A transcription task always has a transcript waiting for you — the
          warm pool transcribes every task before anyone can claim it, so
          your hour is spent reviewing and correcting, not waiting on a
          machine.{' '}
          <strong className="text-ink">The graded work is speaker
          identification and getting the words right</strong> — not prose
          style. Across a 19-document real review, speaker-label FORMAT
          (<code>Male_1</code>, <code>Female_2</code>) was correct in every
          single one; what actually goes wrong is a misheard name or
          technical term, or a speaker boundary the machine drew in the
          wrong place.
        </P>
        <P>
          Everything below assumes you have already claimed the task and are
          looking at its screen. If you have not claimed anything yet, see{' '}
          <code>docs/TASKER_GUIDE.md</code> for listening before claiming,
          the one-hour clock, and how many tasks you may hold at once — none
          of that is repeated here.
        </P>
      </>
    ),
  },
  {
    id: 'happy-path',
    label: 'The normal path, step by step',
    body: (
      <Ol>
        <li>
          Open the task. Check the standard banner near the top first — it
          says whether this transcript is <em>edited verbatim</em> (fillers
          and false starts removed, slang standardized) or <em>strict
          verbatim</em> (everything kept exactly as spoken), and whether the
          platform actually told us which one or we are only assuming. An
          assumed standard is shown as an alert, not a quiet label — read
          it, because applying the wrong standard makes a document wrong in
          every line, not just a few. Contractions are kept exactly as
          spoken either way — that no longer differs between the two.
        </li>
        <li>
          Press play and follow along. The turn being spoken is highlighted
          and the list scrolls itself. You are listening for two things at
          once: does the text match what is actually said, and does the
          speaker label match who is actually talking?
        </li>
        <li>
          Fix speaker labels first (see <em>“Speakers, in priority
          order”</em> below) — this is the graded work, and it is the
          cheapest thing to get right early rather than after you have
          already reviewed every line.
        </li>
        <li>
          Click any line to edit its text. Fix what is actually wrong —
          names, technical terms, numbers — rather than rewording things
          that are already correct.
        </li>
        <li>
          If you are not sure whether a name or term is right, use{' '}
          <Btn>Copy accuracy check</Btn> (see its own section below) before
          you guess.
        </li>
        <li>
          Press <Btn>Preview submission</Btn>. This is the exact document
          Eagle will receive, rendered by the server — not a guess. Fix
          anything flagged (see <em>“Reading what the checks flag”</em>).
        </li>
        <li>
          If nothing blocks it, press <Btn>Submit to platform</Btn>. You will
          see a confirmation — that is your receipt. Do not submit again.
        </li>
      </Ol>
    ),
  },
  {
    id: 'speakers',
    label: 'Speakers, in priority order',
    body: (
      <>
        <P>
          The <strong className="text-ink">Speakers</strong> card lists every
          distinct speaker with how much each one talks. Two different tools
          for two different problems — using the wrong one for the job
          either does nothing or merges two people into one:
        </P>
        <Ul>
          <li>
            <strong className="text-ink">A speaker's identity is right, only
            the label reads wrong</strong> (Female_1 should read Male_1
            throughout) — click the chip and rename it. This renames the
            speaker EVERYWHERE at once, not just the line you clicked from.
          </li>
          <li>
            <strong className="text-ink">Only the gender guess looks
            wrong</strong> — press the chip's <Btn>→ Female</Btn> /{' '}
            <Btn>→ Male</Btn> button. This keeps the number (Male_2 becomes
            Female_2, never Female_1) because the number identifies WHICH
            person; only the gender was ever in doubt. A chip marked with a
            <strong className="text-ink"> ?</strong> means the machine itself
            was not confident — adult voices genuinely overlap in pitch, and
            one real lecture measured 178 Hz against a 165 Hz boundary and
            went out as Female_1 on all 432 lines. Five seconds of listening
            settles it.
          </li>
          <li>
            <strong className="text-ink">One specific turn was attributed to
            the wrong person</strong> (diarization drew the boundary in the
            wrong place, not just guessed the wrong gender) — use the
            per-line dropdown on that turn instead. This is a single-turn
            fix; it does not touch any other line.
          </li>
        </Ul>
        <P>
          One real document showed a case worth listening for specifically:
          one label produced only short transitional lines ("I am trying to
          generate it...") while a second label always immediately continued
          with the substantive explanation, with no real back-and-forth
          between them anywhere — the shape of one continuous speaker split
          across two labels, not two people genuinely talking. Nothing in
          the text alone can catch this; it is exactly the kind of thing
          worth a few seconds of audio to confirm.
        </P>
      </>
    ),
  },
  {
    id: 'check',
    label: 'Using Copy accuracy check',
    body: (
      <>
        <P>
          Press <Btn>Copy accuracy check</Btn> on the Transcript card to copy
          a ready-to-paste prompt, together with the transcript itself, for
          your own assistant (Gemini or similar). Paste the result back here
          and read it. This is deliberately{' '}
          <strong className="text-ink">report-only — there is no button that
          rewrites the document for you</strong>, unlike the translation
          side's polish step. The reason is structural, not a missing
          feature: your assistant has never heard this audio. A guess at a
          name from it is a lead worth going to verify against the real
          recording, never a fact to paste over the transcript unheard. If
          you ever see a version of this offering to hand back a corrected
          document instead of a report, do not trust it the same way —
          confirm anything it says by listening first.
        </P>
        <P>
          The prompt already knows this document's verbatim standard and
          adjusts itself — it will tell your assistant to flag informal
          words like "gonna" on an edited-verbatim task and explicitly NOT
          to on a strict-verbatim one, so you do not have to remember which
          way the rule runs. Contractions are never flagged either way —
          Eagle's guidelines keep them exactly as spoken on every job.
        </P>
        <P>
          <strong className="text-ink">It copies whatever is currently
          saved</strong>, not whatever is sitting unsaved in a textarea. If
          you have just typed a correction into a line and not yet pressed{' '}
          <Btn>Save</Btn>, the check will review the OLD text — save first,
          then check, if you want it looking at your latest edit.
        </P>
      </>
    ),
  },
  {
    id: 'wrong-word',
    label: 'Scenario: a name or technical term looks wrong',
    body: (
      <>
        <P>
          This is the single most consequential thing either review found —
          more consequential than on the translation side, because a
          transcript's whole authority rests on correctly capturing what a
          named expert actually said. Real, confirmed examples from the
          reviews: real scholars' names mangled (Milnor → "Milner", Dirac →
          "Durac", de Broglie → "De Bruy"), a real term spelled two different
          wrong ways in the same document (Sierpinski → "Pinsky" AND
          "Spinsky"), and the literal subject of one talk transcribed as the
          wrong word for the first 40% of the document before correcting
          itself.
        </P>
        <Ol>
          <li>Click the line to hear that moment again and be sure.</li>
          <li>Click the line's text to edit it, correct it, and save.</li>
          <li>
            <strong className="text-ink">Search the rest of the document for
            the same name or term.</strong> These repeat once they start
            going wrong — one real document had the same technical term
            transcribed correctly a dozen times and wrong once, in a totally
            different, unrelated spot; another had it wrong every single
            time with nothing nearby to contrast against, which reads as
            confident and correct even though it never was.
          </li>
        </Ol>
        <P>
          One further thing worth knowing, not a scenario to act on: two
          documents in the review that literally shared one sentence — one
          ending a recording, the next beginning with the exact same words —
          transcribed that identical audio correctly in one task and
          incorrectly in the very next. There is no shared memory between
          adjacent tasks, even ones from the same original recording, so a
          name being right on one task is no guarantee it will be right on
          the next one drawn from the same source.
        </P>
      </>
    ),
  },
  {
    id: 'findings',
    label: 'Reading what the checks flag',
    body: (
      <>
        <P>
          These are mechanical, and mostly free — they run on every task
          without costing anything and without needing a model call.
        </P>
        <Ul>
          <li><strong className="text-ink">Speaker label format</strong> — the one CRITICAL check here. A label that is not <code>Male_N</code> or <code>Female_N</code> (a name, a title, "THE COURT") blocks submission outright.</li>
          <li><strong className="text-ink">Numbers</strong> — zero to nine as words, ten and up as figures, except money, a time, a date, a percentage, a measurement (including a plain duration like "2 years" or "5 kilometers"), an age, or a citation ("Rule 8", "Section 4"), which are always figures regardless of size. A scale word riding on an already-correct figure ("50 million") is not itself a defect.</li>
          <li><strong className="text-ink">Contractions</strong> — kept exactly as spoken on every job, per Eagle's General Guidelines. Never flagged for expansion, under either standard.</li>
          <li><strong className="text-ink">Informal words</strong> — "gonna", "kinda" and similar are flagged for standardizing under edited verbatim, and correctly left alone under strict verbatim. "Yeah" is never flagged — it is the required spelling, not a defect.</li>
          <li><strong className="text-ink">Repeated words</strong> — a genuine stutter, not ordinary grammar that happens to repeat a word ("had had" as past perfect, "that that" as a demonstrative following a complementizer are both correct English, not disfluencies).</li>
          <li><strong className="text-ink">Capitalization, paragraphing, quote style</strong> — edited-verbatim only; strict verbatim explicitly forbids tidying a run-on with artificial capitals.</li>
          <li><strong className="text-ink">Required spellings</strong> — email, all right, okay, yeah, y'all, TV, US, UK, exactly as Eagle's Writing Rules table names them, on every job.</li>
          <li><strong className="text-ink">Bracket tags</strong> — only the agreed set is valid, including <code>[foreign]</code> for a foreign-language stretch and <code>[background]</code> for noise with no discernible words.</li>
          <li><strong className="text-ink">Timestamp continuity</strong> — one block's end time must equal the next block's start time.</li>
        </Ul>
        <P>
          A finding names ONE example even when the real count is higher — a
          list like "2, 9, 10, 11, 13, 16" can carry several real number
          violations with only the first one shown, and a long
          punctuation-light monologue can have more than one paragraph-break
          issue with only one flagged. Treat an example as a sample of the
          problem, not the whole of it — check nearby for more of the same
          shape once you have fixed the one shown.
        </P>
      </>
    ),
  },
  {
    id: 'blind-spots',
    label: 'What the checks still miss — read this one',
    body: (
      <>
        <P>
          Across two real-submission reviews (19 documents, then 15 more),
          the deterministic checks are reliable at the mechanical rubric —
          number format, contractions, fillers, repetition — and essentially
          blind to whether the transcript is actually TRUE. Two documents in
          the review showed <em>"No findings — matches the format the
          platform has accepted"</em> while containing multiple real,
          high-confidence proper-noun errors, including a Fields Medalist's
          name.{' '}
          <strong className="text-ink">"No findings" measures compliance
          with the checks that exist, not correctness.</strong>
        </P>
        <Ol>
          <li>
            <strong className="text-ink">Listen for anything that sounds
            like a real name, book, place, or technical term you do not
            immediately recognize</strong> — especially right where someone
            is being introduced, a result is being cited, or a place or
            institution is named. That is exactly where the reviews found
            almost every confirmed error.
          </li>
          <li>
            <strong className="text-ink">If a spelling looks phonetically
            garbled</strong> rather than simply unfamiliar — "ciphate" for
            "Seifert surface," "Nietzsche's" for "Nature's" — that is the
            signature of a mishearing, not an obscure but correct word.
          </li>
          <li>
            <strong className="text-ink">Once you find one, search the whole
            document for it.</strong> A wrong spelling used inconsistently
            is the easy case; a wrong spelling used consistently throughout
            reads as confident and correct and is the harder, more dangerous
            case — nothing in the text contradicts it.
          </li>
          <li>
            <strong className="text-ink">Speaker-label FORMAT being correct
            says nothing about whether a diarization BOUNDARY is correct.</strong>{' '}
            See the "Speakers" section above for the specific shape to
            listen for.
          </li>
        </Ol>
      </>
    ),
  },
  {
    id: 'submit',
    label: 'Preview, submit, and the deadline',
    body: (
      <>
        <P>
          Always press <Btn>Preview submission</Btn> before submitting — it
          shows the exact document Eagle will receive. You can submit from
          anywhere; you do not need to move the task to any particular
          status first.
        </P>
        <P>
          Watch the clock on screen. When time is short, submit what you
          have — good-enough submitted beats perfect and late. Work not
          submitted before the deadline is not accepted, and there is no
          extension. If you cannot finish in time at all, releasing the task
          early is always better than missing the deadline with it still in
          your hands.
        </P>
        <P>
          Once you see the confirmation, the work is delivered — do not
          submit again, and you can no longer edit it. The task moves to
          Approved or Rejected on its own; you do not need to wait on the
          screen for it.
        </P>
      </>
    ),
  },
  {
    id: 'trouble',
    label: 'When something goes wrong',
    body: (
      <Ul>
        <li><strong className="text-ink">The task will not process / stays “being prepared”</strong> — the machine has not finished yet. You can still listen to the audio while you wait; check back.</li>
        <li><strong className="text-ink">Preview shows an error instead of a document</strong> — the task is not ready to submit yet. Read the message; it says why.</li>
        <li><strong className="text-ink">The clipboard copy fails</strong> — the prompt and transcript text are still shown on screen and can be selected and copied by hand.</li>
        <li><strong className="text-ink">The accuracy check seems to be reviewing an old version</strong> — you likely have an unsaved edit; press <Btn>Save</Btn> on that line first.</li>
        <li><strong className="text-ink">Minutes running low or a claim is refused for lack of minutes</strong> — this is an account-level balance, not something you caused. Tell your administrator.</li>
      </Ul>
    ),
  },
];
