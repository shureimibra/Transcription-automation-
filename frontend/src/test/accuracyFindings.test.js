import { describe, expect, it } from 'vitest';
import { containsPhrase, parseFindings, primaryPhrase, replaceFirst } from '../lib/accuracyFindings.js';

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
      notation: false,
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
      notation: false,
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

  it('matches a straight-quoted phrase against curly-quoted transcript text, and vice versa', () => {
    // CONFIRMED LIVE: real transcript text and a real pasted report
    // disagree about which apostrophe character they use often enough
    // that this has to work both directions.
    expect(replaceFirst('the professor’s office', "professor's office", 'X'))
      .toBe('the X');
    expect(replaceFirst("the professor's office", 'professor’s office', 'X'))
      .toBe('the X');
  });
});

describe('primaryPhrase', () => {
  // CONFIRMED LIVE: a rule-2 finding's phrase lists every wrong spelling
  // ("X" / "Y" / "Z") -- real evidence, kept intact on `finding.phrase`
  // for display -- but no transcript segment ever contains that whole
  // joined string. Find it / Apply / the found-check all need the ONE
  // alternative that could actually be on the page.

  it('picks the first alternative out of several, quotes and all', () => {
    expect(primaryPhrase('"Professor Esla\'s lectures" / "Professor Estlis lectures"'))
      .toBe("Professor Esla's lectures");
  });

  it('leaves a single, unquoted phrase alone beyond normalising its quotes', () => {
    expect(primaryPhrase('Professor Blondell’s course'))
      .toBe("Professor Blondell's course");
  });

  it('is a no-op on a plain phrase with nothing to split or normalise', () => {
    expect(primaryPhrase('first turn')).toBe('first turn');
  });
});

describe('containsPhrase', () => {
  it('matches regardless of which side used a curly apostrophe', () => {
    expect(containsPhrase("in Professor Blondell's course, he covered it",
      'Professor Blondell’s course')).toBe(true);
    expect(containsPhrase('in Professor Blondell’s course, he covered it',
      "Professor Blondell's course")).toBe(true);
  });

  it('is false when the phrase genuinely is not there', () => {
    expect(containsPhrase('nothing relevant here', 'missing phrase')).toBe(false);
  });
});

describe('parseFindings: notation vs. mishearing findings', () => {
  // The check prompt's math-notation section asks the assistant to start
  // the reason with "Notation:" specifically so a formatting fix ("p minus
  // q a" -> "p - qA") can be told apart from a claim about what was
  // actually said -- the first needs no audio check, the second always
  // does. Verified across two different parsed shapes, since the marker
  // has to survive regardless of which one produced the finding.

  it('marks a labelled finding as notation and strips the marker from the reason', () => {
    const report = (
      '* the phrase as transcribed: p minus q a\n'
      + '* what you think it should probably be: p - qA\n'
      + '* why: Notation: this is a written-out equation, not spoken language.'
    );
    const [finding] = parseFindings(report);
    expect(finding.notation).toBe(true);
    expect(finding.reason).toBe('this is a written-out equation, not spoken language.');
  });

  it('marks a bulleted-bold finding as notation the same way', () => {
    const report = (
      '* **Sigma**\n'
      + '* Σ\n'
      + '* Notation: the Greek letter should be spelled as a word, not the glyph.'
    );
    const [finding] = parseFindings(report);
    expect(finding.notation).toBe(true);
  });

  it('leaves an ordinary finding unmarked, with its reason untouched', () => {
    const report = (
      '* the phrase as transcribed: Professor Blondell\n'
      + '* what you think it should probably be: Professor Blundell\n'
      + '* why: A real Oxford physics professor, misspelled phonetically.'
    );
    const [finding] = parseFindings(report);
    expect(finding.notation).toBe(false);
    expect(finding.reason).toBe('A real Oxford physics professor, misspelled phonetically.');
  });
});
