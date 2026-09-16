import { describe, expect, it } from 'vitest';
import { parseFindings, replaceFirst } from '../lib/accuracyFindings.js';

// Real report shapes seen in practice, not invented ones -- the check
// prompt asks for one exact structure, but a worker's own assistant does
// not always give it back to the letter.

describe('parseFindings', () => {
  it('parses the prompt-canonical, asterisk-bulleted shape', () => {
    const report = `
* the phrase as transcribed: Sean, North Carolina
* what you think it should probably be: Shaw, North Carolina
* why: Mishearing of Shaw University in Raleigh, North Carolina, where SNCC was founded.
* the phrase as transcribed: Richard daily
* what you think it should probably be: Richard Daley
* why: Misspelling of the long-serving Mayor of Chicago, Richard J. Daley.
`;
    const findings = parseFindings(report);
    expect(findings).toHaveLength(2);
    expect(findings[0]).toEqual({
      phrase: 'Sean, North Carolina',
      suggestion: 'Shaw, North Carolina',
      reason: 'Mishearing of Shaw University in Raleigh, North Carolina, where SNCC was founded.',
    });
    expect(findings[1].phrase).toBe('Richard daily');
    expect(findings[1].suggestion).toBe('Richard Daley');
  });

  it('parses the same fields without asterisks and with "why, in one line:"', () => {
    const report = `
the phrase as transcribed: quant mechanics
what you think it should probably be: quantum mechanics
why, in one line: A clear phonetic truncation of the standard technical term.
`;
    const findings = parseFindings(report);
    expect(findings).toHaveLength(1);
    expect(findings[0].phrase).toBe('quant mechanics');
    expect(findings[0].suggestion).toBe('quantum mechanics');
  });

  it('parses the bare, unlabelled shape (a real ChatGPT report)', () => {
    const report = `
"Snick" / "SNick" / "Snick's" / "Snick is"
probably "SNCC" / "SNCC's"
The Student Nonviolent Coordinating Committee is conventionally abbreviated SNCC, and the transcript repeatedly renders the same organization as "Snick."

"the SCL scene"
probably "the SCLC"
The organization discussed is the Southern Christian Leadership Conference (SCLC), which is named explicitly later.
`;
    const findings = parseFindings(report);
    expect(findings).toHaveLength(2);
    expect(findings[0].phrase).toBe('Snick');
    expect(findings[0].suggestion).toBe('SNCC');
    expect(findings[0].reason).toMatch(/Student Nonviolent Coordinating Committee/);
    expect(findings[1].phrase).toBe('the SCL scene');
    expect(findings[1].suggestion).toBe('the SCLC');
  });

  it('parses the bulleted-bold shape (a real report, confirmed live -- the one that made a worker conclude the checklist was never built)', () => {
    const report = `
* **Moett's**
* Mowat's
* Farley Mowat was introduced just previously; this is a misspelling of his surname.
* **Eskimo's**
* Eskimos
* Plural form misheard/mistranscribed with an erroneous possessive apostrophe (occurs multiple times: "Eskimo's probably figured", "handled by Eskimo's", "worry about Eskimo's").
* **a miner's fame**
* a miner's pay
* "Fame" does not parse in context with doing a miner's work; "pay" is the standard idiomatic and phonetic fit.
* **Couserck**
* not sure
* Garbled Inuit personal name from the 1960 NFB film documentary narration.
`;
    const findings = parseFindings(report);
    // "Couserck" is dropped -- "not sure" is a real, correct answer, not a
    // usable correction, same as every other shape.
    expect(findings).toHaveLength(3);
    expect(findings[0]).toEqual({
      phrase: "Moett's",
      suggestion: "Mowat's",
      reason: 'Farley Mowat was introduced just previously; this is a misspelling of his surname.',
    });
    expect(findings[1].phrase).toBe("Eskimo's");
    expect(findings[1].suggestion).toBe('Eskimos');
    expect(findings[2].phrase).toBe("a miner's fame");
    expect(findings[2].suggestion).toBe('a miner\'s pay');
  });

  it('does not let the bulleted-bold shape swallow the next finding when a reason line is missing', () => {
    // Two consecutive findings with no gap -- the second's bold phrase line
    // must not be consumed as the first's reason.
    const report = `
* **first**
* fixed
* **second**
* also fixed
* because
`;
    const findings = parseFindings(report);
    expect(findings).toHaveLength(1);
    expect(findings[0].phrase).toBe('second');
    expect(findings[0].suggestion).toBe('also fixed');
    expect(findings[0].reason).toBe('because');
  });

  it('drops a "not sure" answer -- a real, correct reply with nothing to apply', () => {
    const report = `
* the phrase as transcribed: cannabis
* what you think it should probably be: not sure
* why: Context describes law enforcement; likely county deputies.
`;
    expect(parseFindings(report)).toEqual([]);
  });

  it('returns an empty list for text with no findings, rather than throwing', () => {
    expect(parseFindings('CLEAN')).toEqual([]);
    expect(parseFindings('')).toEqual([]);
    expect(parseFindings(undefined)).toEqual([]);
  });

  it('only ever uses the first alternative when several are offered', () => {
    const report = `
* the phrase as transcribed: Snick
* what you think it should probably be: SNCC / SNCC's
* why: abbreviation
`;
    expect(parseFindings(report)[0].suggestion).toBe('SNCC');
  });
});

describe('replaceFirst', () => {
  it('replaces the first case-insensitive occurrence only', () => {
    expect(replaceFirst('Meridale said this, Meridale said that', 'meridale', 'Mayor Daley'))
      .toBe('Mayor Daley said this, Meridale said that');
  });

  it('leaves the text unchanged when the phrase is not found', () => {
    expect(replaceFirst('Nothing to see here', 'missing', 'X')).toBe('Nothing to see here');
  });
});
