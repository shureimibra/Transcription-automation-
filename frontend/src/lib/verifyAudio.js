// Turning a saved VERIFY AGAINST AUDIO note into individually findable
// items -- the translation side's counterpart to accuracyFindings.js's
// "Find it" for the transcription accuracy check.
//
// WHY THIS IS ITS OWN FILE, NOT ADDED TO accuracyFindings.js. That module
// parses a worker's ASSISTANT'S REPLY about a transcript -- several
// tolerated shapes, because a report never obeys the prompt to the
// letter. A VERIFY AGAINST AUDIO note is different: it comes from the
// SAME polish prompt this project already controls the exact wording of
// (worker_prompts.py's _POLISH), so it is asked for in exactly one shape
// and only needs to parse that one shape, not guess among several.
//
// THE SHAPE: one item per line, "<exact English phrase, quoted>" -- <why
// unsure>. The phrase is extracted as the quoted span itself, not by
// splitting the line on a dash -- a bare hyphen inside the phrase
// ("circuit-court judge") would otherwise be mistaken for the separator
// by anything that tried to split on it. Still tolerant of a stray
// backtick in place of straight quotes, the same defence
// accuracyFindings.js's stripQuotes already has, and for the same
// reason: a worker's own assistant does not always reproduce a
// template's exact punctuation even when it gets the content right.
//
// WHAT "FIND" ACTUALLY SEARCHES. Unlike a transcript, a translation's own
// French output has no timestamps at all -- it is prose, not segments
// (see Task's own docstring: "PROSE, NOT SEGMENTS"). So there is nothing
// to jump to inside the French. The ENGLISH SOURCE does have timestamped
// segments, with a working audio player and search already built in
// TranslationWorkspace.jsx -- this hands that existing search a phrase
// to look for, it does not invent a new way to search.

import { normalizeQuotes } from './accuracyFindings.js';

// The quoted phrase, wherever it sits on the line -- not anchored to the
// very start, since a reply occasionally leads with a bullet or a number.
const QUOTED_PHRASE_RE = /["“]([^"”]+)["”]|`([^`]+)`/;

// Whatever separates the phrase from the reason, if the reply used one --
// stripped from the front of whatever text follows the closing quote.
const LEADING_SEPARATOR_RE = /^(?:--|—|-)\s*/;

/**
 * `text` is exactly `outcome.verify_against_audio` as the server returns
 * it -- already stripped of the "VERIFY AGAINST AUDIO:" label itself by
 * worker_prompts.strip_reply_scaffolding, one item per line. Returns
 * `[]` for empty text or the literal word NONE, same as "nothing to
 * flag" reads on the transcription side.
 */
export function parseVerifyItems(text) {
  const raw = (text || '').trim();
  if (!raw || raw.toUpperCase() === 'NONE') return [];

  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const qm = line.match(QUOTED_PHRASE_RE);
      if (!qm) {
        // No quoted phrase anywhere on the line: kept as a note with
        // nothing to search for, rather than dropped -- a worker still
        // gets to read it, just without a Find button next to it.
        return { phrase: '', note: line };
      }
      const phrase = normalizeQuotes((qm[1] || qm[2] || '').trim());
      const rest = line.slice(qm.index + qm[0].length).trim();
      const note = rest.replace(LEADING_SEPARATOR_RE, '').trim();
      return { phrase, note: note || line };
    });
}
