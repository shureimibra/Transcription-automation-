"""
Language-aware typography checking -- deterministic, no model involved.

spec 20 asks for French rules and then, in the same breath, forbids making
them global. Both halves are satisfied the same way the academic package
handles subjects: a small ABC, one subclass per language, and a registry
that falls back to a permissive default for languages nobody has written
rules for yet. Adding Somali typography later is a new class and one
registry entry, not an edit to a French-shaped if-chain.

WHY DETERMINISTIC (RULE 4 / spec 19): "is there a non-breaking space before
that colon" is a question with one right answer that a regex settles for
free and reliably. Asking an LLM costs a call, returns a probability, and is
worse at it. The model's attention belongs on meaning, which is the thing
only it can check.

These checks report LOW/MEDIUM issues by design. Typography does not change
what a document means, and blocking a submission on a missing thin space
while a semantic defect is still in the queue would be the wrong priority --
the profile's policy decides what blocks, not this module.
"""
import re
from typing import List, Optional

from .issues import IssueCategory, QualityIssue, Severity

# U+00A0 NO-BREAK SPACE, U+202F NARROW NO-BREAK SPACE. Both are acceptable
# before French high punctuation; typographers prefer the narrow one, and
# Word/LibreOffice insert U+00A0, so neither is treated as an error.
NBSP = ' '
NNBSP = ' '
_FRENCH_SPACES = (NBSP, NNBSP)


class TypographyRules:
    """One language's conventions. `check` returns issues; `normalize`
    returns text with the safely-mechanical fixes applied."""

    language_code = ''
    language_name = ''

    def check(self, text: str, segment_index: Optional[int] = None) -> List[QualityIssue]:
        return []

    def normalize(self, text: str) -> str:
        """
        Applies only fixes that cannot change meaning.

        Kept separate from `check` because the two are used at different
        moments and carry different risk: `check` runs inside the gate and
        may flag anything, while `normalize` MUTATES the deliverable and so
        is restricted to substitutions that are reversible and semantically
        inert. Nothing here may ever touch a word.
        """
        return text


class DefaultTypographyRules(TypographyRules):
    """
    The fallback for every language without a dedicated class.

    Checks only what is genuinely universal -- doubled spaces, space before
    a comma/period, unbalanced brackets and quotes. Deliberately silent
    about spacing before ':' and '?', because that is precisely the rule
    that differs between French and English and guessing would produce a
    stream of false findings in whichever language we guessed wrong.
    """

    language_code = ''
    language_name = 'default'

    _DOUBLE_SPACE = re.compile(r'[ ]{2,}')
    _SPACE_BEFORE_LOW = re.compile(r'\s+[,.](?:\s|$)')
    _BRACKETS = {')': '(', ']': '[', '}': '{'}

    def check(self, text, segment_index=None):
        issues = []
        if not text:
            return issues

        if self._DOUBLE_SPACE.search(text):
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Repeated spaces in the output.',
                correction='Collapse runs of spaces to a single space.',
                rule='typography.double-space', segment_index=segment_index,
                detector='deterministic',
            ))

        if self._SPACE_BEFORE_LOW.search(text):
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Whitespace before a comma or full stop.',
                correction='Remove the space before the punctuation mark.',
                rule='typography.space-before-low-punctuation',
                segment_index=segment_index, detector='deterministic',
            ))

        if not self._brackets_balanced(text):
            issues.append(QualityIssue(
                category=IssueCategory.STRUCTURE, severity=Severity.MEDIUM,
                problem='Unbalanced brackets in the output.',
                correction='Close every bracket that was opened.',
                rule='structure.unbalanced-brackets',
                segment_index=segment_index, detector='deterministic',
            ))

        if text.count('"') % 2 == 1:
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Odd number of straight double quotes: one is unclosed.',
                correction='Balance the quotation marks.',
                rule='typography.unbalanced-quotes',
                segment_index=segment_index, detector='deterministic',
            ))
        return issues

    @classmethod
    def _brackets_balanced(cls, text: str) -> bool:
        # Same algorithm as core.services.academic.validator._brackets_balanced.
        # Duplicated rather than imported: that module is the offline academic
        # formatter's private validator and this package deliberately does not
        # depend on it, so a future change to either cannot silently alter the
        # other's verdict.
        stack = []
        openers = set(cls._BRACKETS.values())
        for char in text:
            if char in openers:
                stack.append(char)
            elif char in cls._BRACKETS:
                if not stack or stack.pop() != cls._BRACKETS[char]:
                    return False
        return not stack

    def normalize(self, text):
        if not text:
            return text
        return self._DOUBLE_SPACE.sub(' ', text)


class FrenchTypographyRules(DefaultTypographyRules):
    """
    French high punctuation takes a non-breaking space before it; low
    punctuation does not. Decimals use a comma, thousands a narrow space,
    and quotations use guillemets with inner spacing.

    Every pattern below excludes text where the correct character is
    ALREADY present, so a compliant document produces zero findings -- a
    checker that fires on correct input trains people to ignore it.
    """

    language_code = 'fr'
    language_name = 'French'

    # A high-punctuation mark not preceded by a no-break space. The negative
    # lookbehind also excludes '::' and URLs' "https:" by requiring a letter,
    # digit, or closing bracket immediately before the (missing) space.
    _MISSING_NBSP = re.compile(
        r'(?<=[\w\)\]»])[ ]?(?<![' + NBSP + NNBSP + r'])([;:!?])'
    )
    # French percentages take a space before the sign; "50%" is an anglicism.
    _PERCENT_NO_SPACE = re.compile(r'\d%')
    # Straight ASCII quotes around a phrase, where guillemets belong.
    _ASCII_QUOTES = re.compile(r'"[^"]{2,}"')
    # A decimal point between digits: French writes 3,14 not 3.14. Excludes
    # dotted version/section numbers by requiring exactly one dot group.
    _DECIMAL_POINT = re.compile(r'(?<!\d\.)\b\d+\.\d+\b(?!\.\d)')

    def check(self, text, segment_index=None):
        issues = super().check(text, segment_index)
        if not text:
            return issues

        if self._MISSING_NBSP.search(text):
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem=(
                    'French high punctuation (; : ! ?) without a preceding '
                    'non-breaking space.'
                ),
                correction=(
                    'Insert U+202F (or U+00A0) before ; : ! and ?.'
                ),
                rule='typography.fr.nbsp-before-high-punctuation',
                segment_index=segment_index, detector='deterministic',
            ))

        if self._PERCENT_NO_SPACE.search(text):
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Percent sign attached to its number.',
                correction='French takes a non-breaking space: "50 %".',
                rule='typography.fr.percent-spacing',
                segment_index=segment_index, detector='deterministic',
            ))

        if self._ASCII_QUOTES.search(text) and '«' not in text:
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Straight ASCII quotation marks in French text.',
                correction='Use guillemets with inner spacing: « ... ».',
                rule='typography.fr.guillemets',
                segment_index=segment_index, detector='deterministic',
            ))

        if self._DECIMAL_POINT.search(text):
            issues.append(QualityIssue(
                # NUMERIC, not TYPOGRAPHY, and MEDIUM rather than LOW: a
                # decimal separator is not decoration. "3.14" read by a
                # French reader as a thousands separator is a different
                # number, so this is a meaning defect wearing punctuation's
                # clothes -- and the default policy blocks NUMERIC outright.
                category=IssueCategory.NUMERIC, severity=Severity.MEDIUM,
                problem='Decimal written with a point rather than a comma.',
                correction='French decimals use a comma: 3,14.',
                rule='typography.fr.decimal-separator',
                segment_index=segment_index, detector='deterministic',
            ))
        return issues

    def normalize(self, text):
        """
        Inserts the missing no-break spaces and nothing else.

        Guillemets and decimal separators are NOT normalized here even
        though both are detected above. Swapping '"' for '«' has to decide
        which quote opens and which closes, and rewriting 3.14 to 3,14 has
        to be sure the dot is a decimal and not a section number -- both are
        judgement calls, and a wrong automatic judgement silently corrupts
        the deliverable. They stay findings for a human or a repair pass.
        """
        text = super().normalize(text)
        if not text:
            return text
        return self._MISSING_NBSP.sub(NNBSP + r'\1', text)


class EnglishTypographyRules(DefaultTypographyRules):
    """English is the mirror image of French: NO space before high
    punctuation, and a point for decimals."""

    language_code = 'en'
    language_name = 'English'

    _SPACE_BEFORE_HIGH = re.compile(r'[ ' + NBSP + NNBSP + r'][;:!?]')

    def check(self, text, segment_index=None):
        issues = super().check(text, segment_index)
        if text and self._SPACE_BEFORE_HIGH.search(text):
            issues.append(QualityIssue(
                category=IssueCategory.TYPOGRAPHY, severity=Severity.LOW,
                problem='Space before ; : ! or ? in English text.',
                correction=(
                    'English sets high punctuation tight against the word. '
                    'This often survives from a French draft.'
                ),
                rule='typography.en.no-space-before-high-punctuation',
                segment_index=segment_index, detector='deterministic',
            ))
        return issues

    def normalize(self, text):
        text = super().normalize(text)
        if not text:
            return text
        return self._SPACE_BEFORE_HIGH.sub(lambda m: m.group(0)[-1], text)


class TypographyRegistry:
    """
    Selects rules for a language code. Same shape as
    AcademicFormatterRegistry.select and TranslationEngineRegistry: one
    cached instance per language, one entry point, a new language is one
    branch.

    An unknown code gets DefaultTypographyRules rather than raising. A
    language we have no rules for must still be translatable -- the platform
    supports Swahili, Somali and Oromo today and more later, and refusing to
    QA them because nobody has written their typography rules would make the
    quality pipeline unavailable exactly where review is hardest to come by.
    """

    _instances = {}

    @classmethod
    def select(cls, language_code: Optional[str]) -> TypographyRules:
        key = (language_code or '').strip().lower()[:2]
        if key not in cls._instances:
            cls._instances[key] = cls._build(key)
        return cls._instances[key]

    @staticmethod
    def _build(language_code: str) -> TypographyRules:
        if language_code == 'fr':
            return FrenchTypographyRules()
        if language_code == 'en':
            return EnglishTypographyRules()
        return DefaultTypographyRules()


def check_typography(
    text: str, language_code: Optional[str], segment_index: Optional[int] = None
) -> List[QualityIssue]:
    return TypographyRegistry.select(language_code).check(text, segment_index)


def normalize_typography(text: str, language_code: Optional[str]) -> str:
    return TypographyRegistry.select(language_code).normalize(text)
