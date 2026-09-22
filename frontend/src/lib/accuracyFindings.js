// Turning a pasted accuracy-check report back into something a worker can
// act on one click at a time, instead of reading a list and doing the
// find-and-retype work by hand for every single item.
//
// WHY THIS HAS TO TOLERATE FOUR SHAPES. The check prompt (worker_prompts.py's
// _TRANSCRIPT_CHECK) asks for an exact structure -- "the phrase as
// transcribed / what you think it should probably be / why, in one line" --
// and most compliant replies give it verbatim, with or without a leading
// "*". But a worker's own assistant does not always obey that structure to
// the letter, and it does not obey it the SAME WRONG WAY twice:
//   - one real report gave three bare lines per finding instead -- a quoted
//     phrase, a line starting "probably", then a reasoning paragraph, no
//     labels at all.
//   - another real report (same prompt, same task family, a different
//     session) gave three bulleted lines per finding with no quotes and no
//     "probably" either -- "* **phrase**" (bold marking where a finding
//     starts), "* suggestion", "* reason" -- confirmed live: this is what
//     made a worker conclude the checklist feature "wasn't there", because
//     AccuracyChecklist renders nothing at all when it parses zero findings
//     (see its own render guard), which looks identical to a missing
//     feature from the outside.
//   - a fourth real report went one step plainer still: three bulleted
//     lines with no bold marking either -- "* m * v^2 / r = q * v * B" /
//     "* Notation: ..." -- distinguishable from an ordinary bulleted list
//     only by being in groups of exactly three. Confirmed live the same day
//     notation findings shipped: the report contained real, correctly
//     marked notation findings, and the checklist -- badge included --
//     never got a chance to render a single one of them.
// All four shapes are parsed; anything that matches none of them is simply
// left out of the checklist rather than guessed at -- the raw pasted text is
// still right there either way, so nothing is lost, only not made clickable.

const LABELLED_PHRASE_RE = /^\*?\s*(?:the\s+)?phrase as transcribed:?\s*(.+)$/i;
const LABELLED_SUGGESTION_RE =
  /^\*?\s*(?:what you think it should probably be[^:]*:|probably)\s*(.+)$/i;
const LABELLED_REASON_RE = /^\*?\s*why(?:, in one line)?:?\s*(.+)$/i;

// A line that is JUST a quoted phrase (optionally several "/"-separated
// alternatives), and nothing else -- the shape a bare, unlabelled report
// opens each finding with.
const BARE_QUOTED_LINE_RE = /^["“](.+?)["”](?:\s*\/\s*["“].+?["”])*\s*$/;
const BARE_PROBABLY_RE = /^probably\s+(.+)$/i;

// The third real shape: "* **phrase**" / "* suggestion" / "* reason" --
// three consecutive bullets, the first (and only the first) bold-marked as
// where a finding begins. No quotes, no "probably", no label anywhere.
const BULLETED_BOLD_PHRASE_RE = /^\*\s+\*\*(.+?)\*\*\s*$/;
const BULLETED_PLAIN_RE = /^\*\s+(.+)$/;

// CONFIRMED LIVE, 2026-09-22: a worker's assistant started wrapping the
// phrase in backticks -- "`Lardil word`" -- markdown-code-span style,
// instead of the straight/curly quotes every report before this used. Left
// unstripped, the backticks became part of `finding.phrase` itself, so
// Find it / Apply / the "already found" check all searched the transcript
// for the literal string "`Lardil word`" (backticks included) -- which
// cannot exist in real transcript text, so EVERY finding in EVERY report
// using this shape showed "Not found in the transcript -- likely already
// fixed", correct or not.
//
// A backtick-quoted phrase can also carry trailing annotation AFTER the
// closing backtick -- a speaker-label finding wrote
// "`Male_2:` (at timestamp [00:08:02.400 --> 00:08:23.340])" -- so this
// pulls out just the backtick-delimited span itself rather than only
// trimming from the ends of the whole string, which is enough for a plain
// quoted phrase but would leave that trailing timestamp attached here.
function stripQuotes(text) {
  const trimmed = (text || '').trim();
  const backticked = trimmed.match(/^`([^`]+)`/);
  if (backticked) return backticked[1].trim();
  return trimmed.replace(/^["“]|["”]$/g, '').trim();
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

// Smart/curly quotes are routine in text copied out of a chat UI's own
// rendering -- ChatGPT and Gemini both typeset a straight ' as a curly '
// on the page, and a worker copies what they see. ASR output never does
// that. CONFIRMED LIVE: a report's "Professor Blondell's course" showed as
// "Not found in the transcript" although the transcript had that exact
// text, because the apostrophe on the clipboard was U+2019 and the one in
// the transcript was U+0027 -- two different characters that render
// identically and compare unequal. Both sides of every match go through
// this before comparing, so which one either text happened to use stops
// mattering.
function normalizeQuotes(text) {
  return (text || '').replace(/[‘’]/g, "'").replace(/[“”]/g, '"');
}

/**
 * The one phrase actually worth searching for and applying, out of a
 * `finding.phrase` that may list several alternative spellings of the SAME
 * mishearing ("X" / "Y" / "Z" -- exactly the shape rule 2 of the check
 * prompt asks for, and therefore exactly the findings most worth trusting).
 *
 * CONFIRMED LIVE: `finding.phrase` used to be handed to Find it / Apply /
 * the "already found" check UNSPLIT. No single transcript segment ever
 * contains the literal string `"X" / "Y" / "Z"` -- only X, or only Y -- so
 * every multi-spelling finding showed "Not found in the transcript --
 * likely already fixed" regardless of whether it had, and Apply, had it
 * ever reached the replace, would have silently written nothing.
 * `suggestion` already picked its own first alternative for the same
 * reason; `phrase` never did. `finding.phrase` itself is left untouched by
 * this -- the card still shows the full compound text, because seeing
 * every variant IS the evidence for a rule-2 finding -- this is only for
 * the three places that need one concrete, matchable string.
 */
export function primaryPhrase(phrase) {
  return normalizeQuotes(firstAlternative(phrase));
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

    // The bulleted-bold shape: "* **phrase**" then exactly two more plain
    // bullets, suggestion then reason. Both follow-on lines must themselves
    // be plain bullets, not the bold start of the NEXT finding -- a
    // finding whose reason line was skipped must not silently swallow the
    // next finding's phrase as its own reason.
    if ((m = line.match(BULLETED_BOLD_PHRASE_RE))) {
      const suggestionLine = lines[i + 1] || '';
      const reasonLine = lines[i + 2] || '';
      const sm = suggestionLine.match(BULLETED_PLAIN_RE);
      const rm = reasonLine.match(BULLETED_PLAIN_RE);
      if (sm && !BULLETED_BOLD_PHRASE_RE.test(suggestionLine)
          && rm && !BULLETED_BOLD_PHRASE_RE.test(reasonLine)) {
        flush();
        findings.push({
          phrase: stripQuotes(m[1]),
          suggestion: firstAlternative(sm[1]),
          reason: rm[1].trim(),
        });
        i += 2;
        continue;
      }
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

    // The fourth real shape: three consecutive plain "* " bullets with NO
    // marker at all -- no bold, no quotes, no label -- distinguished from
    // every other shape only by POSITION. CONFIRMED LIVE: a real report
    // delivering notation findings ("* m * v^2 / r = q * v * B" /
    // "* Notation: ...") used exactly this shape and parsed into nothing
    // at all -- the checklist, badge included, never got a chance to
    // render, on the same day that badge shipped. Tried last, after every
    // more specific shape above has already had a chance to claim the
    // line, since a plain "* " bullet is the least distinctive marker of
    // any shape this module recognises.
    if ((m = line.match(BULLETED_PLAIN_RE)) && !BULLETED_BOLD_PHRASE_RE.test(line)) {
      const suggestionLine = lines[i + 1] || '';
      const reasonLine = lines[i + 2] || '';
      const sm = suggestionLine.match(BULLETED_PLAIN_RE);
      const rm = reasonLine.match(BULLETED_PLAIN_RE);
      // Neither follow-on line may itself look like the START of a MORE
      // SPECIFIC shape -- labelled or bulleted-bold, both checked, not
      // just one. CONFIRMED LIVE (as a test failure, not just a worry):
      // checking only LABELLED_PHRASE_RE let this branch swallow a bold
      // "* **second**" as an ordinary suggestion line, because that
      // regex has nothing to say about bold markers at all.
      if (sm && !LABELLED_PHRASE_RE.test(suggestionLine) && !BULLETED_BOLD_PHRASE_RE.test(suggestionLine)
          && rm && !LABELLED_PHRASE_RE.test(reasonLine) && !BULLETED_BOLD_PHRASE_RE.test(reasonLine)) {
        flush();
        findings.push({
          phrase: stripQuotes(m[1]),
          // NOT firstAlternative: a bare, unquoted "/" in this shape is
          // dividing two numbers, not separating "X" / "Y" spelling
          // alternatives -- that convention only ever appears quoted, and
          // this shape has no quotes at all. CONFIRMED LIVE: "m * v^2 / r
          // = q * v * B" split at the division sign and lost everything
          // after it, because firstAlternative treats "/" as its
          // alternatives separator regardless of what put it there.
          suggestion: stripQuotes(sm[1]),
          reason: rm[1].trim(),
        });
        i += 2;
        continue;
      }
    }
  }
  flush();

  // "not sure" is a real, correct answer from the check prompt -- not a
  // usable correction. Nothing to search for or apply, so it is left out
  // of the actionable list rather than shown as a broken card.
  return findings
    .filter((f) => f.suggestion.toLowerCase() !== 'not sure')
    .map((f) => ({ ...f, ...classifyFinding(f) }));
}

// A NOTATION FINDING IS A DIFFERENT KIND OF LEAD FROM EVERYTHING ELSE ON
// THIS CHECKLIST. The check prompt's own math-notation section asks the
// assistant to start the reason with "Notation:" for exactly this reason
// -- a phrase like "p minus q a" -> "p - qA" is a formatting fix a worker
// can act on by reading it, not a claim about what was actually said, so
// it does not need the "go verify against audio first" caution that every
// other finding here genuinely does. Detected once, here, regardless of
// which of the three shapes above produced the finding, rather than
// duplicated at each push site -- and the marker itself is stripped from
// what gets shown, since the badge AccuracyChecklist renders from
// `notation` already says the same thing without the raw tag word.
const NOTATION_REASON_RE = /^notation:\s*/i;

/**
 * CHECKED ON BOTH `reason` AND `suggestion`, NOT JUST `reason`.
 *
 * CONFIRMED LIVE: a real report put "Notation:" on the suggestion line
 * instead of the reason line for four straight findings -- not a fluke,
 * a pattern, from an assistant that read the instruction as labelling
 * the corrected VALUE rather than the reason for it. Checked only on
 * `reason`, this missed the marker entirely: no badge, AND the literal
 * word "Notation:" got written straight into the transcript by Apply,
 * four times, on a document about to be submitted. Whichever field it
 * lands on, the marker means the same thing and must never reach the
 * document.
 */
function classifyFinding(finding) {
  const reason = (finding.reason || '').trim();
  const suggestion = (finding.suggestion || '').trim();
  const reasonMarked = NOTATION_REASON_RE.test(reason);
  const suggestionMarked = NOTATION_REASON_RE.test(suggestion);
  return {
    reason: reasonMarked ? reason.replace(NOTATION_REASON_RE, '') : reason,
    suggestion: suggestionMarked ? suggestion.replace(NOTATION_REASON_RE, '') : suggestion,
    notation: reasonMarked || suggestionMarked,
  };
}

/**
 * Whether `phrase` is present anywhere in `text`, quotes and case aside.
 * The single source of truth for "is this finding still there" -- used
 * directly for the checklist's found/not-found state, and by
 * `replaceFirst` below so the two can never disagree with each other.
 */
export function containsPhrase(text, phrase) {
  return normalizeQuotes(text || '').toLowerCase()
    .includes(normalizeQuotes(phrase || '').toLowerCase());
}

/**
 * Where `phrase` actually is among `rows` (each `{ text }`, already in
 * time order) -- checked one row at a time first, then across every
 * adjacent PAIR of rows read together.
 *
 * CONFIRMED LIVE: this task's segments run 5-8 seconds each, short enough
 * that a real multi-word finding routinely straddles the exact point a
 * clause happens to break at -- "power limit matrices" was "...the power
 * limit" at the end of one segment and "matrices is..." at the start of
 * the next; "mulching it by any other member" split the same way one
 * segment later. `containsPhrase` alone, checked one row at a time,
 * reported both as "Not found in the transcript -- likely already
 * fixed" even though every word of them was sitting there untouched,
 * because no SINGLE row's text ever contained the whole phrase.
 *
 * Returns `null` when the phrase is genuinely nowhere, `{ rows: [row] }`
 * for an ordinary single-segment match, or `{ rows: [rowA, rowB] }` when
 * it only appears once the two are joined -- the caller has to tell
 * these apart, because only the first is safe to rewrite automatically;
 * `replaceFirst` has no way to split a replacement across two rows.
 */
export function locatePhrase(rows, phrase) {
  const list = rows || [];
  const single = list.find((r) => containsPhrase(r.text, phrase));
  if (single) return { rows: [single] };
  for (let i = 0; i < list.length - 1; i += 1) {
    const a = list[i];
    const b = list[i + 1];
    if (containsPhrase(`${a.text || ''} ${b.text || ''}`, phrase)) {
      return { rows: [a, b] };
    }
  }
  return null;
}

/** Replace the FIRST case- and quote-insensitive occurrence of `phrase` in
 * `text`. The match is quote/case-insensitive; the slice it replaces, and
 * everything around it, is the original `text` untouched. */
export function replaceFirst(text, phrase, suggestion) {
  const source = text || '';
  const index = normalizeQuotes(source).toLowerCase()
    .indexOf(normalizeQuotes(phrase || '').toLowerCase());
  if (index === -1) return source;
  return source.slice(0, index) + suggestion + source.slice(index + (phrase || '').length);
}
