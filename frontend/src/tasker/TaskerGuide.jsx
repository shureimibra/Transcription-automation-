import React, { useState } from 'react';
import { Button } from '../components/ui/index.jsx';

/**
 * The tasker's guide: how to get the most out of a prepared draft.
 *
 * ADAPTED FROM THE WORKERS' GUIDES (worker/TranscriptionGuideModal.jsx and
 * TranslationGuideModal.jsx), which are built from real-submission reviews.
 * The quality advice is the same -- speakers, misheard names, numbers, the
 * accuracy check, polishing a translation. What is left out is everything
 * about how our own operation runs (claiming, minutes, deadlines, submitting
 * from here, administrators), which is not the tasker's business and does
 * not apply to them.
 */
export function TranscriptionGuide({ onClose }) {
  return <Guide title="Transcription guide" sections={TRANSCRIPTION} onClose={onClose} />;
}

export function TranslationGuide({ onClose }) {
  return <Guide title="Translation guide" sections={TRANSLATION} onClose={onClose} />;
}

function Guide({ title, sections, onClose }) {
  const [open, setOpen] = useState(sections[0].id);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-labelledby="tasker-guide-title"
           className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel">
        <div className="border-b border-edge px-5 py-4">
          <h2 id="tasker-guide-title" className="text-sm font-semibold text-ink">{title}</h2>
          <p className="mt-0.5 text-2xs text-ink-mute">
            Every step, and the mistakes that most often get work sent back.
          </p>
        </div>
        <nav aria-label="Sections" className="flex flex-wrap gap-1.5 border-b border-edge px-5 py-3">
          {sections.map((s) => (
            <button key={s.id} type="button" onClick={() => setOpen(s.id)}
                    className={`rounded-lg px-2.5 py-1 text-2xs font-medium transition-colors ${
                      open === s.id ? 'bg-accent-soft text-accent-ink' : 'bg-overlay text-ink-dim hover:text-ink'}`}>
              {s.label}
            </button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {sections.map((s) => (
            <section key={s.id} className="border-b border-edge/60 py-3 last:border-b-0">
              <button type="button" onClick={() => setOpen(open === s.id ? '' : s.id)}
                      aria-expanded={open === s.id}
                      className="flex w-full items-center justify-between gap-2 text-left">
                <h3 className="text-xs font-semibold text-ink">{s.label}</h3>
                <span aria-hidden="true" className="text-ink-mute">{open === s.id ? '−' : '+'}</span>
              </button>
              {open === s.id && (
                <div className="mt-2 space-y-2.5 text-xs leading-relaxed text-ink-dim">{s.body}</div>
              )}
            </section>
          ))}
        </div>
        <div className="flex justify-end border-t border-edge px-5 py-3">
          <Button variant="primary" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

const B = ({ children }) => <strong className="text-ink">{children}</strong>;
const K = ({ children }) => <span className="rounded-md bg-overlay px-1.5 py-0.5 font-medium text-ink">{children}</span>;
const Ol = ({ children }) => <ol className="list-decimal space-y-1.5 pl-5">{children}</ol>;
const Ul = ({ children }) => <ul className="list-disc space-y-1.5 pl-5">{children}</ul>;

const TRANSCRIPTION = [
  {
    id: 'steps', label: 'Step by step',
    body: (
      <Ol>
        <li>Check the standard banner at the top: <em>edited verbatim</em> (fillers and false starts removed) or <em>strict verbatim</em> (everything kept exactly as spoken). Applying the wrong one makes every line wrong.</li>
        <li>Press play and follow along. The line being spoken is highlighted and the list scrolls with it. Click any line to jump the audio there.</li>
        <li>Fix speakers first — see <em>Speakers</em>. It is the most graded part.</li>
        <li>Press <K>Edit</K> on a line to fix its words. Fix what is actually wrong — names, terms, numbers — not wording that is already right.</li>
        <li>Run the accuracy check (see its section) to catch names and terms you might miss.</li>
        <li>Press <K>Get final draft</K>, replace <K>[Your name]</K> with your name, and <K>Copy final draft</K>. Paste it where you submit your work.</li>
      </Ol>
    ),
  },
  {
    id: 'speakers', label: 'Speakers',
    body: (
      <Ul>
        <li><B>The person is right, the label reads wrong</B> (Female_1 should be Male_1 everywhere): click the speaker chip and rename it. Every line changes at once.</li>
        <li><B>Only the gender is wrong</B>: press the chip’s <K>→ Male</K> / <K>→ Female</K>. The number stays, so two people are never merged. A <B>?</B> on a chip means the machine was unsure — listen for five seconds.</li>
        <li><B>One turn belongs to someone else</B>: change that line’s speaker dropdown. Only that line changes.</li>
        <li><B>Two people in one line</B>: <K>Edit</K> the line, then <K>Split turn…</K>, click where the second person starts, and pick who they are.</li>
        <li><B>A speaker the machine missed entirely</B>: <K>+ Add</K> under the speakers, then pick it from any line’s dropdown.</li>
      </Ul>
    ),
  },
  {
    id: 'check', label: 'The accuracy check',
    body: (
      <>
        <p>Press <K>Copy accuracy check</K>. It copies a ready-made prompt together with your transcript. Paste it into your own AI assistant (Gemini, ChatGPT or similar) and paste the reply back into the box.</p>
        <p>A checklist appears under the box: <K>Find it</K> jumps to the phrase, <K>Apply</K> corrects the line in one click. <B>Listen before you apply</B> — the assistant has never heard the audio, so treat what it says as leads, not answers.</p>
        <p>It checks what is <B>saved</B>. Save any line you are editing before you copy the check.</p>
      </>
    ),
  },
  {
    id: 'names', label: 'Names and technical terms',
    body: (
      <>
        <p>This is where most real mistakes are: a person’s name, a book, a place or a technical word misheard as a different word. Real examples: “Milner” for Milnor, “Durac” for Dirac, “yeast and fills” for eosinophils.</p>
        <Ol>
          <li>Click the line to hear it again.</li>
          <li>Fix it and save.</li>
          <li>Press <K>⌕ Search</K> and look for the same word everywhere else — once it goes wrong, it usually goes wrong more than once.</li>
        </Ol>
      </>
    ),
  },
  {
    id: 'rules', label: 'Numbers and style',
    body: (
      <Ul>
        <li><B>Numbers</B>: zero to nine as words, 10 and up as figures — but money, times, dates, percentages, measurements (“2 years”, “5 km”), ages and labels (“Rule 8”, “step 1”) are always figures.</li>
        <li><B>Contractions</B> stay exactly as spoken on every job.</li>
        <li><B>Informal words</B> (“gonna”, “kinda”) are standardised under edited verbatim and kept under strict verbatim. “Yeah” is always correct.</li>
        <li><B>No exclamation marks</B>, and no comma after a sentence-opening “So”.</li>
        <li><B>Tags</B>: only the agreed set — [inaudible], [crosstalk], [laughter], [foreign], [background] and the others in the <K>+ Annotate</K> list.</li>
        <li><B>“No findings” is not the same as correct.</B> The checks catch format, not whether a name was heard right. Your ears do that.</li>
      </Ul>
    ),
  },
];

const TRANSLATION = [
  {
    id: 'steps', label: 'Step by step',
    body: (
      <Ol>
        <li>Listen to the audio and skim the English beside the French. You do not need to read French to do this well.</li>
        <li>Press <K>Copy prompt + translation</K>, paste it into your own AI assistant, and paste its reply into <B>Polished French</B>. Save.</li>
        <li>Fix anything flagged after saving — numbers that changed, English left untranslated, names.</li>
        <li>Press <K>Get final draft</K>, replace <K>[Your name]</K> with your name, and <K>Copy final draft</K>.</li>
      </Ol>
    ),
  },
  {
    id: 'clean', label: 'The draft already looks right',
    body: <p>Use <K>Copy a check instead</K>. It asks your assistant to compare the French with the English and answer <B>CLEAN</B> if nothing is wrong — it reports instead of rewriting, so a good draft is not changed for the sake of it.</p>,
  },
  {
    id: 'nodraft', label: 'There is no French yet',
    body: <p>If the French is not ready, press <K>Copy prompt + English</K>, let your assistant translate it, and paste the French back. Long document? Split it at a paragraph break, never mid-sentence, and paste the halves back together.</p>,
  },
  {
    id: 'english', label: 'The English itself is wrong',
    body: (
      <>
        <p>If the English line has a misheard name or number, the French inherits the mistake. Press <K>Edit</K> on that English line, correct it, and save.</p>
        <p>The French does not change on its own. Press <K>Retranslate this line</K> to get a fresh French line, then copy it into your polished text where it belongs.</p>
      </>
    ),
  },
  {
    id: 'verify', label: 'VERIFY AGAINST AUDIO',
    body: <p>If your assistant ends its reply with spots to verify against the audio, they appear as a list after you save. Each one is something it could not be sure of — listen to that moment before you finish.</p>,
  },
  {
    id: 'misses', label: 'What the checks still miss',
    body: (
      <Ul>
        <li>The checks compare numbers, catch untranslated English and spot missing paragraphs. They cannot tell whether a sentence <B>means</B> the same thing.</li>
        <li>Names and technical terms deserve a second look in both languages.</li>
        <li>Keep your assistant’s extra comments (“Here is the translation…”) out of the pasted text.</li>
      </Ul>
    ),
  },
];
