import { describe, expect, it } from 'vitest';
import { parseVerifyItems } from '../lib/verifyAudio.js';

describe('parseVerifyItems', () => {
  it('parses the exact shape the polish prompt asks for', () => {
    const items = parseVerifyItems(
      '"the ninth circuit" -- unclear whether it is the ninth or the second circuit\n'
      + '"Alito" -- unsure if "Jr." belongs after the name',
    );
    expect(items).toEqual([
      { phrase: 'the ninth circuit', note: 'unclear whether it is the ninth or the second circuit' },
      { phrase: 'Alito', note: 'unsure if "Jr." belongs after the name' },
    ]);
  });

  it('returns an empty list for NONE, case-insensitively', () => {
    expect(parseVerifyItems('NONE')).toEqual([]);
    expect(parseVerifyItems('none')).toEqual([]);
    expect(parseVerifyItems('  None  ')).toEqual([]);
  });

  it('returns an empty list for empty or missing text', () => {
    expect(parseVerifyItems('')).toEqual([]);
    expect(parseVerifyItems(null)).toEqual([]);
    expect(parseVerifyItems(undefined)).toEqual([]);
  });

  it('does not split on a hyphen that is part of the phrase itself', () => {
    // "circuit-court" has its own internal hyphen -- a naive split on
    // "-" would cut the phrase there instead of at the real separator.
    const items = parseVerifyItems(
      '"the circuit-court judge" -- unsure which one',
    );
    expect(items[0].phrase).toBe('the circuit-court judge');
    expect(items[0].note).toBe('unsure which one');
  });

  it('strips a backtick-quoted phrase, the same defence as the transcription side', () => {
    // CONFIRMED LIVE, 2026-09-22: a worker's assistant wrapping phrases in
    // backticks broke every "Find it" on the transcription side until
    // stripQuotes learned to strip them. Same risk here, same fix reused.
    const items = parseVerifyItems('`the ninth circuit` -- unclear which circuit');
    expect(items[0].phrase).toBe('the ninth circuit');
  });

  it('normalises a curly apostrophe in the phrase to match straight-quoted source text', () => {
    const items = parseVerifyItems('"the professor’s office" -- unclear which professor');
    expect(items[0].phrase).toBe("the professor's office");
  });

  it('accepts an em dash or a plain hyphen as the separator, not only double hyphens', () => {
    expect(parseVerifyItems('"a phrase" — some reason')[0].note).toBe('some reason');
    expect(parseVerifyItems('"a phrase" - some reason')[0].note).toBe('some reason');
  });

  it('keeps a line with no quoted phrase as a note with nothing to search for', () => {
    const items = parseVerifyItems('the whole third paragraph is hard to make out');
    expect(items).toHaveLength(1);
    expect(items[0].phrase).toBe('');
    expect(items[0].note).toBe('the whole third paragraph is hard to make out');
  });

  it('ignores blank lines between items', () => {
    const items = parseVerifyItems('"one" -- first\n\n"two" -- second\n');
    expect(items).toHaveLength(2);
  });
});
