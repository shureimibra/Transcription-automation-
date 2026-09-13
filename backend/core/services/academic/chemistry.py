"""
Chemistry notation: element-symbol-plus-spoken-digit -> underscore
subscript ("H two O" -> "H_2O", matching Eagle's own "H_2O" example --
see symbols.py's docstring for why this changed from Unicode), ion charge
suffixes ("Na plus" -> "Na⁺", still Unicode -- see the note on
_ion_superscript below for why that one did NOT change), and
reaction-verb-gated arrow insertion between two recognized compound/element
phrases. Never balances or invents stoichiometry (ticket §8) -- the arrow
rule only ever replaces the spoken verb with "→"; it does not add
coefficients that weren't already present in the transcript.
"""
import re
from typing import List, Tuple

from .base import AcademicFormatter
from .symbols import NUMBER_WORDS, to_subscript

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
    'water': 'H_2O',
    'carbon dioxide': 'CO_2',
    'oxygen': 'O_2',
    'hydrogen': 'H_2',
}
_COMPOUND_ALTERNATION = '|'.join(sorted(_COMPOUND_NAMES, key=len, reverse=True))
_REACTION_VERB_RULE = re.compile(
    rf'\b({_COMPOUND_ALTERNATION})\s+(?:and|plus)\s+({_COMPOUND_ALTERNATION})\s+(?:produces|yields|forms)\s+({_COMPOUND_ALTERNATION})\b',
    re.IGNORECASE,
)


def _digits_for(number_word: str) -> str:
    return number_word if number_word.isdigit() else NUMBER_WORDS.get(number_word.lower(), '')


#: Unicode superscript digits, used ONLY for ion charge notation ("sodium
#: two plus" -> "Na²⁺"). Deliberately NOT symbols.py's to_superscript,
#: which now returns caret notation ("^2") to match Eagle's math examples
#: -- ion charges are not addressed by Eagle's guide in either direction,
#: so this keeps the existing, internally-consistent Unicode ion notation
#: rather than producing a mixed "Na^2⁺" (a caret digit next to a Unicode
#: sign character), which would be worse than either pure form and is not
#: what any evidence asked for.
_ION_SUPERSCRIPT_DIGITS = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
}


def _ion_superscript(digits: str):
    if not digits or not digits.isdigit():
        return None
    return ''.join(_ION_SUPERSCRIPT_DIGITS[d] for d in digits)


def _ion_charge_replacement(match: 're.Match') -> str:
    element, number_word, sign_word = match.group(1), match.group(2), match.group(3)
    sign = '⁺' if sign_word.lower() == 'plus' else '⁻'
    if number_word:
        sup = _ion_superscript(_digits_for(number_word))
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
