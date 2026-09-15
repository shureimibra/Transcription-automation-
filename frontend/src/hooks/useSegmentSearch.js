import { useEffect, useMemo, useState } from 'react';

/**
 * Find every segment mentioning a word, and step through just those.
 *
 * THE SCENARIO THIS EXISTS FOR: the ASR misheard one name three different
 * wrong ways across a 400-segment lecture, or spelled the right one
 * correctly in some places and wrong in others. Finding every occurrence
 * by scrolling and reading meant a worker either caught it by luck or
 * missed it -- there was no way to ask "where does this word appear" and
 * get taken there. This turns that into: type the word, see how many
 * places it shows up, jump to each one, listen, fix or confirm, move on --
 * instead of reading past 380 segments that were never the problem to find
 * the 5 that are.
 *
 * COLLAPSED BY DEFAULT, ON PURPOSE. A search box permanently sitting above
 * a transcript a worker reads top-to-bottom most of the time is a feature
 * nobody asked for taking up room every single task. `open` starts false
 * and the caller only renders the bar once it is true.
 *
 * `rows` is whatever the caller is already listing; `textOf` says which
 * field to search -- the English side on a translation task (what the
 * worker actually reads against the audio), the transcript text on a
 * transcription task. Matching is substring, case-insensitive: a phonetic
 * near-miss needs a different search, but a name spelled two different
 * wrong ways is usually still findable by a short common fragment of it.
 */
export function useSegmentSearch(rows, textOf = (row) => row.text) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    const found = [];
    rows.forEach((row, position) => {
      const haystack = (textOf(row) || '').toLowerCase();
      if (haystack.includes(needle)) found.push({ row, position });
    });
    return found;
  }, [rows, query, textOf]);

  // THE CURSOR SURVIVES A NARROWING SEARCH, NOT A WIDENING ONE. Editing the
  // query to add a letter can only ever remove matches from the list a
  // worker is stepping through, so clamping back to 0 only when the
  // current position has actually fallen off the end keeps "match 3 of 5"
  // pointing at the same real match after a keystroke shrinks the count,
  // rather than silently jumping back to the first one every time.
  useEffect(() => {
    if (index >= matches.length) setIndex(0);
  }, [matches.length, index]);

  const current = matches[index] || null;

  const next = () => {
    if (!matches.length) return;
    setIndex((i) => (i + 1) % matches.length);
  };
  const prev = () => {
    if (!matches.length) return;
    setIndex((i) => (i - 1 + matches.length) % matches.length);
  };

  const openSearch = () => setOpen(true);
  // CLOSING CLEARS THE QUERY. Reopening later to a blank box, not
  // whatever was last searched, is what "closed" should mean -- otherwise
  // the dimmed/highlighted state a worker dismissed comes right back.
  const closeSearch = () => { setOpen(false); setQuery(''); setIndex(0); };

  return {
    open, query, setQuery, matches, index, setIndex, current,
    next, prev, openSearch, closeSearch,
  };
}

/**
 * One field's text, split around the search term for highlighting.
 *
 * Same shape as ReviewWorkspace's own `sentencesWithActive` (an array of
 * `{ text, hit }` parts) so both can be rendered by the same kind of
 * `<span className={hit ? ... : undefined}>` map, rather than inventing a
 * second convention for marking up a piece of text.
 */
export function splitOnMatch(text, query) {
  const needle = query.trim();
  if (!needle) return [{ text: text || '', hit: false }];
  const haystack = text || '';
  const lower = haystack.toLowerCase();
  const needleLower = needle.toLowerCase();
  const parts = [];
  let from = 0;
  let at = lower.indexOf(needleLower, from);
  if (at === -1) return [{ text: haystack, hit: false }];
  while (at !== -1) {
    if (at > from) parts.push({ text: haystack.slice(from, at), hit: false });
    parts.push({ text: haystack.slice(at, at + needle.length), hit: true });
    from = at + needle.length;
    at = lower.indexOf(needleLower, from);
  }
  if (from < haystack.length) parts.push({ text: haystack.slice(from), hit: false });
  return parts;
}
