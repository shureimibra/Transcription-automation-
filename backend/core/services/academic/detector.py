"""
Lightweight, local, deterministic subject detection -- no model, no LLM
call, a single regex pass over the transcript text. Research comparison
(see the conversation this was designed in): training/shipping a text
classifier for this would need labeled lecture-transcript data this
project doesn't have and adds a model artifact to maintain, for a problem
a weighted keyword/phrase scoring pass already solves at negligible cost
(confirmed: sub-millisecond for a typical transcript's worth of text) --
squarely the same "local, fast, deterministic over ML" choice this
codebase already makes elsewhere (see transcript_formatting.py's paragraph
grouping).

Method: each subject has a dict of {phrase: weight}, weight reflecting how
specific/unambiguous that phrase is to the subject (e.g. "integral"=3 is
almost never ordinary speech; "plus"=1 is common enough on its own that it
shouldn't carry much signal alone). Hits are word-boundary-matched,
case-insensitive, and summed, then normalized into a density (weighted
hits per 100 words) so a short technical aside in a long general lecture
doesn't get diluted into GENERAL, and a long technical lecture doesn't
saturate to 1.0 confidence just from sheer length.
"""
import re
from typing import Dict, Iterable, Tuple

# Below this density (weighted hits per 100 words) -> GENERAL, regardless
# of which subject scored highest -- "don't randomly convert 'rate' into
# an equation" applies to subject detection too, not just formatting.
FLOOR_THRESHOLD = 1.5

# Density at or above which confidence saturates to 1.0.
SATURATION = 8.0

# If the second-highest subject's density is at least this fraction of the
# top subject's, neither one dominates clearly enough to pick alone ->
# STEM_MIXED instead.
MIXED_RATIO_THRESHOLD = 0.6

MATH_TERMS: Dict[str, float] = {
    'integral': 3, 'derivative': 3, 'summation': 3, 'infinity': 2,
    'square root': 3, 'squared': 1.5, 'cubed': 1.5, 'quadratic': 3,
    'equation': 1, 'variable': 1.5, 'coefficient': 2.5, 'theorem': 2.5,
    'sine': 2, 'cosine': 2, 'tangent': 2, 'logarithm': 3, 'exponent': 2,
    'algebra': 2.5, 'calculus': 3, 'geometry': 2, 'matrix': 2.5,
    'fraction': 1.5, 'divided by': 1, 'multiplied by': 1,
    'pi': 1.5, 'theta': 2, 'alpha': 1, 'beta': 1, 'lambda': 1.5, 'sigma': 1.5,
}

PHYSICS_TERMS: Dict[str, float] = {
    'force': 1.5, 'velocity': 2.5, 'acceleration': 2.5, 'momentum': 2.5,
    'newton': 2.5, 'joule': 2.5, 'watt': 2, 'voltage': 2, 'ampere': 2.5,
    'gravity': 1.5, 'friction': 1.5, 'kinetic energy': 3, 'potential energy': 3,
    'electric field': 3, 'magnetic field': 3, 'wavelength': 2.5, 'frequency': 1.5,
    'mass': 1, 'kilogram': 2, 'meters per second': 3, 'circuit': 1.5, 'resistor': 2.5,
}

CHEMISTRY_TERMS: Dict[str, float] = {
    'molecule': 2.5, 'compound': 2, 'reaction': 1.5, 'chemical': 1.5,
    'element': 1.5, 'atom': 2, 'ion': 2.5, 'bond': 1.5, 'solution': 1,
    'acid': 1.5, 'base': 1, 'catalyst': 3, 'oxidation': 3, 'reduction': 1.5,
    'periodic table': 3, 'valence': 3, 'isotope': 3, 'molar': 2.5,
    'hydrogen': 1.5, 'oxygen': 1.5, 'carbon dioxide': 2.5, 'sodium': 1.5, 'chlorine': 1.5,
}


def _score(text: str, terms: Dict[str, float]) -> float:
    total = 0.0
    for phrase, weight in terms.items():
        hits = len(re.findall(r'\b' + re.escape(phrase) + r'\b', text))
        total += hits * weight
    return total


def _get_text(seg) -> str:
    return seg.get('text') if isinstance(seg, dict) else getattr(seg, 'text', '')


class SubjectDetector:
    @staticmethod
    def detect(segments: Iterable) -> Tuple[str, float]:
        text = ' '.join((_get_text(seg) or '') for seg in segments).lower()
        word_count = max(1, len(text.split()))

        densities = {
            'MATHEMATICS': _score(text, MATH_TERMS) / word_count * 100,
            'PHYSICS': _score(text, PHYSICS_TERMS) / word_count * 100,
            'CHEMISTRY': _score(text, CHEMISTRY_TERMS) / word_count * 100,
        }

        ranked = sorted(densities.items(), key=lambda kv: kv[1], reverse=True)
        top_subject, top_density = ranked[0]

        if top_density < FLOOR_THRESHOLD:
            confidence = round(1.0 - min(top_density / FLOOR_THRESHOLD, 1.0), 2)
            return 'GENERAL', confidence

        second_density = ranked[1][1] if len(ranked) > 1 else 0.0
        if second_density >= top_density * MIXED_RATIO_THRESHOLD:
            confidence = round(min(top_density / SATURATION, 1.0), 2)
            return 'STEM_MIXED', confidence

        confidence = round(min(top_density / SATURATION, 1.0), 2)
        return top_subject, confidence
