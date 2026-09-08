import { describe, expect, it } from 'vitest';
import { ANNOTATION_TAGS, insertTagAtPosition } from '../lib/annotations.js';

// What this pins down: a tag lands where the cursor actually was, not
// always at the end -- the exact bug reported ("if I click on that tag,
// most of those tags come to the end of the sentence") and traced to
// checking `document.activeElement === textarea` at insert time, which is
// essentially never true once a dropdown click has already moved focus
// off the textarea. This function takes the cursor as a plain argument
// instead, so the fix is testable without simulating DOM focus at all.

describe('insertTagAtPosition', () => {
  it('splices the tag in at the middle of a sentence', () => {
    const { text } = insertTagAtPosition(
      'I was going to say something', { start: 9, end: 9 }, '[pause]',
    );
    expect(text).toBe('I was goi[pause]ng to say something');
  });

  it('splices the tag in between two sentences, not at the very end', () => {
    const source = 'First sentence. Second sentence.';
    const midpoint = source.indexOf('Second');
    const { text } = insertTagAtPosition(
      source, { start: midpoint, end: midpoint }, '[laughter]',
    );
    expect(text).toBe('First sentence. [laughter]Second sentence.');
  });

  it('replaces a selection rather than inserting inside it', () => {
    const source = 'the quick brown fox';
    const start = source.indexOf('quick');
    const end = start + 'quick'.length;
    const { text } = insertTagAtPosition(source, { start, end }, '[inaudible]');
    expect(text).toBe('the [inaudible] brown fox');
  });

  it('falls back to appending at the end when no cursor was ever recorded', () => {
    const { text } = insertTagAtPosition('Hello there', null, '[silence]');
    expect(text).toBe('Hello there [silence]');
  });

  it('falls back to appending when cursor is undefined, same as null', () => {
    const { text } = insertTagAtPosition('Hello there', undefined, '[silence]');
    expect(text).toBe('Hello there [silence]');
  });

  it('clamps a stale out-of-range position instead of throwing or corrupting text', () => {
    // The position was recorded against a longer version of the text that
    // has since been edited shorter.
    const { text } = insertTagAtPosition('short', { start: 500, end: 500 }, '[pause]');
    expect(text).toBe('short[pause]');
  });

  it('clamps a negative position to the start of the text', () => {
    const { text } = insertTagAtPosition('hello', { start: -5, end: -5 }, '[pause]');
    expect(text).toBe('[pause]hello');
  });

  it('returns a cursor positioned right after the inserted tag', () => {
    const { cursor } = insertTagAtPosition('abcdef', { start: 3, end: 3 }, '[x]');
    // 'abc' (3) + '[x]' (3) = 6
    expect(cursor).toEqual({ start: 6, end: 6 });
  });

  it('a second tag inserted right after the first lands after it, not at the same spot', () => {
    const first = insertTagAtPosition('abcdef', { start: 3, end: 3 }, '[one]');
    expect(first.text).toBe('abc[one]def');
    const second = insertTagAtPosition(first.text, first.cursor, '[two]');
    expect(second.text).toBe('abc[one][two]def');
  });

  it('handles an empty/missing source text without throwing', () => {
    const { text } = insertTagAtPosition('', null, '[music]');
    expect(text).toBe('[music]');
    const { text: text2 } = insertTagAtPosition(undefined, null, '[music]');
    expect(text2).toBe('[music]');
  });
});

describe('ANNOTATION_TAGS', () => {
  it('is the exact, shared vocabulary both interfaces present', () => {
    // Pinned explicitly, not just "is a non-empty array": a silent change
    // here is a silent change to what every worker sees in the dropdown,
    // on both the general tool and the Eagle interface.
    expect(ANNOTATION_TAGS).toEqual([
      '[laughter]', '[music]', '[applause]', '[silence]', '[pause]',
      '[background noise]', '[crosstalk]', '[inaudible]', '[phone ringing]', '[door closes]',
    ]);
  });
});
