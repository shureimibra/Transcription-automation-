import React, { useState } from 'react';
import { Button } from '../components/ui/index.jsx';

/**
 * The complete translation-job walkthrough, on the screen where the job
 * actually happens.
 *
 * WHY THIS EXISTS AS A SCREEN AND NOT ONLY A FILE. The two documents this
 * content is drawn from -- docs/TASKER_GUIDE.md and
 * docs/TRANSLATION_POLISH_PROMPT.md -- already say most of this, but "read a
 * file in a repository" was the exact failure worker_prompts.py's own
 * docstring already names for the prompt text itself: nobody finds a file to
 * do their job, so six people end up with six different understandings.
 * Putting the full procedure one click away from the task list closes the
 * same gap for the WORKFLOW that worker_prompts.py already closed for the
 * PROMPT.
 *
 * WHY IT IS LONGER THAN THE IN-APP STEP LIST ON TranslationWorkspace. That
 * screen's three-step list is deliberately short -- it is what you follow
 * WHILE working. This is what you read once, and return to when something
 * does not fit the three-step happy path: a wrong English word, a document
 * too long for one pass, a finding you do not understand, a deadline running
 * out. Scenario coverage over brevity is the right trade here specifically
 * because this is opt-in reading, not a screen blocking the work.
 *
 * SECTIONS TRACE TO REAL EVIDENCE, THE SAME WAY THE PROMPTS DO. The "what the
 * checks still miss" section exists because an 11-real-document review
 * found defects (see docs/TRANSLATION_QUALITY_FINDINGS_2026-09-12.md) that
 * no automated check catches even after this session's fixes -- a worker who
 * only trusts "no findings" would still miss them. Telling them what to
 * look for by hand is the only remaining safety net for that specific gap.
 *
 * TRANSCRIPTION IS DELIBERATELY OUT OF SCOPE HERE -- it has its own guide
 * now, TranscriptionGuideModal.jsx, written the same way once a comparable
 * review of transcription submissions existed to write it from. Keeping
 * this one translation-only is not a placeholder any more; it is the same
 * "read the job you are actually doing" design both guides share.
 */
export default function TranslationGuideModal({ onClose }) {
  const [open, setOpen] = useState('overview');
  const toggle = (id) => setOpen((current) => (current === id ? '' : id));

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="tguide-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <div className="border-b border-edge px-5 py-4">
          <h2 id="tguide-title" className="text-sm font-semibold text-ink">
            Translation job guide
          </h2>
          <p className="mt-0.5 text-2xs text-ink-mute">
            Every step, and every scenario that does not fit the three-step
            happy path. Nothing here requires reading French.
          </p>
        </div>

        <nav aria-label="Sections" className="flex flex-wrap gap-1.5 border-b border-edge px-5 py-3">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setOpen(s.id);
                document.getElementById(`tguide-${s.id}`)?.scrollIntoView({ block: 'start' });
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
            This screen covers translation tasks only. For everything the
            platform does — claiming, the one-hour clock, minutes, releasing
            a task — see <code className="text-ink">docs/TASKER_GUIDE.md</code>.
            For the exact wording of the assistant prompts and why each rule
            in them exists, see{' '}
            <code className="text-ink">docs/TRANSLATION_POLISH_PROMPT.md</code>.
            Working a transcription task instead? Press{' '}
            <strong className="text-ink">Full guide</strong> on that screen —
            it has its own walkthrough, built the same way this one was.
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
    <section id={`tguide-${id}`} className="border-b border-edge/60 py-3 last:border-b-0">
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
          A translation task always has a machine draft waiting for you — the
          platform translates every task before anyone can claim it.{' '}
          <strong className="text-ink">You are not expected to speak or
          read French.</strong> Your job is to run the polish step, act on
          what is flagged, and submit. There are no speaker labels and no
          timestamps in the deliverable — that is correct, not a bug.
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
          Open the task. The English source and the French machine draft sit
          side by side, with the source read-only — it is an input, editing
          it does not change what gets submitted (see{' '}
          <em>“When the English itself is wrong”</em> below for the one case
          where you do edit it).
        </li>
        <li>
          Press play and follow along. The current line highlights and the
          list scrolls itself — you do not need to keep your place by hand.
          You are listening for one thing only: does the English match what
          is actually said?
        </li>
        <li>
          Press <Btn>Copy prompt + translation</Btn>. This copies the review
          prompt AND the French together, ready to paste — you do not need a
          prompt of your own, and you should not write one.
        </li>
        <li>Paste it into your own assistant (Gemini or similar).</li>
        <li>
          Paste what comes back into the <em>Polished French</em> box and
          press <Btn>Save polished version</Btn>. Paste the text only — if
          your assistant added a line like “Here is the corrected version”,
          leave it out. That exact sentence is what the commentary check
          looks for.
        </li>
        <li>
          Read what gets flagged. Fix anything shown (see{' '}
          <em>“Reading what the checks flag”</em> for what each one means).
        </li>
        <li>
          Press <Btn>Preview submission</Btn>. This is the exact document
          Eagle will receive, rendered by the server — not a guess.
        </li>
        <li>
          If nothing blocks it, press <Btn>Submit to platform</Btn>. You will
          see a confirmation — that is your receipt. Do not submit again.
        </li>
      </Ol>
    ),
  },
  {
    id: 'no-draft',
    label: 'Scenario: there is no machine draft yet',
    body: (
      <>
        <P>
          The screen says <em>“Translate this task”</em> instead of{' '}
          <em>“Polish the translation”</em>. This happens when the machine
          translation has not finished or was never requested. You do the
          whole translation yourself, through your own assistant:
        </P>
        <Ol>
          <li>
            Press <Btn>Copy prompt + English</Btn> — this copies a translate
            prompt together with the English source.
          </li>
          <li>
            Paste it into your assistant. If it is long, split it in half at
            a paragraph break first (see <em>“Splitting a long document”</em>
            below) — never mid-sentence.
          </li>
          <li>Paste the French result into the box below and save.</li>
          <li>Fix anything flagged, then preview and submit as normal.</li>
        </Ol>
      </>
    ),
  },
  {
    id: 'check-instead',
    label: 'Scenario: the draft already looks right',
    body: (
      <P>
        Use <Btn>Copy a check instead</Btn> rather than the polish button. It
        asks your assistant to compare the French against the English and
        report problems — it replies <Btn>CLEAN</Btn> if it finds nothing,
        rather than rewriting a translation that did not need it. Asking an
        assistant to “polish” a document that is already fine tends to
        produce changes for the sake of changes; asking it to check gives you
        a second opinion you can weigh against what the platform’s own gate
        found.
      </P>
    ),
  },
  {
    id: 'wrong-english',
    label: 'Scenario: the English itself is wrong',
    body: (
      <>
        <P>
          This is the case that catches people out. If the speaker says
          “Kuwait” and the English line reads “Kuwara”, the French inherited
          that mistake and states it just as confidently.{' '}
          <strong className="text-ink">Polishing the French cannot fix
          this</strong> — your assistant only ever sees the French and has no
          way to know the English was wrong in the first place.
        </P>
        <P>Only do this when the meaning is actually wrong — a wrong name, a
          wrong number, a wrong place. A wording slip in the English that
          still produced correct French is not worth the minutes.</P>
        <Ol>
          <li>Click the line to hear that moment again and be sure.</li>
          <li>
            Hover the English line and click <Btn>Edit</Btn>. Correct it and
            save.
          </li>
          <li>
            You will see: <em>“English updated. The French has NOT
            changed.”</em> That is the important part — correcting the
            English does not retranslate anything by itself.
          </li>
          <li>
            Press <Btn>Retranslate this line</Btn> to get a fresh French
            version of just that one line through the platform’s own AI,
            without spending your own assistant on the whole document again.
          </li>
          <li>
            Press <Btn>Copy</Btn> next to the new result and paste it into
            the polished French box, in the right place.
          </li>
          <li>
            If you would rather correct it yourself, use{' '}
            <Btn>Revert to original</Btn> any time to undo an edit back to
            what the machine first heard.
          </li>
        </Ol>
      </>
    ),
  },
  {
    id: 'splitting',
    label: 'Scenario: the document is too long for one pass',
    body: (
      <>
        <P>
          A 20-minute task is roughly 3,900 words. If your assistant
          truncates its reply or stalls, split the text in half rather than
          fighting with a single giant paste.
        </P>
        <Ul>
          <li>Split at a paragraph break — never mid-sentence.</li>
          <li>
            No overlap and no gap: the second half must start exactly where
            the first one ended.
          </li>
          <li>Join the two results back together with a blank line between them.</li>
          <li>
            Check that any name or technical term that appears in both halves
            reads the same way in both — this is exactly where terminology
            drifts across a split.
          </li>
        </Ul>
      </>
    ),
  },
  {
    id: 'findings',
    label: 'Reading what the checks flag',
    body: (
      <>
        <P>
          These are mechanical — the platform runs them without reading
          French, so you can trust what they say without reading French
          either.
        </P>
        <Ul>
          <li><strong className="text-ink">Numbers do not match</strong> — a figure in the French is not in the English, or vice versa. This is the one that <strong className="text-ink">blocks submission outright</strong>, with no override — it is also what an assistant most often breaks, by turning “one” into “1” or rewriting a date.</li>
          <li><strong className="text-ink">Corrupted text</strong> — accented letters turned into garbled sequences ("é" became "Ã©" or similar) by a broken copy-paste. <strong className="text-ink">Also blocks submission outright</strong> — invisible to every other check, and often invisible to you too if you do not read French. Copy the text again as plain text and check the accents look right before saving.</li>
          <li><strong className="text-ink">English text remaining</strong> — a run of untranslated source survived into the French.</li>
          <li><strong className="text-ink">English word or phrase left untranslated</strong> — a shorter miss than the one above: a stray word like “I’m”, “each”, or “November” sitting in an otherwise-French sentence.</li>
          <li><strong className="text-ink">A URL, email address, or identifier code changed</strong> — these are never translated, so the French copy must match the English exactly. An identifier flag can occasionally be a false alarm on an ordinary word; the other two are not.</li>
          <li><strong className="text-ink">A web address in the French the English never said</strong> — your assistant sometimes helpfully supplies the website it believes is being described. Remove anything the speaker did not actually say, however plausible it looks.</li>
          <li><strong className="text-ink">Currency written in English format</strong> — a figure like “$60,000” that should read “60 000 $” in French. The number itself is right; only the formatting is not.</li>
          <li><strong className="text-ink">A word an English spellchecker has damaged</strong> — French-specific: something like "dont" quietly autocorrected to "don't" by a checker set to English. Reads as fluent French to anyone who does not know the correct word, and a real reviewer has marked work down for exactly this.</li>
          <li><strong className="text-ink">Tu and vous mixed in one document</strong> — French, Spanish and German each have a familiar and a polite form of "you"; a document that switches partway through reads as a mistake, not a stylistic choice, even if both forms are individually correct French.</li>
          <li><strong className="text-ink">Length anomaly</strong> — a paragraph was dropped, or padded out.</li>
          <li><strong className="text-ink">Repeated paragraph</strong> — the assistant looped and produced the same text twice.</li>
          <li><strong className="text-ink">Commentary detected</strong> — a line like “Here is the corrected version” got pasted in by mistake.</li>
          <li><strong className="text-ink">Typography</strong> — a decimal point instead of a comma, straight quotes instead of « guillemets », a missing space before %. Small, but a French reader notices immediately.</li>
        </Ul>
        <P>
          A finding is not automatically a reason to stall — the preview
          screen tells you plainly whether anything actually blocks
          submission. Work with only low-severity notes has been accepted
          before. What you must never do is submit <em>around</em> a
          finding that is shown as blocking.
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
          A real review of eleven accepted-format translation submissions
          found defects that survive every automated check, even after the
          checks above were added. None of these require reading French —
          you are scanning for the presence of English, not judging French
          quality.
        </P>
        <Ol>
          <li>
            <strong className="text-ink">Scan for capitalized English words
            sitting inside French sentences.</strong> The clearest confirmed
            examples: “I’m”, “I’ll”, “It’s”, “That’s”, “They”, “All”,
            “Each”, “Anyone”, “One”, a month name, “Canadian”, “United
            States”. French capitalizes far less than English mid-sentence,
            so a capitalized word that is not a real proper noun and is not
            the start of a sentence is worth a second look.
          </li>
          <li>
            <strong className="text-ink">If you find one, search the whole
            document for it.</strong> These repeat once they start — one
            real document left the same technical term untranslated more
            than a dozen times.
          </li>
          <li>
            <strong className="text-ink">Check that an institutional or
            technical term stays the same every time it recurs</strong> — a
            court, an Act, a defined term — including a shorthand reference
            back to it later (“the Act” after “the Income Tax Act” was
            already given in full). A term that is wrong every single time,
            with nothing to contrast against, will not look inconsistent —
            it will just look confident and wrong.
          </li>
          <li>
            <strong className="text-ink">Check every dollar figure</strong>
            specifically for French format — number first, a space as the
            thousands separator, symbol last: <code>1 000 $</code>, never{' '}
            <code>$1,000</code>.
          </li>
          <li>
            <strong className="text-ink">“No findings” is not the same as
            “clean.”</strong> The automated gate checks structure, numbers,
            and the patterns above — it is not a guarantee nothing was
            missed. This five-point scan is the actual safety net for
            everything it does not cover.
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
          submit again. The task moves to Approved or Rejected on its own;
          you do not need to wait on the screen for it.
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
        <li><strong className="text-ink">Preview shows an error instead of a document</strong> — the task is not ready to submit, usually because a translation has not finished. Read the message; it says why.</li>
        <li><strong className="text-ink">An AI job is already running</strong> when you press Retranslate — the server allows only one AI job per task at a time. Wait for it to finish and try again.</li>
        <li><strong className="text-ink">Still working after a minute</strong> on a retranslate — check back in a moment rather than pressing it again.</li>
        <li><strong className="text-ink">The clipboard copy fails</strong> — the prompt text is still shown on screen and can be selected and copied by hand.</li>
        <li><strong className="text-ink">Minutes running low or a claim is refused for lack of minutes</strong> — this is an account-level balance, not something you caused. Tell your administrator.</li>
      </Ul>
    ),
  },
];
