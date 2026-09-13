"""
Shared lookup tables used by mathematics.py/physics.py/chemistry.py.

PLAIN-ASCII NOTATION, NOT UNICODE -- changed 2026-09-13 to match Eagle's
General Guidelines, which is now the policy baseline for this codebase.
This module used to emit real Unicode superscript/subscript characters
and Unicode Greek letters (x -> x², H2O -> H₂O, pi -> π), on the reasoning
that "no LaTeX involved anywhere" meant Unicode was the natural plain-text
choice. It wasn't evidenced against what Eagle actually asks for, and the
guideline's own worked examples are explicit and go the other way:
"x^4", "y_2", "H_2O", and Greek letters spelled as capitalized words
("Alpha", "Pi", "Sigma") -- caret, underscore and plain word, never a
Unicode symbol. See docs/ (the compliance and evidence audits this change
is based on) for the full reasoning.
"""
from typing import Optional

# Spoken number words -> digit string, used both for exponents/subscripts
# ("x squared" doesn't need this, but "H two O" and "integral from zero to
# one" do) and for calculus-phrase limits not already a plain digit.
NUMBER_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
}

# Capitalized spoken Greek letter names, kept as words -- Eagle's own
# example table lists "Alpha | Beta | Sigma | Mu | Epsilon | Pi | Tau" as
# the required form, not the Unicode character. Capitalization matches
# that table; nothing here converts to a symbol.
GREEK_LETTERS = {
    'alpha': 'Alpha', 'beta': 'Beta', 'gamma': 'Gamma', 'theta': 'Theta',
    'lambda': 'Lambda', 'mu': 'Mu', 'sigma': 'Sigma', 'pi': 'Pi',
    'delta': 'Delta', 'omega': 'Omega', 'phi': 'Phi', 'epsilon': 'Epsilon',
}


def to_superscript(digits: str) -> Optional[str]:
    """Converts a plain digit string to caret notation ("2" -> "^2"), or
    returns None if it contains anything else. Eagle's own example is
    "x^4" -- a caret, not a Unicode superscript character -- so this no
    longer tries to render a true superscript at all; the caret is the
    correct form, not a fallback for one."""
    if not digits or not digits.isdigit():
        return None
    return '^' + digits


def to_subscript(digits: str) -> Optional[str]:
    """Same shape as to_superscript, for subscripts -- Eagle's own
    example is "H_2O": an underscore, not a Unicode subscript character."""
    if not digits or not digits.isdigit():
        return None
    return '_' + digits


def spoken_number_to_digits(word: str) -> Optional[str]:
    """'seven' -> '7'; returns None (never guesses) for anything not in
    NUMBER_WORDS or already a plain digit string."""
    word = word.strip().lower()
    if word.isdigit():
        return word
    return NUMBER_WORDS.get(word)
