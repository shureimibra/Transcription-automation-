"""
Physics notation: reuses MathematicsFormatter's rules wholesale (a spoken
physics equation is fundamentally the same "spoken equation" shape as pure
math -- "F equals m a" and "x equals y" are the same grammar), then adds
physics-specific equation idioms and unit-symbol substitution on top.

Unit substitution is deliberately gated on a PRECEDING NUMBER token only
("5 meters" -> "5 m") -- never a bare unit word in prose ("mass is measured
in kilograms" stays completely unchanged, matching the ticket's own
explicit example) -- since "the room was cold" contains no digit-adjacent
unit word for any rule to even consider.
"""
import re
from typing import Tuple

from .mathematics import MathematicsFormatter
from .symbols import NUMBER_WORDS

_EQUATION_IDIOMS = [
    (re.compile(r'\bF equals m a\b', re.IGNORECASE), 'F = ma'),
    (re.compile(r'\bv equals u plus a t\b', re.IGNORECASE), 'v = u + at'),
    (re.compile(r'\bE equals m c squared\b', re.IGNORECASE), 'E = mc²'),
    (re.compile(r'\bP equals V I\b', re.IGNORECASE), 'P = VI'),
]

# unit name -> symbol, matched only when immediately preceded by a digit or
# a spoken number word (see module docstring).
_UNIT_SYMBOLS = {
    'meters': 'm', 'meter': 'm',
    'seconds': 's', 'second': 's',
    'kilograms': 'kg', 'kilogram': 'kg',
    'newtons': 'N', 'newton': 'N',
    'joules': 'J', 'joule': 'J',
    'watts': 'W', 'watt': 'W',
    'volts': 'V', 'volt': 'V',
    'amperes': 'A', 'ampere': 'A',
    'degrees celsius': '°C',
}

_NUMBER_TOKEN = r'(?:\d+(?:\.\d+)?|' + '|'.join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r')'
_UNIT_ALTERNATION = '|'.join(sorted(_UNIT_SYMBOLS, key=len, reverse=True))
_UNIT_RULE = re.compile(rf'\b({_NUMBER_TOKEN})\s+({_UNIT_ALTERNATION})\b', re.IGNORECASE)


def _unit_replacement(match: 're.Match') -> str:
    number, unit = match.group(1), match.group(2)
    return f'{number} {_UNIT_SYMBOLS[unit.lower()]}'


class PhysicsFormatter(MathematicsFormatter):
    """Physics-specific rules run BEFORE the inherited math rules (equation
    idioms are the most specific match available), then units run last."""

    def format_segment(self, text: str) -> Tuple[str, bool]:
        result = text
        for pattern, replacement in _EQUATION_IDIOMS:
            result = pattern.sub(replacement, result)

        # Inherited math rules (operators, powers, Greek letters, ...).
        result, _ = super().format_segment(result)

        result = _UNIT_RULE.sub(_unit_replacement, result)

        return result, (result != text)
