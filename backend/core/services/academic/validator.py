"""
Runs before an AcademicSegment's formatted text is accepted (see
run_academic_format_pipeline's 'validating' stage). The original transcript
is always the source of truth (ticket §12) -- a segment that fails any
check here falls back to its ORIGINAL text (was_modified=False) rather than
failing the whole job, exactly like a formatter's own conservatism: a
validation failure is not an error condition for the job, just a signal
that this one segment's conversion wasn't trustworthy enough to keep.
"""
import re
from typing import Tuple

_BRACKET_PAIRS = {')': '(', ']': '[', '}': '{'}
_OPENERS = set(_BRACKET_PAIRS.values())

# Content-word retention: formatting legitimately consumes a lot of
# "grammar" vocabulary (of/the/to/from/by/times/plus/minus/squared/...) as
# PART of a correct conversion ("square root of X" -> "√(X)" removes
# "square", "root", "of" on purpose) -- so this only ever counts words of
# 4+ letters and excludes the formatting vocabulary itself, and uses a
# generous threshold since even a fully-successful conversion of a short,
# math-heavy segment can reasonably replace most of its words with symbols.
_RETENTION_THRESHOLD = 0.35
_FORMATTING_VOCAB = {
    'square', 'root', 'plus', 'minus', 'times', 'over', 'squared', 'cubed',
    'equals', 'negative', 'power', 'divided', 'multiplied', 'greater',
    'less', 'than', 'equal', 'approximately', 'integral', 'from', 'derivative',
    'partial', 'summation', 'infinity', 'sine', 'cosine', 'tangent', 'degrees',
    'percent', 'meters', 'meter', 'seconds', 'second', 'kilograms', 'kilogram',
    'newtons', 'newton', 'joules', 'joule', 'watts', 'watt', 'volts', 'volt',
    'amperes', 'ampere', 'produces', 'yields', 'forms',
}


def _brackets_balanced(text: str) -> bool:
    stack = []
    for ch in text:
        if ch in _OPENERS:
            stack.append(ch)
        elif ch in _BRACKET_PAIRS:
            if not stack or stack.pop() != _BRACKET_PAIRS[ch]:
                return False
    return not stack


def _content_words(text: str) -> set:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return {w for w in words if w not in _FORMATTING_VOCAB}


def _has_duplicated_run(text: str, min_words: int = 4) -> bool:
    words = text.split()
    for i in range(len(words) - min_words * 2 + 1):
        if words[i:i + min_words] == words[i + min_words:i + min_words * 2]:
            return True
    return False


def validate(original_text: str, formatted_text: str) -> Tuple[bool, str]:
    """Returns (is_valid, reason) -- reason is '' when valid."""
    if not _brackets_balanced(formatted_text):
        return False, 'unbalanced brackets in formatted text'

    if _has_duplicated_run(formatted_text):
        return False, 'duplicated text detected in formatted output'

    original_content = _content_words(original_text)
    if original_content:
        formatted_content = _content_words(formatted_text)
        retained = len(original_content & formatted_content) / len(original_content)
        if retained < _RETENTION_THRESHOLD:
            return False, f'formatted text retained only {retained:.0%} of original content words'

    return True, ''
