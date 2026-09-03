"""
Shared Unicode lookup tables used by mathematics.py/physics.py/chemistry.py.
Deliberately plain data + tiny pure functions, no LaTeX involved anywhere --
every academic formatter emits Unicode directly (see the package docstring
in __init__.py for why).
"""
from typing import Optional

# Spoken number words -> digit string, used both for exponents/subscripts
# ("x squared" doesn't need this, but "H two O" and "integral from zero to
# one" do) and written out for anything not in SUPERSCRIPT/SUBSCRIPT_DIGITS.
NUMBER_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
}

SUPERSCRIPT_DIGITS = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
}

SUBSCRIPT_DIGITS = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
    '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
}

# Lowercase spoken Greek letter names -> Unicode. Deliberately lowercase
# forms only for v1 (ticket's own worked examples never ask for e.g.
# capital Delta as a separate spoken word from lowercase delta -- "Delta"
# said aloud is indistinguishable from "delta" by ASR anyway).
GREEK_LETTERS = {
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'theta': 'θ',
    'lambda': 'λ', 'mu': 'μ', 'sigma': 'σ', 'pi': 'π',
    'delta': 'δ', 'omega': 'ω', 'phi': 'φ', 'epsilon': 'ε',
}


def to_superscript(digits: str) -> Optional[str]:
    """Converts a plain digit string to Unicode superscript digits, or
    returns None if it contains anything else (no general-purpose
    superscript exists in Unicode for arbitrary text -- see mathematics.py's
    module docstring for why this is a real, honestly-documented limit,
    not an oversight)."""
    if not digits or not digits.isdigit():
        return None
    return ''.join(SUPERSCRIPT_DIGITS[d] for d in digits)


def to_subscript(digits: str) -> Optional[str]:
    """Same limitation as to_superscript, for subscripts."""
    if not digits or not digits.isdigit():
        return None
    return ''.join(SUBSCRIPT_DIGITS[d] for d in digits)


def spoken_number_to_digits(word: str) -> Optional[str]:
    """'seven' -> '7'; returns None (never guesses) for anything not in
    NUMBER_WORDS or already a plain digit string."""
    word = word.strip().lower()
    if word.isdigit():
        return word
    return NUMBER_WORDS.get(word)
