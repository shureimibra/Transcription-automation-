import { describe, expect, it } from 'vitest';
import { splitOnMatch } from '../hooks/useSegmentSearch.js';

// The scenario this exists for: a name spelled three different wrong ways
// across a long transcript, findable by searching a short fragment and
// stepping through the handful of segments that actually mention it
// instead of reading past everything that never did.

describe('splitOnMatch', () => {
  it('returns the whole text unmarked when the query is empty', () => {
    expect(splitOnMatch('Tillman met with King.', '')).toEqual([
      { text: 'Tillman met with King.', hit: false },
    ]);
  });

  it('marks a single match in the middle of the text', () => {
    const parts = splitOnMatch('Tillman met with King.', 'met');
    expect(parts).toEqual([
      { text: 'Tillman ', hit: false },
      { text: 'met', hit: true },
      { text: ' with King.', hit: false },
    ]);
  });

  it('marks every occurrence, not just the first', () => {
    const parts = splitOnMatch('Tillman spoke. Tillman listened.', 'Tillman');
    expect(parts.filter((p) => p.hit)).toHaveLength(2);
  });

  it('matches case-insensitively but keeps the original casing', () => {
    const parts = splitOnMatch('The NWRO met the nwro board.', 'nwro');
    const hits = parts.filter((p) => p.hit).map((p) => p.text);
    expect(hits).toEqual(['NWRO', 'nwro']);
  });

  it('returns the text unmarked when nothing matches', () => {
    expect(splitOnMatch('Tillman met with King.', 'Chisholm')).toEqual([
      { text: 'Tillman met with King.', hit: false },
    ]);
  });

  it('handles an empty or missing source text without throwing', () => {
    expect(splitOnMatch('', 'anything')).toEqual([{ text: '', hit: false }]);
    expect(splitOnMatch(undefined, 'anything')).toEqual([{ text: '', hit: false }]);
  });
});
