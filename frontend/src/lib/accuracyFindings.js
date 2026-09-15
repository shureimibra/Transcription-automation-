// Turning a pasted accuracy-check report back into something a worker can
// act on one click at a time, instead of reading a list and doing the
// find-and-retype work by hand for every single item.
//
// WHY THIS HAS TO TOLERATE TWO SHAPES. The check prompt (worker_prompts.py's
// _TRANSCRIPT_CHECK) asks for an exact structure -- "the phrase as
// transcribed / what you think it should probably be / why, in one line" --
// and most compliant replies give it verbatim, with or without a leading
// "*". But a worker's own assistant does not always obey that structure to
// the letter: a real report seen in practice gave three bare lines per
// finding instead -- a quoted phrase, a line starting "probably", then a
// reasoning paragraph, no labels at all. Both shapes are parsed; anything
// that matches neither is simply left out of the checklist rather than
// guessed at -- the raw pasted text is still right there either way, so
// nothing is lost, only not made clickable.

const LABELLED_PHRASE_RE = /^\*?\s*(?:the\s+)?phrase as transcribed:?\s*(.+)$/i;
const LABELLED_SUGGESTION_RE =
  /^\*?\s*(?:what you think it should probably be[^:]*:|probably)\s*(.+)$/i;
const LABELLED_REASON_RE = /^\*?\s*why(?:, in one line)?:?\s*(.+)$/i;

// A line that is JUST a quoted phrase (optionally several "/"-separated
// alternatives), and nothing else -- the shape a bare, unlabelled report
// opens each finding with.
const BARE_QUOTED_LINE_RE = /^["“](.+?)["”](?:\s*\/\s*["“].+?["”])*\s*$/;
const BARE_PROBABLY_RE = /^probably\s+(.+)$/i;

function stripQuotes(text) {
  return (text || '').trim().replace(/^["“]|["”]$/g, '').trim();
}

// "SNCC" / "SNCC's" -- several alternatives offered for one phrase. There
// is no way to know which one a worker wants without asking them, so the
// first is used as the actual substitution; the full original text stays
// visible on the card either way, for a worker to see the nuance and adjust
// before confirming.
function firstAlternative(text) {
  const [first] = (text || '').split('/');
  return stripQuotes(first);
}

/**
 * Parse a pasted accuracy-check report into `{ phrase, suggestion, reason }`
 * entries, in the order they appeared. Never throws -- a report that does
 * not parse at all just yields an empty list, same as one with no findings.
 */
export function parseFindings(text) {
  const lines = (text || '').split(/\r?\n/).map((l) => l.trim());
  const findings = [];
  let pending = null;

  const flush = () => {
    if (pending && pending.phrase && pending.suggestion) {
      findings.push({
        phrase: pending.phrase,
        suggestion: pending.suggestion,
        reason: pending.reason || '',
      });
    }
    pending = null;
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line) continue;

    let m = line.match(LABELLED_PHRASE_RE);
    if (m) {
      flush();
      pending = { phrase: stripQuotes(m[1]) };
      continue;
    }
    if (pending && (m = line.match(LABELLED_SUGGESTION_RE))) {
      pending.suggestion = firstAlternative(m[1]);
      continue;
    }
    if (pending && (m = line.match(LABELLED_REASON_RE))) {
      pending.reason = m[1].trim();
      continue;
    }

    // The bare, unlabelled shape: a quoted phrase line, then a very next
    // "probably ..." line, then a reasoning paragraph up to the next blank
    // line or the next quoted-phrase line.
    if ((m = line.match(BARE_QUOTED_LINE_RE))) {
      const next = lines[i + 1] || '';
      const pm = next.match(BARE_PROBABLY_RE);
      if (pm) {
        flush();
        let j = i + 2;
        const reasonLines = [];
        while (j < lines.length && lines[j] && !BARE_QUOTED_LINE_RE.test(lines[j])) {
          reasonLines.push(lines[j]);
          j += 1;
        }
        findings.push({
          phrase: stripQuotes(m[1]),
          suggestion: firstAlternative(pm[1]),
          reason: reasonLines.join(' ').trim(),
        });
        i = j - 1;
        continue;
      }
    }
  }
  flush();

  // "not sure" is a real, correct answer from the check prompt -- not a
  // usable correction. Nothing to search for or apply, so it is left out
  // of the actionable list rather than shown as a broken card.
  return findings.filter((f) => f.suggestion.toLowerCase() !== 'not sure');
}

/** Replace the FIRST case-insensitive occurrence of `phrase` in `text`. */
export function replaceFirst(text, phrase, suggestion) {
  const source = text || '';
  const index = source.toLowerCase().indexOf((phrase || '').toLowerCase());
  if (index === -1) return source;
  return source.slice(0, index) + suggestion + source.slice(index + phrase.length);
}
