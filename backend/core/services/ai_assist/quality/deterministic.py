"""
The checks that must NOT be delegated to a language model (RULE 4, spec 19).

Everything here answers a question with exactly one correct answer that code
settles cheaply and exactly: did every number survive, did the URLs come
through intact, is the output suspiciously short, did the model leave
commentary in the deliverable. An LLM asked "are all the numbers the same?"
returns a plausible opinion and misses the one that matters; a multiset
comparison cannot miss it.

These findings are also the ones a repair loop is least entitled to argue
with. `detector='deterministic'` travels on every issue this module raises
so the audit prompt can be told which findings are facts, and so a
re-audit that "resolves" a numeric mismatch without the number changing is
visible as the contradiction it is.

LOCALE-AWARE NUMBER COMPARISON is the subtle part. "3.14" translated into
French is correctly "3,14", and a naive string diff reports a defect on a
perfect translation while still missing "10 mg" becoming "100 mg". So
numbers are parsed to values, not compared as text -- see `extract_numbers`.
"""
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple

from .issues import IssueCategory, QualityIssue, Severity
from .typography import check_typography

# ---------------------------------------------------------------------- #
# Numbers
# ---------------------------------------------------------------------- #

# A number possibly carrying grouping separators and a decimal part, in
# either the anglophone (1,234.56) or francophone (1 234,56) convention.
#
# The space-grouped alternative comes FIRST and demands groups of exactly
# three digits. Without that constraint a plain space would join any two
# adjacent numbers -- "10 20" would parse as 1020 -- and a list of figures
# in a transcript would report a fabricated mismatch on every line.
#
# Deliberately does not capture a sign: a leading '-' is far more often a
# dash or a hyphen in transcript text than a negative quantity, and a false
# "minus lost" finding on every dashed clause would drown the real ones.
_NUMBER_RE = re.compile(
    r'\d{1,3}(?:[   ]\d{3})+(?:[,.]\d+)?'
    r'|\d+(?:[,.]\d+)*'
)

# Groups of exactly three digits, which is what a thousands separator
# separates. Used to tell "1,234" (grouping) from "1,23" (a decimal comma).
_GROUPED_RE = re.compile(r'^\d{1,3}(?:[   ,.]\d{3})+$')


def _to_decimal(token: str) -> Optional[Decimal]:
    """
    Parses one numeric token under either convention, or returns None.

    The ambiguous case is a single separator with three digits after it:
    "1,234" is one thousand two hundred thirty-four in English and could be
    1.234 in French. It is resolved as GROUPING, because thousands are
    vastly more common in prose than a three-decimal fraction -- and because
    guessing wrong in this direction produces a false match (two readings
    that agree) rather than a false alarm.
    """
    cleaned = token.strip()
    if not cleaned:
        return None
    if _GROUPED_RE.match(cleaned):
        # Pure grouping: every separator is a thousands mark.
        digits = re.sub(r'[   ,.]', '', cleaned)
        try:
            return Decimal(digits)
        except InvalidOperation:
            return None

    # Otherwise the LAST separator, if any, is the decimal mark and anything
    # before it is grouping.
    match = re.search(r'[,.](\d+)$', cleaned)
    if match:
        integer_part = re.sub(r'[   ,.]', '', cleaned[:match.start()])
        fraction = match.group(1)
        raw = f'{integer_part or "0"}.{fraction}'
    else:
        raw = re.sub(r'[   ,.]', '', cleaned)
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


#: A decade written the English way: 1850s, 1900s. The trailing "s" is not
#: part of the value, and a parser that refuses the whole token reports the
#: year as missing from a translation that rendered it perfectly well as
#: "les annees 1850".
_DECADE_RE = re.compile(r'\b(\d{3,4})s\b')


def extract_numbers(text: str) -> List[Decimal]:
    """Every number in `text` as a value, order preserved. Trailing grouping
    characters are trimmed so "in 2024, the" does not parse as "2024,"."""
    values = []
    # Decades first: "the 1850s" carries the value 1850, and a source and a
    # translation will never agree on the plural.
    for match in _DECADE_RE.finditer(text or ''):
        value = _to_decimal(match.group(1))
        if value is not None:
            values.append(value)
    for match in _NUMBER_RE.finditer(text or ''):
        token = match.group(0).rstrip(' ,.  ')
        value = _to_decimal(token)
        if value is not None:
            values.append(value)
    return values


def _multiset(values: Sequence) -> Dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _multiset_diff(left: Dict, right: Dict) -> Tuple[List, List]:
    """(only-in-left, only-in-right), each expanded by multiplicity so a
    number that appears twice in the source and once in the target is
    reported rather than cancelling out."""
    missing, added = [], []
    for value, count in left.items():
        delta = count - right.get(value, 0)
        if delta > 0:
            missing.extend([value] * delta)
    for value, count in right.items():
        delta = count - left.get(value, 0)
        if delta > 0:
            added.extend([value] * delta)
    return missing, added


def _display(value: Decimal) -> str:
    """
    A number, the way the human reading this finding expects to see it.

    Decimal.normalize() strips trailing zeros -- needed so "30" and "30.0"
    are recognized as the same value -- but for a whole number that means a
    coefficient of 3 and a positive exponent, and Decimal's own __str__
    switches to scientific notation exactly when the exponent is positive.
    A genuinely missing 30 was reported as the baffling "3E+1" (and 100 as
    "1E+2", 500 as "5E+2"...), which reads as a garbled number rather than
    the round one it actually is. `to_integral_value()` does not fix this --
    an already-integral Decimal is returned unchanged, exponent and all;
    only the 'f' (fixed-point) format spec forces non-scientific notation
    unconditionally, for any value, which is why that is what this uses
    rather than reasoning about which values need it.
    """
    return format(value.normalize(), 'f')


def _format_values(values, limit: int = 6) -> str:
    shown = ', '.join(_display(value) for value in values[:limit])
    return shown + ('...' if len(values) > limit else '')


# --------------------------------------------------------------------- #
# Numbers that were written as words
# --------------------------------------------------------------------- #

#: Small cardinals in the languages this platform actually moves between.
#: Only 0-20 and the round tens: beyond that, spelled-out numbers are rare
#: in speech and the parsing ("quatre-vingt-dix-sept") stops being worth it.
_NUMBER_WORDS = {
    # English
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
    'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    'hundred': 100, 'thousand': 1000, 'million': 1000000,
    'billion': 1000000000,
    # "trillion" WAS MISSING. A real submission spoke "75 trillion" /
    # "70 trillion" (US long-term fiscal figures) and had both flagged
    # CRITICAL, in both directions -- see _DIGIT_SCALE_RE and
    # _forgive_cross_scale_notation below for the fuller story.
    'trillion': 1000000000000,
    # French
    'zéro': 0, 'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4,
    'cinq': 5, 'sept': 7, 'huit': 8, 'neuf': 9, 'dix': 10, 'onze': 11,
    'douze': 12, 'treize': 13, 'quatorze': 14, 'quinze': 15, 'seize': 16,
    'vingt': 20, 'trente': 30, 'quarante': 40, 'cinquante': 50,
    'soixante': 60, 'cent': 100, 'mille': 1000,
    # 'milliard' IS NOT 'billion': French for 10^9 is milliard, not the
    # false-friend "billion" (which in French traditionally means 10^12).
    # The same real submission above translated "75/70 trillion" as
    # "75 000/70 000 milliards" -- the correct, idiomatic French way to
    # say a number this size -- and both forms are needed: the singular
    # is vanishingly rare in real prose (a coefficient of exactly one
    # milliard), but costs nothing to also recognise.
    'milliard': 1000000000, 'milliards': 1000000000,
}
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+", re.UNICODE)


#: A French ordinal in Roman numerals: XIXe, XXe, IIIe. This is how French
#: writes centuries -- "le XIXe siecle" for "the 19th century" -- so a
#: value-for-value comparison sees 19 vanish from every translation that
#: followed the convention correctly.
#:
#: Only the ORDINAL form is matched (Roman numeral immediately followed by
#: e/er/eme). A bare "I" or "X" in running text is a letter, and treating it
#: as a number would forgive real defects by accident.
_ROMAN_ORDINAL_RE = re.compile(r'\b([IVXLCDM]{1,7})(?:e|er|ere|eme|ème)\b')
_ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}

#: "two and a half million" = 2,500,000 exactly; "one and a quarter
#: million" = 1,250,000 exactly. A real submission had both flagged as
#: BLOCKING invented numbers, because neither digit string appears
#: anywhere in the source -- the source spells the whole compound out as
#: words, and the plain word-by-word scan above only ever catches "two",
#: "million" and "one" separately, never the combination.
#:
#: Restricted to halves and quarters specifically, and to that exact
#: shape ("<count> and a <fraction> <scale>"). Unlike "two thirds"
#: (0.666...), a half and a quarter are EXACT binary fractions, so
#: converting this phrase into a single figure invents no precision at
#: all -- which is precisely the distinction the "SAME FORM AND THE SAME
#: PRECISION" worker-prompt rule draws (born from "16 and 2 thirds
#: percent" becoming the invented "16,67"). That rule is about a
#: fraction that CANNOT be carried into a figure exactly; it was never
#: meant to catch one that can. Not extended to the bare "half a
#: million" / "a quarter million" forms (different word order, and not
#: what this document actually used) -- add that shape if a real
#: submission ever needs it.
_HALF_QUARTER_SCALE_RE = re.compile(
    r'\b(' + '|'.join(_NUMBER_WORDS) + r')\s+and\s+a\s+'
    r'(half|quarter)\s+(hundred|thousand|million|billion)\b',
    re.IGNORECASE,
)
_FRACTIONS = {'half': Decimal('0.5'), 'quarter': Decimal('0.25')}

#: "$360 million" = 360000000. A real submission had "360" (the source's
#: literal digits) reported missing and "360000000" (the target's, once
#: written out in full as "360 000 000") reported invented, because
#: extract_numbers only ever sees the bare digit token -- it has no idea
#: a bare scale WORD sits right next to it. Broader than
#: _HALF_QUARTER_SCALE_RE above: that one exists for a fraction spelled
#: entirely in words ("two and a half million"); this one is for a digit
#: that already IS a figure, riding on one scale word ("$360 million",
#: "12 thousand", "1.5 billion"). Both directions are exact multiplication
#: by a round power of ten -- there is no precision question here the way
#: there is with thirds, so nothing is invented by combining them.
_DIGIT_SCALE_RE = re.compile(
    r'([\d][\d,. ]*)\s*(hundred|thousand|million|billion|trillion'
    r'|milliard|milliards)\b',
    re.IGNORECASE,
)

#: French approximate-count nouns: "une vingtaine" (about twenty), "une
#: trentaine" (about thirty), and so on. These are how French idiomatically
#: renders an approximate English count ("a dozen", "a score of") and carry
#: the same base value as the cardinal they are built from -- they are not
#: exact synonyms of it, but forgiving them the same way spelled-out
#: cardinals are already forgiven is the right call for the same reason:
#: this set is used only to excuse an apparent mismatch, never to accuse.
_FRENCH_APPROX_COUNT_NOUNS = {
    'huitaine': 8, 'dizaine': 10, 'douzaine': 12, 'quinzaine': 15,
    'vingtaine': 20, 'trentaine': 30, 'quarantaine': 40,
    'cinquantaine': 50, 'soixantaine': 60, 'centaine': 100,
}

#: "the 1960s and 70s": the second decade is written as a bare two digits,
#: inheriting its century from the first. A translator who correctly
#: expands it to "1970" produces a value (1970) that never appeared in the
#: source's literal digits (which only ever spell "70"), and the source's
#: bare "70" then never appears in a target that expanded it -- so the
#: SAME correct translation was reported as both an invented 1970 and a
#: dropped 70, in both directions, on a real submission. Matched only in
#: the specific "<decade>s and/et <two digits>s" shape: outside a list of
#: decades, a bare two-digit number has no century to inherit and this
#: must not guess one.
_ELIDED_DECADE_RE = re.compile(
    r'\b(\d{3})0s\b\s*(?:,\s*)?(?:and|et)\s+(\d{2})s\b', re.IGNORECASE,
)

#: "Edward VI", "Louis XIV": a monarch's ordinal, bare, with none of the
#: French "e/er/eme" suffix _ROMAN_ORDINAL_RE demands -- that suffix is how
#: French spells a CENTURY ("le XIXe siecle"), not how either language
#: numbers a monarch, which is written as a plain trailing numeral in both.
#: A real submission had "Edward VI" reported as a dropped 6 because the
#: source's only spelling of the value is this bare roman numeral. Anchored
#: to "capitalized word immediately followed by the numeral" so a random
#: capital "I" is not read as one -- a name has to precede it.
_MONARCH_ORDINAL_RE = re.compile(r"\b[A-ZÀ-Þ][\wà-ÿ'’-]+\s+([IVXLCDM]{1,7})\b")


def _roman_value(token: str):
    """Classical Roman numeral to int, or None if it is not one."""
    total, previous = 0, 0
    for character in reversed(token.upper()):
        value = _ROMAN_VALUES.get(character)
        if value is None:
            return None
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total or None


def spelled_number_values(text: str) -> set:
    """
    Values written as words rather than digits.

    USED ONLY TO FORGIVE, NEVER TO ACCUSE. A speaker says "little green man
    one" and a translator writes "little green man 1" -- the value is
    identical and nothing was invented, but a digit-only comparison calls
    that an invented quantity and blocks the submission. Since `numeric` is
    the one CRITICAL check with no override, that false positive stops a
    worker submitting perfect work.

    It is deliberately NOT used on the missing-from-target side. This list
    over-generates -- French "un" and "une" are far more often the
    indefinite article than the number -- and an over-generating source list
    would manufacture numbers the source never stated and then report them
    as dropped. Forgiving an addition is safe in a way that inventing an
    omission is not.
    """
    values = set()
    for word in _WORD_RE.findall((text or '').lower()):
        if word in _NUMBER_WORDS:
            values.add(Decimal(_NUMBER_WORDS[word]))
        elif word in _FRENCH_APPROX_COUNT_NOUNS:
            values.add(Decimal(_FRENCH_APPROX_COUNT_NOUNS[word]))
    # Roman ordinals, which is how French writes a century.
    for match in _ROMAN_ORDINAL_RE.finditer(text or ''):
        value = _roman_value(match.group(1))
        if value is not None:
            values.add(Decimal(value))
    # "Edward VI", "Louis XIV" -- a bare monarch ordinal, not a century.
    # See _MONARCH_ORDINAL_RE.
    for match in _MONARCH_ORDINAL_RE.finditer(text or ''):
        value = _roman_value(match.group(1))
        if value is not None:
            values.add(Decimal(value))
    # "two and a half million" -> 2500000, "one and a quarter million" ->
    # 1250000. See _HALF_QUARTER_SCALE_RE for why only these two fractions.
    for match in _HALF_QUARTER_SCALE_RE.finditer(text or ''):
        count_word, fraction_word, scale_word = (g.lower() for g in match.groups())
        whole = Decimal(_NUMBER_WORDS[count_word])
        scale = Decimal(_NUMBER_WORDS[scale_word])
        values.add((whole + _FRACTIONS[fraction_word]) * scale)
    # "$360 million" -> 360000000, digit riding on a bare scale word. See
    # _DIGIT_SCALE_RE.
    for match in _DIGIT_SCALE_RE.finditer(text or ''):
        digits, scale_word = match.group(1), match.group(2).lower()
        count = _to_decimal(digits)
        if count is not None:
            values.add(count * Decimal(_NUMBER_WORDS[scale_word]))
    # "the 1960s and 70s" -> also forgive 1970 (what a correct expansion
    # writes) and 70 (what the source literally spells). See
    # _ELIDED_DECADE_RE.
    for match in _ELIDED_DECADE_RE.finditer(text or ''):
        first_decade = int(match.group(1)) * 10
        century = (first_decade // 100) * 100
        second_digits = int(match.group(2))
        values.add(Decimal(century + second_digits))
        values.add(Decimal(second_digits))
    return values


def _forgive_hour_conversions(
    missing: List[Decimal], added: List[Decimal],
) -> Tuple[List[Decimal], List[Decimal]]:
    """
    5pm -> 17 heures, 2pm -> 14 heures: a correct 12h-to-24h conversion, not
    a changed quantity. English commonly states a time as "5pm"; formal
    French writes the same moment as "17 heures". The two notations share
    no digits, so a literal value comparison read the conversion as the
    source's 5 going missing and the target's 17 being invented -- a real
    submission was blocked over lecture start times for exactly this
    reason.

    BOUNDED TO PLAUSIBLE HOURS, not "anything 12 apart": 12 is also "a
    dozen", and forgiving every pair that happens to differ by 12 would let
    a real defect ("12 patients" quietly became "24 patients") through as
    a false time conversion. Only values in the ranges an hour can actually
    take -- 1-11 for the 12-hour form, 13-23 for the 24-hour one -- are
    eligible, and each pairing is consumed once, so a genuine surplus
    number past the pairing is still reported.
    """
    remaining_missing = list(missing)
    remaining_added = list(added)
    for value in list(remaining_missing):
        if not (1 <= value <= 11):
            continue
        pair = value + 12
        if pair in remaining_added:
            remaining_missing.remove(value)
            remaining_added.remove(pair)
    return remaining_missing, remaining_added


def _forgive_digit_scale_expansions(
    source: str, missing: List[Decimal], target_values: set,
) -> List[Decimal]:
    """
    "$360 million" in the source, correctly written out in full as
    "360 000 000" in the target, merges into a SINGLE grouped number under
    extract_numbers -- there is no separate "360" token left in the target
    to match against, because writing it out in full was the translator's
    job. The source's bare 360 then reads as a dropped quantity even
    though nothing was dropped.

    _DIGIT_SCALE_RE (used in spelled_number_values, above) already forgives
    the other direction -- the fully-expanded value being reported as
    INVENTED, since it is not literally present in the source's digits.
    This is the missing half of the same real bug: the source's bare count
    also has to stop reading as missing, but only once, and only when the
    target actually contains the expanded value -- proof the translator
    legitimately expanded it, not a blanket exemption for every small
    number that happens to precede a scale word.
    """
    remaining = list(missing)
    for match in _DIGIT_SCALE_RE.finditer(source or ''):
        base = _to_decimal(match.group(1))
        if base is None or base not in remaining:
            continue
        scale = Decimal(_NUMBER_WORDS[match.group(2).lower()])
        if base * scale in target_values:
            remaining.remove(base)
    return remaining


def _forgive_elided_decade_expansions(
    source: str, missing: List[Decimal], target_values: set,
) -> List[Decimal]:
    """
    The missing half of the same asymmetry _forgive_digit_scale_expansions
    exists for, on "the 1960s and 70s". spelled_number_values(source)
    already forgives the target's 1970 from being reported as INVENTED
    (see _ELIDED_DECADE_RE): the source itself proves 1970 is what "70s"
    means here. But the source's bare 70 still reads as dropped, because
    a target that correctly expanded it to "1970" contains no literal 70
    of its own for the multiset diff to find.

    Only forgiven when the target actually contains the expanded year --
    the same proof-of-legitimate-expansion guard as the digit-scale case,
    not a blanket exemption for every bare two-digit number that happens
    to follow a decade.
    """
    remaining = list(missing)
    for match in _ELIDED_DECADE_RE.finditer(source or ''):
        first_decade = int(match.group(1)) * 10
        century = (first_decade // 100) * 100
        second_digits = Decimal(int(match.group(2)))
        expanded = Decimal(century) + second_digits
        if second_digits in remaining and expanded in target_values:
            remaining.remove(second_digits)
    return remaining


def _true_scale_values(text: str) -> set:
    """Every "<digits> <scale word>" phrase in `text`, resolved to the
    real value it names -- on EITHER language's text, unlike
    spelled_number_values's use of the same _DIGIT_SCALE_RE, which is
    only ever called on the source. See _forgive_cross_scale_notation,
    the one caller that needs both sides resolved the same way."""
    values = set()
    for match in _DIGIT_SCALE_RE.finditer(text or ''):
        base = _to_decimal(match.group(1))
        scale = _NUMBER_WORDS.get(match.group(2).lower())
        if base is not None and scale is not None:
            values.add(base * Decimal(scale))
    return values


def _forgive_cross_scale_notation(
    source: str, target: str,
    missing: List[Decimal], added: List[Decimal],
) -> Tuple[List[Decimal], List[Decimal]]:
    """
    "75 trillion" in the source, "75 000 milliards" in the target -- the
    same quantity, each written the way its own language actually says a
    number this size. FROM A REAL SUBMISSION: US long-term fiscal
    figures, "75/70 trillion" translated as "75 000/70 000 milliards",
    correctly. It was blocked CRITICAL in both directions: the source's
    bare 70 read as dropped (the target has no literal "70" left, only
    "70 000"), and the target's 75000/70000 read as invented (neither
    figure is literally in the source's digits, only "75"/"70").

    _forgive_digit_scale_expansions/spelled_number_values already forgive
    the SIBLING shape -- a scale word surviving in the source only, fully
    written out as bare digits in the target ("$360 million" ->
    "360 000 000", no scale word left). This is the case neither of those
    covers: a scale word survives on BOTH sides, just a different one
    with a compensating coefficient, so the bare digit tokens never
    match on their own.

    Forgiven only when the TRUE VALUES the two phrases resolve to are
    actually equal -- proof of a correct conversion between two
    languages' conventions for a large number, not a blanket exemption
    for any digit that happens to sit next to a scale word.
    """
    shared = _true_scale_values(source) & _true_scale_values(target)
    if not shared:
        return missing, added
    remaining_missing, remaining_added = list(missing), list(added)
    for text, remaining in ((source, remaining_missing), (target, remaining_added)):
        for match in _DIGIT_SCALE_RE.finditer(text or ''):
            base = _to_decimal(match.group(1))
            scale = _NUMBER_WORDS.get(match.group(2).lower())
            if base is None or scale is None:
                continue
            if base * Decimal(scale) in shared and base in remaining:
                remaining.remove(base)
    return remaining_missing, remaining_added


def check_numbers(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    Every number in the source must appear in the target and vice versa.

    CRITICAL severity, unconditionally. This is the "10 mg becomes 100 mg"
    class, and there is no domain in which silently changing a quantity is
    a minor issue -- a dose, a monetary amount, a statute number and a
    tolerance all fail the same way.
    """
    source_values = _multiset(extract_numbers(source))
    target_values = _multiset(extract_numbers(target))
    missing, added = _multiset_diff(source_values, target_values)

    # A figure the source spelled out is not an invented figure. See
    # spelled_number_values: this forgives, it never accuses.
    # A figure the source spelled out is not an invented figure, and a figure
    # the TARGET spelled out is not a missing one. Both directions forgive;
    # neither manufactures a defect.
    #
    # The second half was absent and it blocked a real submission: English
    # "20 minutes", French "vingt minutes", reported as a dropped number.
    # Same value, different notation -- which is the translator's job.
    if added:
        added = [v for v in added if v not in spelled_number_values(source)]
    if missing:
        missing = [v for v in missing if v not in spelled_number_values(target)]

    # MULTIPLICITY IS NOT MEANINGFUL ACROSS A TRANSLATION. A source saying
    # "1850" four times and a translation saying it three, having merged two
    # sentences, has dropped no fact. What matters is whether a value appears
    # at all -- a changed or invented figure, not a recount.
    source_set = set(extract_numbers(source))
    target_set = set(extract_numbers(target))
    missing = [v for v in dict.fromkeys(missing) if v not in target_set]
    added = [v for v in dict.fromkeys(added) if v not in source_set]

    missing, added = _forgive_hour_conversions(missing, added)
    # "$360 million" -> the source's bare 360 is not a dropped quantity
    # once the target legitimately writes it out in full as 360 000 000.
    # See _forgive_digit_scale_expansions.
    missing = _forgive_digit_scale_expansions(source, missing, target_set)
    # "the 1960s and 70s" -> the source's bare 70 is not a dropped
    # quantity once the target legitimately expands it to 1970. See
    # _forgive_elided_decade_expansions.
    missing = _forgive_elided_decade_expansions(source, missing, target_set)
    # "75 trillion" -> "75 000 milliards" -- a scale word survives on
    # BOTH sides, with a different, compensating coefficient. See
    # _forgive_cross_scale_notation.
    missing, added = _forgive_cross_scale_notation(source, target, missing, added)

    issues = []
    if missing:
        issues.append(QualityIssue(
            category=IssueCategory.NUMERIC, severity=Severity.CRITICAL,
            problem=(
                'Number(s) present in the source are absent from the output: '
                + _format_values(missing) + '.'
            ),
            correction='Restore every quantity exactly as the source states it.',
            source_span=_format_values(missing),
            rule='numeric.missing-from-target',
            segment_index=segment_index, detector='deterministic',
        ))
    if added:
        issues.append(QualityIssue(
            category=IssueCategory.NUMERIC, severity=Severity.CRITICAL,
            problem=(
                'Number(s) in the output do not appear in the source: '
                + _format_values(added) + '.'
            ),
            correction=(
                'Remove invented quantities, or correct one that was mistyped.'
            ),
            target_span=_format_values(added),
            rule='numeric.absent-from-source',
            segment_index=segment_index, detector='deterministic',
        ))
    return issues


# ---------------------------------------------------------------------- #
# Verbatim tokens: URLs, e-mail, identifiers
# ---------------------------------------------------------------------- #

#: Closed-class words a title may contain between its capitalized content
#: words -- "The Mind of the South", "Trouble the Water", "Ideas That Have
#: Shaped Our Country". Titles use ordinary English syntax, so this has to
#: cover articles, prepositions and conjunctions AND the relative/possessive
#: words a subtitle clause commonly needs. "that" was missing: a real title
#: ("...Ideas that have shaped our country") fell through this list's gap
#: and reported "that" as a foreign word surviving in a French translation,
#: when it was the correctly-untranslated title doing exactly what a title
#: is supposed to do. Shared with check_source_leakage's title exemption
#: below, so both checks agree on what a title looks like.
_TITLE_CONNECTORS = frozenset({
    'of', 'the', 'and', 'or', 'nor', 'but', 'in', 'on', 'at', 'by', 'as',
    'a', 'an', 'to', 'for', 'from', 'with', 'that', 'which', 'who', 'whom',
    'whose', 'our', 'your', 'their', 'its', 'his', 'her', 'is', 'are',
})

_TITLE_RUN_RE = re.compile(
    r"\b[A-Z][\w'’-]+"
    r"(?:\s+(?:[A-Z][\w'’-]+|" + '|'.join(sorted(_TITLE_CONNECTORS)) + r"))*"
    r"\s+[A-Z][\w'’-]+"
)


def _looks_like_a_title(phrase: str) -> bool:
    """
    True when `phrase` reads as title-case rather than ordinary prose --
    available for check_source_leakage, which needs to ask this of an
    already-located run rather than search for one the way _TITLE_RUN_RE
    does.

    ANCHORED ON SEVERAL DISTINCT CAPITALS, NOT "every word accounted for",
    AND FOUND BY SLIDING, NOT JUDGED OVER THE WHOLE TEXT. A model does not
    always apply full title-case to a title it renders mid-sentence --
    measured on the real case this exists for, only 5 of 13 words in a
    genuine 14-word book title were capitalized at all ("The Promise of
    Canada, 150 years, People and Ideas that have shaped our country";
    "years", "have", "shaped" and "country" stayed lowercase). And a real
    segment usually frames the title in ordinary prose around it ("her
    recent book is titled ... you know"), which dilutes any ratio measured
    over the WHOLE segment below a title's own local density even though
    the title itself, read in isolation, is unambiguous. So this slides an
    8-word window (or the whole phrase, if shorter) and asks only whether
    SOME window inside the text is title-dense -- not whether the text is,
    on average. An ordinary skipped SENTENCE -- the real leak this exists
    to catch -- has at most its own scattered proper nouns, and this
    codebase's own content is proper-noun-heavy (case names, institutions),
    so both thresholds matter together within one window: few 8-word
    stretches combine three-plus capitals with half the window accounted
    for by accident.
    """
    words = re.findall(r"[A-Za-z][\w'’-]*", phrase or '')
    if len(words) < 2:
        return False
    flags = [
        word[0].isupper() or word.lower() in _TITLE_CONNECTORS
        for word in words
    ]
    caps = [word[0].isupper() for word in words]
    window = min(8, len(words))
    for start in range(0, len(words) - window + 1):
        if (sum(caps[start:start + window]) >= 3
                and sum(flags[start:start + window]) / window >= 0.5):
            return True
    return False

_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+', re.IGNORECASE)
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
# An identifier-shaped token: letters and digits mixed, or a dotted/hyphenated
# code. Catches article references, part numbers, gene symbols, case numbers.
_IDENTIFIER_RE = re.compile(r'\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*\b')


def _check_verbatim(
    source: str, target: str, pattern, label: str, rule: str,
    severity: Severity, segment_index: Optional[int],
) -> List[QualityIssue]:
    # A DECADE IS NOT AN IDENTIFIER CODE. "1780s" matches the identifier
    # shape -- digits plus a letter -- but French drops the plural ("les
    # annees 1780"), so demanding it survive character-for-character reports
    # a correct translation as having altered a code.
    def _keep(token):
        # Not identifier codes, however much they look like one:
        #   1780s        a decade; French drops the plural
        #   mid-1780s    the same, with an English prefix
        #   60-year      a compound adjective; French says "60 ans"
        #   60-year-old  the same, with a second part -- the old pattern
        #                allowed only one, so this was reported as a code
        #   19th, 21st   an ordinal; French writes XIXe, XXIe
        #   1L, 2L, 3L   US law-school jargon for first/second/third-year
        #                student -- correctly translated as "premiere
        #                annee" etc., which is exactly what this check
        #                should not demand survive as literal "1L".
        # Demanding these survive character-for-character reports a correct
        # translation as having altered a code.
        #
        # THE ORDINALS AND THE SECOND HYPHEN PART WERE ADDED FROM EVIDENCE:
        # task 4452 was ACCEPTED by the platform, and our gate reported
        # "60-year-old, 20th, 19th" as altered identifiers against a French
        # text that had rendered every one of them correctly.
        #
        # 1L/2L/3L IS ITS OWN ALTERNATIVE, NOT FOLDED INTO THE SUFFIX LIST
        # ABOVE: a bare "\dL" pattern would also swallow a real code like
        # "5L" meaning five litres, which this check exists to protect.
        # Law school is exactly three years, so only 1-3 qualify.
        return not re.match(
            r'^(?:[a-z]+-)?\d{1,4}(?:st|nd|rd|th|s)?(?:-[a-z]{2,})*$'
            r'|^[123]L$',
            str(token), re.IGNORECASE)

    source_tokens = _multiset([t for t in pattern.findall(source or '') if _keep(t)])
    target_tokens = _multiset([t for t in pattern.findall(target or '') if _keep(t)])
    missing, _ = _multiset_diff(source_tokens, target_tokens)
    if not missing:
        return []
    shown = ', '.join(str(token) for token in missing[:5])
    return [QualityIssue(
        category=IssueCategory.ENTITY, severity=severity,
        problem=f'{label} from the source is missing or altered in the output: {shown}.',
        correction=f'{label} must be reproduced character-for-character.',
        source_span=shown, rule=rule,
        segment_index=segment_index, detector='deterministic',
    )]


def check_verbatim_tokens(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    URLs and e-mail addresses are never translated, so any difference is a
    defect -- HIGH, because a broken link in a deliverable is visible and
    embarrassing but does not misstate a fact.

    Identifiers are checked at MEDIUM. The pattern is intentionally loose
    and will occasionally match an ordinary alphanumeric word that the
    target language legitimately renders differently, so this one informs
    rather than blocks under the default policy.
    """
    issues = []
    issues += _check_verbatim(
        source, target, _URL_RE, 'A URL', 'entity.url-altered',
        Severity.HIGH, segment_index,
    )
    issues += _check_verbatim(
        source, target, _EMAIL_RE, 'An e-mail address', 'entity.email-altered',
        Severity.HIGH, segment_index,
    )
    issues += _check_verbatim(
        source, target, _IDENTIFIER_RE, 'An identifier code',
        'entity.identifier-altered', Severity.MEDIUM, segment_index,
    )
    return issues


# ---------------------------------------------------------------------- #
# Output integrity
# ---------------------------------------------------------------------- #

# Phrasings a model uses when it addresses the operator instead of producing
# the deliverable. Anchored to line starts or wrapped in markers so ordinary
# prose that happens to contain "note that" is not caught.
_COMMENTARY_PATTERNS = (
    re.compile(r'^\s*(?:##+\s*)?(?:final|revised|corrected)\s+(?:translation|text|version)\s*:?\s*$',
               re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:here(?:\'s| is)\s+the\s+\w+|as an ai\b|i (?:have|\'ve)\s+translated)',
               re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:note|disclaimer|translator[\'s]* note)\s*:',
               re.IGNORECASE | re.MULTILINE),
    re.compile(r'[✅❌⚠]'),  # check mark, cross mark, warning sign
    re.compile(r'^\s*```', re.MULTILINE),
    # worker_prompts._POLISH's own footer, in case it gets pasted along
    # with the document instead of being read and discarded -- see the
    # "no leaked scaffolding" entry in that module's header comment.
    re.compile(r'^\s*verify against audio\s*:', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*=+\s*translation\s+(?:start|end)\s*=+', re.IGNORECASE | re.MULTILINE),
    # worker_prompts._REVIEW's CLEAN verdict, only when it is the very
    # first thing in the document -- exactly the shape a leaked verdict
    # takes, and not a pattern a real transcript would open with.
    re.compile(r'\A\s*CLEAN\s*(?:\r?\n|$)'),
)

# A paragraph repeated verbatim -- the classic symptom of a model looping.
_PARAGRAPH_SPLIT = re.compile(r'\n\s*\n')


#: A bare domain: "planethunters.org", "bbc.co.uk". Distinct from _URL_RE,
#: which requires a scheme and therefore misses every domain a person says
#: out loud -- which is most of them, in a transcript.
_DOMAIN_RE = re.compile(
    r'\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'(?:com|org|net|edu|gov|int|mil|io|co|ai|app|dev|uk|fr|de|au|ca|info|biz)\b',
    re.IGNORECASE,
)


def check_invented_references(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    A URL, domain or e-mail address in the output that the source never
    mentioned.

    WHY THIS IS ITS OWN CHECK. `check_verbatim_tokens` looks for references
    that went MISSING; this looks for ones that ARRIVED. A model asked to
    tidy a transcript will helpfully supply the website it believes is being
    described, and a plausible invention is more dangerous than an
    implausible one -- nobody questions it.

    Found on a real task: a polished translation contained
    "planethunters.org", which appears nowhere in the English and nowhere in
    the machine draft. It is the correct site for what the speaker was
    describing, which is exactly why it slipped past a human reader.

    HIGH, not CRITICAL: a fabricated reference is serious, but unlike a
    changed quantity it does not silently alter a stated fact, and blocking
    outright would make a stray mention of a domain unsubmittable.
    """
    issues = []
    for pattern, label in ((_URL_RE, 'URL'), (_EMAIL_RE, 'E-mail address'),
                           (_DOMAIN_RE, 'Domain')):
        in_source = {t.lower().rstrip('.,;:') for t in pattern.findall(source or '')}
        in_target = {t.lower().rstrip('.,;:') for t in pattern.findall(target or '')}
        # A domain inside a URL the source did state is not an invention.
        source_blob = (source or '').lower()
        added = sorted(t for t in in_target - in_source if t not in source_blob)
        if not added:
            continue
        shown = ', '.join(added[:5])
        issues.append(QualityIssue(
            category=IssueCategory.ENTITY, severity=Severity.HIGH,
            problem=f'{label}(s) in the output that the source never mentions: {shown}.',
            correction='Remove anything the speaker did not actually say.',
            target_span=shown,
            rule='entity.invented-reference',
            segment_index=segment_index, detector='deterministic',
        ))
    return issues



# --------------------------------------------------------------------- #
# A third language in the output
# --------------------------------------------------------------------- #

#: Words that are unmistakably one language and NOT the target. Every entry
#: is checked to be a non-word in the target language -- shared vocabulary
#: (French/Spanish "no", "final", "total") is deliberately absent, because a
#: checker that fires on correct text teaches people to ignore it.
#:
#: Keyed by target language: what does NOT belong when writing French.
_FOREIGN_MARKERS = {
    'fr': {
        # Spanish -- the neighbour a model slips into most often.
        'decir', 'hacer', 'pero', 'porque', 'entonces', 'tambien', 'también',
        'ahora', 'siempre', 'nosotros', 'ustedes', 'muy', 'aqui', 'aquí',
        'esto', 'eso', 'ellos', 'estaba', 'puede', 'hay', 'desde', 'hasta',
        'cuando', 'donde', 'dónde', 'sobre', 'todos', 'algo', 'nada',
        # Italian
        'perché', 'anche', 'sono', 'questo', 'quello', 'sempre', 'adesso',
        # Portuguese
        'porque', 'muito', 'ainda', 'depois', 'porém',
        # English function words that survive a translation pass
        'the', 'and', 'with', 'from', 'that', 'this', 'which', 'because',
        'there', 'their', 'would', 'could', 'should', 'about', 'these',
    },
    'en': {
        'decir', 'hacer', 'pero', 'porque', 'entonces', 'nosotros',
        'toujours', 'parce', 'aussi', 'maintenant', 'beaucoup', 'quelque',
        'lorsque', 'ainsi', 'donc', 'cela', 'était', 'peuvent',
    },
}
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ]{2,}", re.UNICODE)
#: Guillemets, straight quotes and curly quotes -- anything a writer marks
#: as a quotation, title or borrowed name.
_QUOTED_RE = re.compile(r'«[^»]*»|"[^"]*"|“[^”]*”', re.UNICODE)


def check_foreign_language(
    text: str, target_language: Optional[str] = None,
    segment_index: Optional[int] = None,
) -> List[QualityIssue]:
    """
    A word from a language that is neither the source nor the target.

    WHY IT NEEDED ITS OWN CHECK. `check_source_leakage` looks for the SOURCE
    surviving into the output -- English left in a French translation. It
    cannot see a THIRD language, and a model polishing French will
    occasionally reach for Spanish, which is close enough to read as a typo
    and far enough to be wrong.

    Found on a real task: "on peut decir que la planète est là". `decir` is
    Spanish; the French is `dire`. Every existing check passed it, and a
    reviewer who does not read French had nothing to go on.

    MEDIUM. It is a genuine defect, but a single stray word is a correction
    rather than a reason to refuse a whole document -- and the list cannot
    be exhaustive, so treating it as fatal would misrepresent how much it
    can promise.
    """
    markers = _FOREIGN_MARKERS.get((target_language or '').strip().lower()[:2])
    if not markers or not text:
        return []

    if _looks_like_a_title(text):
        # The segment itself reads as a title, not ordinary prose. The
        # per-run stripping below (_TITLE_RUN_RE) still helps when a
        # segment MIXES a title with ordinary framing text around it, but
        # when the model did not title-case every single word of a long
        # title, a marker word can survive stripping simply because
        # nothing capitalized happens to follow it in THAT run -- "Ideas
        # that have shaped our country" has no capital after "that" for
        # the run to close on, even though the whole phrase is plainly
        # still the title. Confirmed live: the same 14-word title this
        # module's docstrings already cite reported "that" this way.
        return []

    # QUOTED MATERIAL IS FOREIGN ON PURPOSE. A title, a nickname or a
    # citation is kept in its original language by convention -- « The Dish »
    # is the telescope's actual name, and "little green man 1" is what the
    # signal was called. Flagging those trains people to ignore the check,
    # which costs more than the words it would have caught.
    scanned = _QUOTED_RE.sub(' ', text)

    # AND A TITLE IS STILL A TITLE WITHOUT QUOTATION MARKS ROUND IT.
    #
    # The capitalised-word guard below skips "The" and "South", but the
    # lowercase "the" in the MIDDLE of an unquoted title survives it, so
    # "intitulé The Mind of the South" -- correct, and a title that must not
    # be translated -- reported a foreign word.
    #
    # Measured against task 4452, a document the platform ACCEPTED: two of
    # its three findings were false positives of exactly this kind. That is
    # the expensive sort of wrong. The screen already tells workers these
    # notes are "worth a look, not worth stalling for", and a check that
    # fires on correct text finishes the job of teaching them to skip it --
    # taking the genuine findings down with it.
    scanned = _TITLE_RUN_RE.sub(' ', scanned)

    found = []
    for token in _TOKEN_RE.findall(scanned):
        # A CAPITALISED WORD IS A NAME, NOT A LANGUAGE SLIP. "The Guardian"
        # and "The Dish" are what they are called; flagging the article in
        # them produces noise on correct text, and a checker that fires on
        # correct text is one people learn to skip. A genuine leak --
        # "decir", "pero", a stray lowercase "the" -- is lower case.
        if token[:1].isupper():
            continue
        lowered = token.lower()
        if lowered in markers and lowered not in found:
            found.append(lowered)

    if not found:
        return []
    shown = ', '.join(found[:6])
    return [QualityIssue(
        category=IssueCategory.TERMINOLOGY, severity=Severity.MEDIUM,
        problem=(
            f'Word(s) that do not belong in the target language: {shown}.'
        ),
        correction='Replace with the correct target-language word.',
        target_span=shown,
        rule='language.foreign-word',
        segment_index=segment_index, detector='deterministic',
    )]


#: Confirmed, from a real 11-document review of accepted-format
#: submissions, to leak untranslated into French output at word level --
#: never long enough for check_source_leakage's 8-word threshold, and
#: (almost) every one capitalized, which is exactly what
#: check_foreign_language's own proper-noun guard exists to let through
#: ("I'm suis en Indiana", "It's est soit 100% soit 0%" -- the rest of
#: each clause was translated correctly around the leaked word).
#:
#: A CURATED BLOCKLIST, NOT A GENERAL HEURISTIC. None of these terms is a
#: valid French word or a plausible proper noun anywhere on this platform,
#: so matching them by exact name carries essentially none of the
#: false-positive risk a general "flag any capitalized word" rule would.
#:
#: LEFT OUT ON PURPOSE: institutional shorthand like "Act", "Duty" and
#: "Corporations" (also confirmed leaking, e.g. "les Act", "la
#: législation sur les Corporations") and a document-specific defined term
#: like "core" (confirmed leaking a dozen times in one document). A rigid
#: blocklist cannot tell a genuine official name some documents correctly
#: keep in English apart from the shorthand backreference that should have
#: been translated, and "core" is an ordinary English word that would
#: misfire constantly on unrelated correct translations. Those are
#: addressed in the TRANSLATE prompt instead (see prompts.py rule 14), not
#: here.
_LEAKED_TERMS_RE = {
    # Each alternative carries its OWN boundaries rather than sharing one
    # pair around the whole group. "U.S." needs that: it ends in
    # punctuation, not a letter, so a shared trailing \b fails the moment
    # it sits at the end of a sentence -- there is no \w/\W transition
    # right after a period followed by nothing. A negative lookahead
    # (not immediately followed by a word character) does the same job
    # without demanding a boundary a period can't provide, and also stops
    # this from matching only the first half of "U.S.A.".
    'fr': re.compile(
        r"\bI['’]m\b|\bI['’]ll\b|\bI['’]ve\b|\bIt['’]s\b|\bThat['’]s\b|"
        r"\byou['’]re\b|\ball right\b|\bunited states\b|\bU\.S\.(?!\w)|"
        r"\ball\b|\beach\b|\banyone\b|\bone\b|\bthey\b|\bcanadian\b|"
        r"\bjanuary\b|\bfebruary\b|\bmarch\b|\bapril\b|\bjune\b|\bjuly\b|"
        r"\baugust\b|\bseptember\b|\boctober\b|\bnovember\b|\bdecember\b|"
        # Added from a real submission that leaked all four repeatedly
        # (Some x4, Very x2, Instead x2, Federal x1 in one document) --
        # moved out of the LLM-based semantic audit and into this free,
        # reliable check for the same reason the words above already are:
        # the semantic pass is intermittent, not systematic, and none of
        # these four have any competing reading in French.
        r"\bsome\b|\bvery\b|\binstead\b|\bfederal\b|"
        # Added from a real submission that leaked all four repeatedly
        # (There's x2, We're x4, Let's x5, Just x3 in one document) --
        # same family as It's/That's/you're above, just not yet covered.
        r"\bthere['’]s\b|\bwe['’]re\b|\blet['’]s\b|\bjust\b|"
        # Added from a fourth audit round of real submissions, roughly 25
        # documents read start to finish. Each of these was confirmed
        # leaking, and each was checked for a competing French reading
        # before being added:
        #   - "museum"/"arctic"/"anthropology"/"region"/"resettlement" are
        #     all near-misses of a real French word (musee/muséum,
        #     arctique, anthropologie, région, réinstallation) but differ
        #     from it by an accent or a letter, so the bare English
        #     spelling is never itself valid French.
        #   - "british"/"english"/"canadian"/"northern"/"north"/"king" are
        #     ordinary adjectives/nouns with no French cognate at all
        #     (britannique, anglais, canadien, nordique/du nord, roi).
        #   - "city" collides with nothing: French is "cite" or "ville",
        #     never bare "city". Left in untranslated 4 times in one
        #     document ("la City", "notable de la City").
        #   - "two"/"three"/"first"/"again"/"nobody"/"she"/"nor"/"can"/
        #     "did"/"thank"/"maybe"/"judge"/"sailor"/"brain"/"whether"/
        #     "earth"/"apparently"/"nevertheless" have no plausible French
        #     reading either; each was seen leaking bare into otherwise
        #     correctly-translated French sentences.
        #   - "here's"/"where's"/"what's"/"we'll" are contractions, same
        #     family as I'm/I've/It's/That's above.
        #   - "stand up" is a two-word phrase, kept as one alternative
        #     rather than two separate word matches.
        #
        # DELIBERATELY NOT ADDED, from the same audit round:
        #   - "Canada's" -- a real leak ("la geographie de Canada's"), but
        #     "Canada's History" is also the actual name of a real
        #     magazine correctly left untranslated in the same document.
        #     A blanket match would misfire on the title. Needs the
        #     semantic layer, not a blocklist entry.
        #   - "Court" -- common French word meaning "short"; blocklisting
        #     it would fire on correct French text constantly.
        #   - "Germany"/"England"/"London" -- country/city proper nouns
        #     are a different, much broader family than a short common
        #     word, and carry their own naming conventions (some texts
        #     correctly keep a place name in English). Same reasoning
        #     that already excludes "Germany".
        #   - "DNA" -- an acronym, not a common word; a different problem
        #     from the rest of this list.
        #   - plain "and" -- too generic and too high-volume a false-
        #     positive risk to add without a narrower shape.
        r"\bwhether\b|\bearth\b|\bhere['’]s\b|\bwhere['’]s\b|\bwhat['’]s\b|"
        r"\bbrain\b|\btwo\b|\bthree\b|\bmaybe\b|\bjudge\b|\bsailor\b|"
        r"\bbritish\b|\bcity\b|\bmuseum\b|\bnevertheless\b|\barctic\b|"
        r"\bresettlement\b|\bapparently\b|\bagain\b|\bfirst\b|\bnobody\b|"
        r"\bwe['’]ll\b|\bking\b|\benglish\b|\bshe\b|\bnor\b|\bcan\b|\bdid\b|"
        r"\bnorthern\b|\bnorth\b|\bthank\b|\bstand up\b|\banthropology\b|"
        # "Region" is checked case-SENSITIVELY here (capital R only) via an
        # inline flag override, unlike every other alternative in this
        # pattern. Lowercase "region" is one accent away from the real
        # French word "région", and a translator dropping just that one
        # accent in ordinary running prose is far more plausible than the
        # same slip on a rarer word like "museum". The real leak that
        # motivated adding this was always capitalized ("Kivillic
        # Region"), so restricting to that case loses nothing evidenced
        # while avoiding a misread of accent-dropped French.
        r"\b(?-i:Region)\b",
        re.IGNORECASE,
    ),
}


def check_common_word_leakage(
    target: str, target_language: Optional[str] = None,
    segment_index: Optional[int] = None,
) -> List[QualityIssue]:
    """
    A short, common English word or contraction surviving untranslated.

    ITS OWN CHECK, SEPARATE FROM check_foreign_language AND
    check_source_leakage -- neither can see this. check_source_leakage
    only fires on an 8-word-or-longer verbatim run; check_foreign_language
    explicitly SKIPS every capitalized token on the theory that it is a
    name, which is exactly what almost every leak in `_LEAKED_TERMS_RE`
    is (sentence-initial, or "I"-contracted, so always capitalized in the
    source it came from).

    Runs the SAME title/quote guards as the other two leakage checks, for
    the same reason: a genuinely quoted English phrase, or a segment that
    is itself a title, is not this defect.

    MEDIUM, not blocking -- still new enough, on real traffic, to earn the
    same caution every other check here already went through before being
    trusted to stop a submission outright.
    """
    pattern = _LEAKED_TERMS_RE.get((target_language or '').strip().lower()[:2])
    if not pattern or not target:
        return []
    if _looks_like_a_title(target):
        return []
    scanned = _QUOTED_RE.sub(' ', target)

    seen = set()
    found = []
    for match in pattern.finditer(scanned):
        word = match.group(0)
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(word)

    if not found:
        return []
    shown = ', '.join(found[:6])
    return [QualityIssue(
        category=IssueCategory.LEAKAGE, severity=Severity.MEDIUM,
        problem=f'English word(s) or phrase(s) left untranslated: {shown}.',
        correction='Translate into the target language.',
        target_span=shown,
        rule='leakage.common-word',
        segment_index=segment_index, detector='deterministic',
    )]


#: English-style currency: symbol immediately before the figure, and
#: comma-grouped thousands -- "$1,000", "$60,000". French convention puts
#: the symbol after the figure with a space-grouped thousands separator
#: ("1 000 $"), so this shape surviving into a French translation is a
#: display-format defect, not a value defect -- check_numbers already
#: confirms the VALUE survived; this catches the convention check_numbers
#: was never asked about.
_ANGLO_CURRENCY_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?')


def check_currency_format(
    target: str, target_language: Optional[str] = None,
    segment_index: Optional[int] = None,
) -> List[QualityIssue]:
    """
    An English-formatted dollar figure ($60,000) surviving in French
    output, where the convention is the number first, space-grouped, with
    the symbol last (60 000 $).

    Confirmed on a real submission: eight figures in a row -- $60,000,
    $5,500 (twice), $27,500 (four times) -- all left in raw English
    format, in a document where an EARLIER, similar figure ($5,143) had
    been correctly reformatted moments before. Intermittent, exactly like
    the word-leakage `check_common_word_leakage` targets, and invisible to
    check_numbers, which only confirms the VALUE survived: 60,000 and
    60 000 are the same number, so it has nothing to say about which one
    is displayed.
    """
    if (target_language or '').strip().lower()[:2] != 'fr':
        return []
    if not target:
        return []
    found = _ANGLO_CURRENCY_RE.findall(target)
    if not found:
        return []
    shown = ', '.join(found[:6])
    return [QualityIssue(
        category=IssueCategory.TYPOGRAPHY, severity=Severity.MEDIUM,
        problem=f'Currency written in English format rather than French: {shown}.',
        correction=(
            'Write the figure with the number first and the symbol last, '
            'using a space as the thousands separator (e.g. "1 000 $").'
        ),
        target_span=shown,
        rule='typography.anglo-currency',
        segment_index=segment_index, detector='deterministic',
    )]


#: The signature of UTF-8 bytes read as Latin-1/cp1252. These pairs do not
#: occur in real French, Spanish, German or Portuguese text: "Ã©" is what
#: "é" becomes, "â€™" is what a typographic apostrophe becomes, "Â«" is what
#: a guillemet becomes.
_MOJIBAKE_RE = re.compile(
    r'Ã[ -¿-©¨ª«¬®¯°±²³´µ¶·¹º»¼½¾¿§¦¥¤£¢¡]'
    r'|â€[-™œ¢“”˜ž]|Â[ -¿«»°©®±²³µ¶·¹º]'
)

#: Enough of them to be certain. One stray sequence could conceivably be
#: quoted material; a document full of them is corrupted.
_MOJIBAKE_THRESHOLD = 3


def check_encoding(text: str, segment_index: Optional[int] = None) -> List[QualityIssue]:
    """
    Text that has been through a broken copy-paste.

    WHY THIS IS CRITICAL AND NOT COSMETIC. UTF-8 read back as Latin-1 turns
    every accented character into two: "conjugué" becomes "conjuguÃ©",
    "d'accord" becomes "dâ€™accord". A French document delivered that way is
    unreadable and is a certain rejection -- and unlike a mistranslation, it
    is not a judgement call, so there is no reason to let it through.

    IT IS INVISIBLE TO EVERY OTHER CHECK. Measured on a real accepted
    document that had been through a bad paste: 205 corrupted sequences, and
    the foreign-language, duplication and commentary checks all reported
    nothing. The text still looked like French to them.

    THE WORKER CANNOT NECESSARILY SEE IT EITHER. On a translation task they
    may not read the target language, and "Ã©" looks no stranger than any
    other accented character to somebody who does not know what it should
    have been.
    """
    found = _MOJIBAKE_RE.findall(text or '')
    if len(found) < _MOJIBAKE_THRESHOLD:
        return []
    return [QualityIssue(
        category=IssueCategory.STRUCTURE, severity=Severity.CRITICAL,
        problem=(
            f'The text is character-corrupted: {len(found)} sequences such as '
            f'{found[0]!r} appear, which is what accented characters become '
            f'after a broken copy-paste.'
        ),
        correction=(
            'Copy the text again from the source. Paste it as plain text, and '
            'check that accented letters look right before saving.'
        ),
        target_span=found[0],
        rule='encoding.mojibake',
        segment_index=segment_index, detector='deterministic',
    )]


#: Words an ENGLISH autocorrect produces when it is run over another
#: language, keyed by target language. Each entry is (pattern, correct form,
#: what it actually means) and every one is unambiguous: the mangled form is
#: not a word of the target language in that position, so there is no
#: judgement involved in reporting it.
_AUTOCORRECT = {
    'fr': (
        # "dont" (of which / whose) is one of the commonest words in formal
        # French. An English dictionary sees a typo and inserts an
        # apostrophe. Seen 10 times in a single accepted document.
        (re.compile(r"\bdon['’]t\b"), 'dont',
         "\"don't\" is English; French \"dont\" takes no apostrophe"),
        # "VIe siecle" -> "Vie siecle", because "vie" IS a French word, so
        # the corrector is confident. It turns "the 6th century" into "the
        # life century". Seen 4 times in the same document.
        (re.compile(r'\bVie(?=\s+si[eè]cle)'), 'VIe',
         '"Vie siecle" reads as "life century"; the ordinal is "VIe"'),
    ),
}


def check_autocorrect(text: str, target_language: str,
                      segment_index: Optional[int] = None) -> List[QualityIssue]:
    """
    Damage done by a spellchecker set to the wrong language.

    FOUND ON A THIRD ACCEPTED DOCUMENT, and invisible to everything else we
    run: the foreign-word, encoding and typography checks all returned
    nothing on text containing fifteen instances of it. The document was
    properly encoded and read as fluent French; a corrector had simply been
    through it with an English dictionary.

    HIGH, ON THE REVIEWER'S OWN EVIDENCE. This was MEDIUM for one day, on
    the reasoning that the platform had accepted a document full of it. Then
    a review came back FAILED with this at the top of the reasons:

        "don't nous avons tous entendu parler" -> "dont nous avons tous
        entendu parler". don't is an English/French typo. The correct
        French relative pronoun is dont.

    So it is not merely untidy: a reviewer reads it as a mistake and marks
    the work down for it. Acceptance elsewhere meant the defect slipped past
    one reviewer, not that it is tolerated.

    Still not blocking, because the gate blocks only on CRITICAL and this
    does not make a document unusable the way corrupted text does.

    ONLY UNAMBIGUOUS CASES. Every entry is a form that is simply not a word
    of the target language where it appears, so the check never has to
    decide whether the author meant it.
    """
    rules = _AUTOCORRECT.get((target_language or '').strip().lower()[:2])
    if not rules or not text:
        return []

    issues = []
    for pattern, correct, explanation in rules:
        hits = pattern.findall(text)
        if not hits:
            continue
        issues.append(QualityIssue(
            category=IssueCategory.LINGUISTIC, severity=Severity.HIGH,
            problem=(
                f'{len(hits)} occurrence(s) of a word an English spellchecker '
                f'has altered: {explanation}.'
            ),
            correction=f'Replace every one with "{correct}".',
            target_span=correct,
            rule='language.autocorrect-damage',
            segment_index=segment_index, detector='deterministic',
        ))
    return issues


#: "ton" IS ALSO AN ORDINARY NOUN ("tone"), and French can never stack two
#: determiners before one noun -- "le ton juste" (the right tone) cannot
#: also mean "your right ...", so a determiner immediately before "ton"
#: proves it is the noun, not the familiar possessive. CONFIRMED LIVE: a
#: real accepted-pending task was flagged for "1 familiar, 10 polite" on
#: the strength of exactly one match, "trouver le ton juste" (to find the
#: right tone) -- zero genuine tu/vous mixing anywhere in the document,
#: independently confirmed by re-reading the whole thing for every second-
#: person form by hand. The list is deliberately generous rather than
#: narrowly tuned to that one sentence, for the same reason the 'cause
#: exclusions are: closing the whole class of false positive rather than
#: the one instance that happened to be seen first.
_TON_NOUN_PRECEDERS = (
    'le ', 'un ', 'ce ', 'cet ', 'du ', 'au ', 'quel ', 'quelle ',
    'mon ', 'son ', 'notre ', 'votre ', 'leur ', 'aucun ', 'chaque ',
    'tout ', 'même ', 'bon ', 'mauvais ', 'autre ', 'certain ',
)

#: Second-person address, split by register. Only languages with a real
#: T-V distinction appear here; English has none, so there is nothing to be
#: inconsistent about.
_ADDRESS_FORMS = {
    # The word boundaries are load-bearing and were lost once already:
    # an escaping slip turned every backslash-b into a literal backspace
    # character, so the patterns compiled, ran, and matched NOTHING --
    # reporting every document clean. A check that cannot fail is worse
    # than no check, because it is indistinguishable from a passing one.
    #
    # "tiens" IS DELIBERATELY LEFT OUT, not merely unrecognised. It is the
    # first-person present of "tenir" ("je tiens à vous remercier" --
    # "I would like to thank you" -- is ordinary formal French with no
    # second person in it at all) far more often than it is the rare
    # familiar possessive pronoun ("les tiens"). Keeping it would trade an
    # occasional real catch for a near-certain false one in exactly the
    # register -- formal, prepared remarks -- this check runs on most.
    'fr': (
        re.compile(
            r"\b(?:tu|t['’]as|t['’]es|toi|ta|tes)\b"
            r'|' + ''.join(f'(?<!{w})' for w in _TON_NOUN_PRECEDERS) + r'\bton\b',
            re.IGNORECASE),
        re.compile(r'\b(?:vous|votre|vos|vôtre)\b', re.IGNORECASE),
        'tu', 'vous',
    ),
    'es': (
        re.compile(r'\b(?:tú|ti|tuyo|tuya|tus)\b', re.IGNORECASE),
        re.compile(r'\b(?:usted|ustedes)\b', re.IGNORECASE),
        'tú', 'usted',
    ),
    'de': (
        re.compile(r'\b(?:du|dich|dir|dein|deine|deinem|deinen)\b'),
        re.compile(r'\b(?:Sie|Ihnen|Ihre|Ihrem|Ihren)\b'),
        'du', 'Sie',
    ),
}


#: Text inside a guillemet-quoted span belongs to whoever is being quoted --
#: a novelist's dialogue, a historical figure's own words -- not to the
#: document's own address to its audience. CONFIRMED on a real, PASSED
#: task ("A 12.mp3", a lecture on the American South): Toni Morrison's
#: Beloved quoted in tu ("« Toi et moi, Sethe, on a trop d'hier. »") and
#: Noah Webster's own 18th-century apostrophe to New England, also in tu
#: ("« ... combien tes habitudes... »"), both counted against the
#: lecturer's own consistently-vous address to the audience -- a false
#: "2 familiar, 15 polite" finding on a document a human reviewer actually
#: passed with both lines in it unchanged.
#:
#: Excluded structurally, the same way the 'ton' noun/possessive fix
#: closed a whole class rather than one sentence: ANY quoted dialogue with
#: its own tu/vous choice is covered, not just these two quotes. The one
#: honest limit is that it only recognises a quote actually delimited with
#: guillemets in the translated text -- unmatched or straight-quoted
#: dialogue still counts, which is no worse than before this fix existed.
_GUILLEMET_QUOTE_RE = re.compile(r'«[^»]*»')


def _outside_quotes(text: str) -> str:
    return _GUILLEMET_QUOTE_RE.sub(' ', text)


def check_address_consistency(text: str, target_language: str,
                              segment_index: Optional[int] = None) -> List[QualityIssue]:
    """
    One document addressing its audience two different ways.

    WRITTEN FROM A FAILED REVIEW. Task 4639 came back rejected, and the
    second item on the reviewer's list was a sudden tutoiement partway
    through a document that had been using vous:

        « Une, tu l'as comme tu veux »  ->  "Tutoiement soudain"

    Switching mid-document reads as two different people talking, and it is
    one of the few naturalness defects that a machine CAN see: the forms are
    closed sets of common words.

    MEDIUM AND ADVISORY, because the mix is not always wrong. French `vous`
    is polite singular AND ordinary plural, so a speaker addressing one
    person as `tu` and the room as `vous` is correct. The check reports the
    inconsistency and the counts; a person decides. Blocking on it would
    refuse legitimate documents, and refusing correct work is how a check
    gets ignored.
    """
    forms = _ADDRESS_FORMS.get((target_language or '').strip().lower()[:2])
    if not forms or not text:
        return []

    familiar_re, polite_re, familiar_name, polite_name = forms
    # QUOTED SPEECH IS EXCLUDED BEFORE COUNTING -- see _outside_quotes.
    unquoted = _outside_quotes(text)
    familiar = familiar_re.findall(unquoted)
    polite = polite_re.findall(unquoted)
    if not familiar or not polite:
        return []

    # WHICHEVER IS RARER IS THE LIKELY SLIP. A document with sixty `vous`
    # and two `tu` is a vous document with two mistakes in it; naming the
    # minority form tells the worker what to search for.
    minority, majority = ((familiar_name, polite_name)
                          if len(familiar) <= len(polite)
                          else (polite_name, familiar_name))
    return [QualityIssue(
        category=IssueCategory.LINGUISTIC, severity=Severity.MEDIUM,
        problem=(
            f'The translation addresses its audience both ways: '
            f'{len(familiar)} familiar ("{familiar_name}") and {len(polite)} '
            f'polite ("{polite_name}"). A review was failed for switching '
            f'partway through.'
        ),
        correction=(
            f'Pick one and use it throughout. The "{minority}" forms are the '
            f'rarer, so they are the likely slips -- unless the speaker really '
            f'is addressing one person and a group separately.'
        ),
        target_span=minority,
        rule='language.mixed-address',
        segment_index=segment_index, detector='deterministic',
    )]


def check_commentary(text: str, segment_index: Optional[int] = None) -> List[QualityIssue]:
    """
    The deliverable must contain the content and nothing else (spec 28).

    Only run for submission-bound profiles. A general cleanup pass has no
    such requirement, and a lecture transcript can legitimately contain the
    words "here is the translation" because a speaker said them.
    """
    issues = []
    for pattern in _COMMENTARY_PATTERNS:
        match = pattern.search(text or '')
        if match:
            issues.append(QualityIssue(
                category=IssueCategory.LEAKAGE, severity=Severity.HIGH,
                problem=(
                    'The output contains AI commentary or scaffolding rather '
                    'than only the deliverable content.'
                ),
                correction='Remove headings, notes, status markers and fences.',
                target_span=match.group(0).strip()[:80],
                rule='leakage.commentary',
                segment_index=segment_index, detector='deterministic',
            ))
            break
    return issues


def check_duplication(text: str, segment_index: Optional[int] = None) -> List[QualityIssue]:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text or '') if p.strip()]
    seen = set()
    for paragraph in paragraphs:
        # Short paragraphs repeat legitimately in a transcript ("Yes.",
        # "Right."), so only substantial blocks count as a loop symptom.
        if len(paragraph) < 80:
            continue
        if paragraph in seen:
            return [QualityIssue(
                category=IssueCategory.COMPLETENESS, severity=Severity.HIGH,
                problem='A paragraph is repeated verbatim in the output.',
                correction='Remove the duplicated block.',
                target_span=paragraph[:80],
                rule='completeness.duplicated-paragraph',
                segment_index=segment_index, detector='deterministic',
            )]
        seen.add(paragraph)
    return []


def check_empty_output(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    if (source or '').strip() and not (target or '').strip():
        return [QualityIssue(
            category=IssueCategory.COMPLETENESS, severity=Severity.CRITICAL,
            problem='The output is empty although the source has content.',
            correction='Produce output for every source segment.',
            source_span=(source or '').strip()[:80],
            rule='completeness.empty-output',
            segment_index=segment_index, detector='deterministic',
        )]
    return []


# Ratio of target characters to source characters outside which the output is
# suspicious. Wide because languages genuinely differ in verbosity -- French
# runs ~15-20% longer than English, agglutinative languages far shorter -- so
# these bounds catch a truncation or a doubling, not ordinary expansion.
LENGTH_RATIO_FLOOR = 0.45
LENGTH_RATIO_CEILING = 2.4
# Below this, ratios are noise: a four-word segment can double in length for
# entirely correct reasons.
_MIN_CHARS_FOR_RATIO = 200


def check_length_anomaly(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    Catches wholesale truncation and runaway generation.

    Only applied above _MIN_CHARS_FOR_RATIO. This is a blunt instrument by
    design: it exists to catch the case where half the document silently
    vanished, which a semantic auditor reading one chunk at a time is
    structurally unable to notice.
    """
    source_length = len((source or '').strip())
    target_length = len((target or '').strip())
    if source_length < _MIN_CHARS_FOR_RATIO or not target_length:
        return []
    ratio = target_length / source_length
    if ratio < LENGTH_RATIO_FLOOR:
        return [QualityIssue(
            category=IssueCategory.COMPLETENESS, severity=Severity.HIGH,
            problem=(
                f'The output is {ratio:.0%} the length of the source, which '
                'suggests content was omitted or summarized.'
            ),
            correction=(
                'Translate the source completely. Do not summarize or '
                'condense unless summarization was explicitly requested.'
            ),
            rule='completeness.output-too-short',
            segment_index=segment_index, detector='deterministic',
        )]
    if ratio > LENGTH_RATIO_CEILING:
        return [QualityIssue(
            category=IssueCategory.COMPLETENESS, severity=Severity.MEDIUM,
            problem=(
                f'The output is {ratio:.0%} of the source length, which '
                'suggests material was added or duplicated.'
            ),
            correction='Remove anything the source does not say.',
            rule='completeness.output-too-long',
            segment_index=segment_index, detector='deterministic',
        )]
    return []


def check_source_leakage(
    source: str, target: str, segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    Long verbatim runs of source text surviving into a translation.

    Matched on word sequences rather than characters, and only at eight
    words or more. Proper nouns, quoted titles, code and formulae are all
    SUPPOSED to survive verbatim -- but "every one of them is short" turned
    out to be false: a real, correctly-untranslated book title ("The
    Promise of Canada, 150 years, People and Ideas that have shaped our
    country") ran 14 words and was reported as a skipped sentence, blocking
    a submission that was actually fine.

    _looks_like_a_title (shared with check_foreign_language) is what tells
    a skipped sentence apart from a title once the eight-word threshold is
    crossed -- checked against the WHOLE target segment, not the matched
    eight-word slice alone. A sliding window can land on the least-
    capitalized tail of a real title (the model did not title-case "have",
    "shaped" or "country" in the example above), which reads as ordinary
    prose in isolation even though the segment as a whole is plainly still
    the title; the anchor words earlier in it need to stay in view. The
    same reasoning is why this looks at the target, not the source: raw
    ASR rarely preserves a title's capitalization at all, having no way to
    know a stretch of spoken audio names a book -- a model that recognized
    the title and rendered it properly capitalized is where that structure
    actually shows up.
    """
    if _looks_like_a_title(target):
        return []
    source_words = re.findall(r"[\w'-]+", (source or '').lower())
    target_lower = (target or '').lower()
    if len(source_words) < 8:
        return []
    run = 8
    for start in range(0, len(source_words) - run + 1):
        phrase = ' '.join(source_words[start:start + run])
        if phrase in target_lower:
            return [QualityIssue(
                category=IssueCategory.LEAKAGE, severity=Severity.HIGH,
                problem=(
                    'A run of source-language text was carried into the '
                    'output untranslated.'
                ),
                correction='Translate the whole passage into the target language.',
                source_span=phrase[:80],
                rule='leakage.untranslated-source',
                segment_index=segment_index, detector='deterministic',
            )]
    return []


def run_deterministic_checks(
    source: str,
    target: str,
    target_language: Optional[str] = None,
    segment_index: Optional[int] = None,
    *,
    is_translation: bool = True,
    submission_ready: bool = False,
) -> List[QualityIssue]:
    """
    Every deterministic check appropriate to this run, in one call.

    `is_translation` gates the leakage check: for a same-language cleanup
    pass, source text surviving verbatim into the output is the CORRECT
    outcome for most of the document, so running it would report the whole
    transcript as defective.

    `submission_ready` gates the commentary check, for the reason given on
    check_commentary.
    """
    issues = []
    issues += check_empty_output(source, target, segment_index)
    # An empty target makes every remaining comparison meaningless -- and
    # would emit a "every number is missing" finding on top of the real one.
    if not (target or '').strip():
        return issues

    issues += check_numbers(source, target, segment_index)
    issues += check_verbatim_tokens(source, target, segment_index)
    # References that ARRIVED rather than went missing, and words from a
    # third language. Both were found by running a real polished
    # translation through this function and comparing with a human read.
    issues += check_invented_references(source, target, segment_index)
    issues += check_foreign_language(target, target_language, segment_index)
    issues += check_common_word_leakage(target, target_language, segment_index)
    issues += check_currency_format(target, target_language, segment_index)
    issues += check_duplication(target, segment_index)
    issues += check_length_anomaly(source, target, segment_index)
    issues += check_typography(target, target_language, segment_index)
    # Corruption from a broken paste. Cheap, certain, and invisible to
    # every other check here -- the text still parses as the right language.
    issues += check_encoding(target, segment_index)
    # A spellchecker set to the wrong language. Invisible to every check
    # above -- the text is well-formed and reads as the right language.
    issues += check_autocorrect(target, target_language, segment_index)
    # Familiar vs polite address, mixed in one document. Named in a failed
    # review, and one of the few naturalness defects a machine can see.
    issues += check_address_consistency(target, target_language, segment_index)
    if is_translation:
        issues += check_source_leakage(source, target, segment_index)
    if submission_ready:
        issues += check_commentary(target, segment_index)
    return issues
