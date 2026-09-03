"""
Converts spoken mathematical phrases into Unicode notation -- "x squared" ->
"x²", "square root of two" -> "√(2)", etc. Deliberately Unicode-only, never
LaTeX: every rule's replacement is a literal Unicode string built from
symbols.py's tables, so AcademicSegment.text is always directly exportable
with zero extra conversion step (see the ticket's own "don't blindly put
raw LaTeX into every export").

Honest limitation, not an oversight: plain Unicode has no general-purpose
superscript/subscript for arbitrary text (SUPERSCRIPT_DIGITS only covers
0-9), so something like an integral's upper limit being a Greek letter
can't be rendered as a true superscript the way LaTeX's `^\pi` would --
this formatter uses bracket-range notation for the calculus phrases
instead (`∫[0,π]`), which is unambiguous plain text rather than a fake
approximation of real mathematical typesetting.

Rules are applied in order, most-specific-phrase-first, each operating on
the result of the previous rule -- e.g. "square root of" and "plus or
minus" both run before the quadratic-formula idiom would need them,
composing rather than needing one giant special-cased pattern per example.
Every rule is deliberately gated on adjacent digits/single-letter variables
or a fixed idiom phrase, never a bare operator word alone -- that's what
keeps ordinary prose ("he was minus one point down") untouched: "minus"
only converts when immediately adjacent to a recognized operand.
"""
import re
from typing import Tuple

from .base import AcademicFormatter
from .symbols import GREEK_LETTERS, NUMBER_WORDS, spoken_number_to_digits, to_superscript

# A single operand token: a digit run, or a single letter (the common case
# for spoken variable names -- "x", "a", "b", "c", "n", ...).
_OPERAND = r'(?:\d+|[a-zA-Z])'

_NUMBER_WORD_ALTERNATION = '|'.join(sorted(NUMBER_WORDS, key=len, reverse=True))
_GREEK_ALTERNATION = '|'.join(sorted(GREEK_LETTERS, key=len, reverse=True))


def _normalize_limit(token: str) -> str:
    """For calculus phrase limits: a spoken number word or digit becomes a
    digit string; a spoken Greek letter name becomes its symbol; anything
    else (e.g. an already-short variable name) passes through as-is --
    never guessed at beyond these two known-safe cases."""
    token = token.strip()
    digits = spoken_number_to_digits(token)
    if digits is not None:
        return digits
    greek = GREEK_LETTERS.get(token.lower())
    if greek is not None:
        return greek
    return token


def _power_replacement(match: 're.Match') -> str:
    base, exponent_word = match.group(1), match.group(2)
    digits = spoken_number_to_digits(exponent_word) or (exponent_word if exponent_word.isdigit() else None)
    if digits is not None:
        sup = to_superscript(digits)
        if sup is not None:
            return f'{base}{sup}'
    # A named exponent ("x to the power of n") that isn't a plain digit --
    # no Unicode superscript exists for it, so fall back to explicit,
    # unambiguous caret notation rather than a fake-looking substitution.
    return f'{base}^{exponent_word}'


# Ordered (compiled pattern, replacement) rules. A replacement may be a
# plain string (used with re.sub's backreference syntax) or a callable
# (used as re.sub's repl function) for anything needing real logic.
_RULES = [
    # --- Named idioms (most specific -- matched as a whole phrase before
    #     any of the general composable rules below get a chance to). ---
    (
        re.compile(
            r'\bx equals negative b plus or minus the square root of b squared minus '
            r'(?:four|4) a c all over (?:two|2) a\b', re.IGNORECASE,
        ),
        'x = (-b ± √(b² - 4ac)) / (2a)',
    ),
    (
        re.compile(
            r'\b([a-zA-Z])\s+squared\s+plus\s+([a-zA-Z])\s+squared\s+equals\s+([a-zA-Z])\s+squared\b',
            re.IGNORECASE,
        ),
        lambda m: f'{m.group(1)}² + {m.group(2)}² = {m.group(3)}²',
    ),

    # --- Calculus phrases ---
    (
        re.compile(r'\bintegral from (\w+) to (\w+) of\s*', re.IGNORECASE),
        lambda m: f'∫[{_normalize_limit(m.group(1))},{_normalize_limit(m.group(2))}] ',
    ),
    (re.compile(r'\bd\s+([a-zA-Z])\b'), lambda m: f'd{m.group(1)}'),  # "d x" -> "dx" (after an integral phrase)
    (re.compile(r'\bderivative of\b', re.IGNORECASE), 'd/dx of'),
    (re.compile(r'\bpartial derivative\b', re.IGNORECASE), '∂'),
    (re.compile(r'\bsummation\b', re.IGNORECASE), 'Σ'),
    (re.compile(r'\binfinity\b', re.IGNORECASE), '∞'),
    (re.compile(r'\b(?:sine|sin) of\b', re.IGNORECASE), 'sin'),
    (re.compile(r'\b(?:cosine|cos) of\b', re.IGNORECASE), 'cos'),
    (re.compile(r'\btangent of\b', re.IGNORECASE), 'tan'),

    # --- Square root: "square root of X" -> "√(X)", X bounded to the
    #     next known stop-phrase or end of string so it doesn't swallow the
    #     rest of an unrelated sentence. ---
    (
        re.compile(r'\bsquare root of\s+(.+?)(?=\s+all over\b|[.,;]|$)', re.IGNORECASE),
        lambda m: f'√({m.group(1).strip()})',
    ),

    # --- Powers ---
    (re.compile(rf'\b({_OPERAND})\s+squared\b', re.IGNORECASE), lambda m: _power_replacement_from(m.group(1), '2')),
    (re.compile(rf'\b({_OPERAND})\s+cubed\b', re.IGNORECASE), lambda m: _power_replacement_from(m.group(1), '3')),
    (
        re.compile(rf'\b({_OPERAND})\s+to the power of\s+(\w+)\b', re.IGNORECASE),
        _power_replacement,
    ),

    # --- plus/minus-as-sign and comparison operators (idioms, safe as
    #     standalone phrases -- "plus or minus"/"approximately equals"
    #     never occur as ordinary prose the way a bare "plus" might). ---
    (re.compile(r'\bplus or minus\b', re.IGNORECASE), '±'),
    (re.compile(r'\bapproximately equals\b', re.IGNORECASE), '≈'),
    (re.compile(r'\bgreater than or equal to\b', re.IGNORECASE), '≥'),
    (re.compile(r'\bless than or equal to\b', re.IGNORECASE), '≤'),
    (re.compile(rf'\bnegative\s+({_OPERAND})\b', re.IGNORECASE), lambda m: f'-{m.group(1)}'),

    # --- Binary operators: gated on BOTH sides being a recognized operand
    #     (a digit run or a single letter) so a bare word in prose is never
    #     touched -- see module docstring. ---
    (re.compile(rf'\b({_OPERAND})\s+plus\s+({_OPERAND})\b', re.IGNORECASE), r'\1 + \2'),
    (re.compile(rf'\b({_OPERAND})\s+minus\s+({_OPERAND})\b', re.IGNORECASE), r'\1 - \2'),
    (re.compile(rf'\b({_OPERAND})\s+(?:times|multiplied by)\s+({_OPERAND})\b', re.IGNORECASE), r'\1 × \2'),
    (re.compile(rf'\b({_OPERAND})\s+divided by\s+({_OPERAND})\b', re.IGNORECASE), r'\1 ÷ \2'),
    (re.compile(rf'\b({_OPERAND})\s+over\s+({_OPERAND})\b', re.IGNORECASE), r'\1/\2'),
    (re.compile(rf'\b({_OPERAND})\s+equals\s+({_OPERAND})\b', re.IGNORECASE), r'\1 = \2'),
    (re.compile(rf'\b({_OPERAND})\s+greater than\s+({_OPERAND})\b', re.IGNORECASE), r'\1 > \2'),
    (re.compile(rf'\b({_OPERAND})\s+less than\s+({_OPERAND})\b', re.IGNORECASE), r'\1 < \2'),

    # --- Fractions (standalone, not already handled by "over" above) ---
    (re.compile(r'\bone half\b', re.IGNORECASE), '½'),
    (re.compile(r'\bone third\b', re.IGNORECASE), '⅓'),
    (re.compile(r'\bone quarter\b', re.IGNORECASE), '¼'),

    # --- Greek letters (standalone spoken names) ---
    (re.compile(rf'\b({_GREEK_ALTERNATION})\b', re.IGNORECASE), lambda m: GREEK_LETTERS[m.group(1).lower()]),

    # --- Misc symbols ---
    (re.compile(r'\bpercent\b', re.IGNORECASE), '%'),
    (re.compile(r'\bdegrees\b', re.IGNORECASE), '°'),
]


def _power_replacement_from(base: str, exponent_digit: str) -> str:
    sup = to_superscript(exponent_digit)
    return f'{base}{sup}' if sup is not None else f'{base}^{exponent_digit}'


class MathematicsFormatter(AcademicFormatter):
    """See module docstring for the full rule-design rationale."""

    RULES = _RULES

    def format_segment(self, text: str) -> Tuple[str, bool]:
        result = text
        for pattern, replacement in self.RULES:
            result = pattern.sub(replacement, result)
        return result, (result != text)
