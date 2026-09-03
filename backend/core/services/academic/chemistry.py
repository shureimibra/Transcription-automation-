"""
Chemistry notation: element-symbol-plus-spoken-digit -> Unicode subscript
("H two O" -> "H₂O"), ion charge suffixes ("Na plus" -> "Na⁺"), and
reaction-verb-gated arrow insertion between two recognized compound/element
phrases. Never balances or invents stoichiometry (ticket §8) -- the arrow
rule only ever replaces the spoken verb with "→"; it does not add
coefficients that weren't already present in the transcript.
"""
import re
from typing import List, Tuple

from .base import AcademicFormatter
from .symbols import NUMBER_WORDS, to_subscript, to_superscript

# Common element symbols as they'd appear already letter-spelled in an ASR
# transcript of someone reading a formula aloud (ticket's own examples --
# "H two O", "C O two", "Na plus" -- all use the symbol letters directly,
# not full element names). Two-letter symbols listed first so the
# alternation prefers them over a false single-letter match.
_ELEMENT_SYMBOLS = [
    'Na', 'Cl', 'Ca', 'Mg', 'Fe', 'Zn', 'Cu', 'Ag', 'Au', 'Al', 'Br', 'Si',
    'H', 'O', 'C', 'N', 'S', 'P', 'K', 'I',
]
_ELEMENT_ALTERNATION = '|'.join(_ELEMENT_SYMBOLS)
_NUMBER_WORD_ALTERNATION = '|'.join(sorted(NUMBER_WORDS, key=len, reverse=True))

# One element, optionally followed by a spoken/digit count -- the building
# block for both a standalone subscript conversion and a multi-element
# formula run (see _format_formula_runs).
_ELEMENT_TOKEN_RE = re.compile(
    rf'\b({_ELEMENT_ALTERNATION})\b(?:\s+({_NUMBER_WORD_ALTERNATION}|\d+)\b)?'
)

_ION_CHARGE_RULE = re.compile(
    rf'\b({_ELEMENT_ALTERNATION})(?:\s+({_NUMBER_WORD_ALTERNATION}|\d+))?\s+(plus|minus)\b',
    re.IGNORECASE,
)

# A "formula-shaped phrase" for the reaction-arrow rule: a known compound
# name. Runs BEFORE subscript formatting, matching the pre-substitution
# spoken shape, and its own replacement already applies known formulas.
_COMPOUND_NAMES = {
    'water': 'H₂O',
    'carbon dioxide': 'CO₂',
    'oxygen': 'O₂',
    'hydrogen': 'H₂',
}
_COMPOUND_ALTERNATION = '|'.join(sorted(_COMPOUND_NAMES, key=len, reverse=True))
_REACTION_VERB_RULE = re.compile(
    rf'\b({_COMPOUND_ALTERNATION})\s+(?:and|plus)\s+({_COMPOUND_ALTERNATION})\s+(?:produces|yields|forms)\s+({_COMPOUND_ALTERNATION})\b',
    re.IGNORECASE,
)


def _digits_for(number_word: str) -> str:
    return number_word if number_word.isdigit() else NUMBER_WORDS.get(number_word.lower(), '')


def _ion_charge_replacement(match: 're.Match') -> str:
    element, number_word, sign_word = match.group(1), match.group(2), match.group(3)
    sign = '⁺' if sign_word.lower() == 'plus' else '⁻'
    if number_word:
        sup = to_superscript(_digits_for(number_word))
        if sup is not None:
            return f'{element}{sup}{sign}'
    return f'{element}{sign}'


def _reaction_replacement(match: 're.Match') -> str:
    left, right, product = match.group(1), match.group(2), match.group(3)

    def formula(name: str) -> str:
        return _COMPOUND_NAMES.get(name.lower(), name)

    return f'{formula(left)} + {formula(right)} → {formula(product)}'


def _format_formula_runs(text: str) -> str:
    """
    Joins a CONTIGUOUS run of element(+count) tokens into one no-space
    formula string ("H two O" -> "H₂O", not "H₂ O") -- matched as whole
    runs rather than one token at a time so inter-element spacing is
    handled correctly instead of leaving the original spoken word-gaps in
    place. A run of length 1 with no count (a single bare element letter,
    e.g. stray "C" in unrelated prose) is deliberately left alone -- not
    formula-shaped enough to touch, matching the ticket's conservatism
    requirement.
    """
    tokens = list(_ELEMENT_TOKEN_RE.finditer(text))
    if not tokens:
        return text

    runs: List[List['re.Match']] = [[tokens[0]]]
    for tok in tokens[1:]:
        prev = runs[-1][-1]
        if text[prev.end():tok.start()].strip() == '':
            runs[-1].append(tok)
        else:
            runs.append([tok])

    result = text
    for run in reversed(runs):  # right-to-left so earlier spans stay valid
        has_number = any(m.group(2) for m in run)
        if len(run) < 2 and not has_number:
            continue
        start, end = run[0].start(), run[-1].end()
        parts = []
        for m in run:
            element, number_word = m.group(1), m.group(2)
            sub = to_subscript(_digits_for(number_word)) if number_word else None
            parts.append(f'{element}{sub}' if sub is not None else element)
        result = result[:start] + ''.join(parts) + result[end:]
    return result


class ChemistryFormatter(AcademicFormatter):
    """See module docstring for the full rule-design rationale."""

    def format_segment(self, text: str) -> Tuple[str, bool]:
        result = text

        # Reaction arrows first -- matches the pre-subscript spoken shape.
        result = _REACTION_VERB_RULE.sub(_reaction_replacement, result)

        # Ion charges before subscript formula runs: "sodium two plus"
        # would otherwise be partially consumed as a formula run first.
        result = _ION_CHARGE_RULE.sub(_ion_charge_replacement, result)

        result = _format_formula_runs(result)

        return result, (result != text)
