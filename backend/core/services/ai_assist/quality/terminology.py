"""
Deterministic enforcement of the terminology and entity anchors that
analysis.py discovered (spec 9, spec 10).

This is the half of terminology control that does NOT need a model. Once an
anchor exists -- "principal" is rendered "mandant" here, and "commettant" is
forbidden because it names a different relationship -- checking whether the
candidate honoured it is string work, and string work does not hallucinate,
does not cost a token, and does not get bored halfway through a long
document. spec 9's hardest requirement is CONSISTENCY ACROSS A LONG
DOCUMENT, which is exactly the thing an auditor reading one chunk at a time
is structurally unable to verify and a whole-document scan gets for free.

The anchors themselves are always discovered, never checked in. There is no
legal glossary, no medical glossary, and no French glossary anywhere in this
package -- a checked-in glossary is a hard-coded domain wearing a data
structure (RULE 8), and it would be wrong for the next document anyway.

WHAT THIS DELIBERATELY DOES NOT DO: it never edits text. A terminology
finding is reported and handed to the repair stage, because replacing
"commettant" with "mandant" mechanically would also rewrite the one passage
where the source genuinely meant something else -- and inflection means the
correct replacement is rarely a literal substring swap.
"""
import re
import unicodedata
from typing import Dict, List, Optional, Sequence

from .issues import IssueCategory, QualityIssue, Severity


def _fold(text: str) -> str:
    """
    Casefolded, accent-stripped text for matching.

    Accent-insensitive on purpose: a model that writes "employe" for
    "employé" has made a typographic error, not chosen a different term, and
    treating the two as different anchors would report a terminology
    violation for what is really a spelling one -- sending the repair stage
    after the wrong problem.
    """
    decomposed = unicodedata.normalize('NFD', text or '')
    stripped = ''.join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def _contains_term(haystack_folded: str, term: str) -> bool:
    """
    Whole-word-ish containment.

    Word boundaries matter in both directions: without them "mandat" matches
    inside "mandataire" and every agency document reports a false positive.
    The trailing boundary is relaxed to allow inflection -- "mandants" and
    "mandaté" must still count as the anchored term -- by permitting up to
    three trailing letters, which covers plural and participle endings
    across the languages this platform handles without matching an unrelated
    longer word.
    """
    folded_term = _fold(term).strip()
    if not folded_term:
        return False
    pattern = r'(?<![^\W\d_])' + re.escape(folded_term) + r'[^\W\d_]{0,3}(?![^\W\d_])'
    return re.search(pattern, haystack_folded) is not None


def check_terminology_consistency(
    target_text: str,
    source_text: str,
    anchors: Sequence[Dict],
    segment_index: Optional[int] = None,
) -> List[QualityIssue]:
    """
    Two checks per anchor, over the WHOLE reviewed range at once.

    1. A forbidden variant appears -> the candidate used a rendering that
       collapses or distorts a distinction the source draws. HIGH: this is
       the fiduciary-duty/duty-of-care class, generalized.
    2. The source term appears but the anchored target term never does ->
       the candidate drifted to some other rendering. MEDIUM, because the
       anchor is a strong recommendation from a model, not ground truth, and
       an unanchored synonym can still be correct.

    Asymmetric severity on purpose. A FORBIDDEN variant was named as wrong
    by an analyst that had read the document; a merely absent anchor is
    weaker evidence, and blocking on it would make the gate fire on every
    legitimate stylistic choice.
    """
    if not anchors:
        return []

    target_folded = _fold(target_text)
    source_folded = _fold(source_text)
    issues: List[QualityIssue] = []

    for anchor in anchors:
        source_term = str(anchor.get('source_term') or '').strip()
        target_term = str(anchor.get('target_term') or '').strip()
        if not source_term or not target_term:
            continue

        for variant in anchor.get('forbidden_variants') or []:
            variant = str(variant).strip()
            if not variant or _fold(variant) == _fold(target_term):
                continue
            if _contains_term(target_folded, variant):
                issues.append(QualityIssue(
                    category=IssueCategory.TERMINOLOGY, severity=Severity.HIGH,
                    problem=(
                        f'"{variant}" is used where "{source_term}" should be '
                        f'rendered "{target_term}". The two are not '
                        f'interchangeable in this material.'
                    ),
                    correction=f'Render "{source_term}" as "{target_term}" throughout.',
                    source_span=source_term, target_span=variant,
                    rule='terminology.forbidden-variant',
                    confidence=_confidence_of(anchor),
                    segment_index=segment_index, detector='deterministic',
                ))

        if (
            _contains_term(source_folded, source_term)
            and not _contains_term(target_folded, target_term)
        ):
            # LOW, and explicitly not a defect claim.
            #
            # This branch used to be MEDIUM, which made it blocking under a
            # high-risk domain -- and it was wrong twice in live runs. Once
            # the anchor proposed "trust constructif" while the translation
            # said "fiducie constructive" (both correct French); once the
            # anchor itself was "trust implicit", which is not French at all.
            #
            # The asymmetry is the point: an anchor is one model's opinion
            # about the ideal rendering, formed from the SOURCE ALONE without
            # ever seeing the translation. A translator disagreeing with it is
            # not evidence of a defect. What IS evidence is the target
            # contradicting ITSELF -- see check_rendering_consistency, which
            # measures that instead and can block on it honestly.
            issues.append(QualityIssue(
                category=IssueCategory.TERMINOLOGY, severity=Severity.LOW,
                problem=(
                    f'The source uses "{source_term}" and the suggested '
                    f'rendering "{target_term}" does not appear. The output '
                    f'may be using a different but equally valid term.'
                ),
                correction=(
                    f'No action needed if the output renders "{source_term}" '
                    f'consistently. Check only if it varies.'
                ),
                source_span=source_term, target_span='',
                rule='terminology.anchor-not-applied',
                confidence=_confidence_of(anchor),
                segment_index=segment_index, detector='deterministic',
            ))

    return issues


def check_rendering_consistency(
    pairs: Sequence[Dict], anchors: Sequence[Dict],
) -> List[QualityIssue]:
    """
    Does the target contradict ITSELF?

    THE DISTINCTION THIS MODULE TURNS ON: consistency is objectively
    checkable, correctness is not. Whether "form contracts" is better as
    "contrats d'adhésion" or "contrats types" is a judgement two competent
    translators can differ on, and an anchor is only one model's opinion
    formed without ever seeing the translation. But rendering it BOTH ways
    in one document is a defect under either opinion -- a reader meets two
    terms and has to wonder whether two different things are meant.

    So this measures drift across occurrences rather than deviation from a
    prescribed term, which makes it immune to the anchor being wrong:

      * two or more known renderings used across the document -> HIGH.
        This is task 4130's finding 3 exactly, and it holds even if the
        anchor picked the wrong one as canonical.
      * one rendering used consistently, and it is a FORBIDDEN variant ->
        HIGH. Analysis named that one as collapsing a distinction, which is
        a claim about meaning rather than taste.
      * one rendering used consistently and it is not forbidden -> clean,
        whether or not it matches the anchor.
      * no known rendering found -> silent here. The target is using
        something we have no candidate for, which says more about the
        anchor than the translation.

    `pairs` is [{'source':..,'target':..}] in document order, because
    per-occurrence alignment is what makes drift visible; a joined blob
    cannot tell "used twice, differently" from "used twice, the same".
    """
    issues: List[QualityIssue] = []

    for anchor in anchors or []:
        source_term = str(anchor.get('source_term') or '').strip()
        target_term = str(anchor.get('target_term') or '').strip()
        if not source_term or not target_term:
            continue

        forbidden = [
            str(v).strip() for v in (anchor.get('forbidden_variants') or [])
            if str(v).strip() and _fold(str(v)) != _fold(target_term)
        ]
        candidates = [target_term] + forbidden

        # Which renderings actually appear where the source term is used.
        #
        # `unmatched` is the load-bearing half. The candidate list only
        # contains renderings we were told about, so drift toward an unknown
        # third term is invisible to a candidate-only comparison -- and that
        # is precisely the common case, since the anchor names the rendering
        # analysis EXPECTED, not the one the translator reached for. Counting
        # occurrences where no candidate appears catches it: if the term is
        # rendered with the known word here and with something else there,
        # the document is inconsistent whatever that something else is.
        seen: Dict[str, int] = {}
        occurrences = 0
        unmatched = 0
        for pair in pairs:
            if not _contains_term(_fold(pair.get('source') or ''), source_term):
                continue
            occurrences += 1
            target_folded = _fold(pair.get('target') or '')
            matched = False
            for candidate in candidates:
                if _contains_term(target_folded, candidate):
                    seen[candidate] = seen.get(candidate, 0) + 1
                    matched = True
            if not matched:
                unmatched += 1

        if occurrences < 2 or not seen:
            continue

        if len(seen) == 1 and unmatched:
            # Some occurrences use the known rendering, others use something
            # we cannot name. Reported at MEDIUM rather than HIGH because
            # there is an innocent explanation: a translation may carry the
            # concept with a pronoun, an ellipsis or a restructured clause
            # rather than repeating the term. Suggestive, not conclusive --
            # so it informs a reviewer without blocking a submission.
            issues.append(QualityIssue(
                category=IssueCategory.TERMINOLOGY, severity=Severity.MEDIUM,
                problem=(
                    f'"{source_term}" appears {occurrences} times in the '
                    f'source but is rendered "{next(iter(seen))}" in only '
                    f'{seen[next(iter(seen))]} of them. The others use some '
                    f'other wording, which may be drift or may be a '
                    f'legitimate restructuring.'
                ),
                correction=(
                    f'Check the other occurrences of "{source_term}" and make '
                    f'the rendering consistent where it refers to the same '
                    f'concept.'
                ),
                source_span=source_term, target_span=next(iter(seen)),
                rule='terminology.partial-rendering',
                confidence=_confidence_of(anchor),
                detector='deterministic',
            ))
            continue

        if len(seen) > 1:
            used = ', '.join(f'"{c}" ({n}x)' for c, n in sorted(seen.items()))
            issues.append(QualityIssue(
                category=IssueCategory.TERMINOLOGY, severity=Severity.HIGH,
                problem=(
                    f'"{source_term}" is rendered inconsistently across the '
                    f'document: {used}. A reader cannot tell whether one '
                    f'concept or several are meant.'
                ),
                correction=(
                    'Choose one rendering and use it throughout, unless the '
                    'context genuinely refers to different concepts.'
                ),
                source_span=source_term,
                target_span=sorted(seen)[0],
                rule='terminology.inconsistent-rendering',
                confidence=1.0,
                detector='deterministic',
            ))
            continue

        only = next(iter(seen))
        if any(_fold(only) == _fold(v) for v in forbidden):
            issues.append(QualityIssue(
                category=IssueCategory.TERMINOLOGY, severity=Severity.HIGH,
                problem=(
                    f'"{source_term}" is consistently rendered "{only}", which '
                    f'the source analysis flagged as collapsing a distinction '
                    f'this material draws.'
                ),
                correction=f'Render "{source_term}" as "{target_term}".',
                source_span=source_term, target_span=only,
                rule='terminology.forbidden-variant',
                confidence=_confidence_of(anchor),
                detector='deterministic',
            ))

    return issues


def check_distinct_concepts(
    target_text: str, anchors: Sequence[Dict], segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    Two source terms must not collapse onto one target rendering.

    This is task 4130's finding 5 stated generally: the source distinguished
    "fiduciary duty" from "duty of care", and a translation that renders both
    with the same French phrase has silently merged two legal concepts. The
    same failure shape occurs everywhere -- "efficacy" and "effectiveness" in
    a clinical trial, "precision" and "accuracy" in metrology, "revenue" and
    "income" in accounts.

    Detectable without any domain knowledge, because the ANCHORS carry the
    distinction: if analysis returned two entries with different
    source_terms and an identical target_term, the merge has already
    happened in the plan, before a word was translated.
    """
    by_target: Dict[str, List[str]] = {}
    for anchor in anchors or []:
        source_term = str(anchor.get('source_term') or '').strip()
        target_term = str(anchor.get('target_term') or '').strip()
        if not source_term or not target_term:
            continue
        by_target.setdefault(_fold(target_term), []).append(source_term)

    issues = []
    for folded_target, source_terms in by_target.items():
        unique_sources = sorted({_fold(t): t for t in source_terms}.values())
        if len(unique_sources) < 2:
            continue
        # Only report if the collapsed rendering is actually in the output;
        # an anchor list that merges two terms is a plan defect, but it only
        # damages the deliverable once the merged term is used.
        if not _contains_term(_fold(target_text), folded_target):
            continue
        issues.append(QualityIssue(
            category=IssueCategory.TERMINOLOGY, severity=Severity.HIGH,
            problem=(
                'The source distinguishes ' +
                ', '.join(f'"{term}"' for term in unique_sources) +
                ', but they share one rendering in the output. A distinction '
                'the source draws has been collapsed.'
            ),
            correction=(
                'Give each source concept its own rendering so the '
                'distinction survives.'
            ),
            source_span=' / '.join(unique_sources),
            rule='terminology.collapsed-distinction',
            segment_index=segment_index, detector='deterministic',
        ))
    return issues


def check_entity_preservation(
    target_text: str, entities: Sequence[Dict], segment_index: Optional[int] = None
) -> List[QualityIssue]:
    """
    Low-confidence entities must survive as the source wrote them (spec 11,
    spec 15).

    The direction of this check is the point. It does NOT verify that a name
    was corrected; it verifies that an UNCERTAIN name was not. A model
    quietly turning "Ira Buschi" into a confident-looking "Ira Bushey" it
    cannot support is precisely the invention RULE 15 forbids, and it is
    invisible downstream -- the corrupted version reads perfectly.

    High-confidence normalizations are exempt: those were authorized by
    analysis and applied deliberately, and the prompt asked for them.
    """
    from .prompts import HIGH_CONFIDENCE

    target_folded = _fold(target_text)
    issues = []
    for entity in entities or []:
        surface = str(entity.get('surface_form') or '').strip()
        if not surface or len(surface) < 3:
            continue
        # Skip entity classes that SHOULD change in translation.
        #
        # A real run flagged "American" as an unpreserved entity because the
        # French correctly says "américain". Nationalities, languages,
        # demonyms and places are category words with established
        # equivalents in every target language -- translating them is right,
        # and demanding they survive verbatim produces a finding on correct
        # work. Only names that identify one specific thing (a person, a
        # company, a case, a product, a drug) are invariant, and those are
        # the ones a phonetic ASR error actually corrupts.
        if _is_translatable_class(entity.get('entity_type')):
            continue
        try:
            confidence = float(entity.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        normalized = str(entity.get('normalized') or '').strip()

        # Authorized correction: the normalized form is what should appear.
        if normalized and normalized != surface and confidence >= HIGH_CONFIDENCE:
            continue
        if _contains_term(target_folded, surface):
            continue
        issues.append(QualityIssue(
            category=IssueCategory.ENTITY, severity=Severity.MEDIUM,
            problem=(
                f'"{surface}" ({entity.get("entity_type") or "entity"}) does '
                f'not appear in the output. It was not confident enough to '
                f'normalize, so the source form should have been preserved.'
            ),
            correction=(
                f'Restore "{surface}" as the source has it. Do not substitute '
                f'a spelling that cannot be supported.'
            ),
            source_span=surface,
            rule='entity.uncertain-entity-altered',
            confidence=1.0 - confidence,
            segment_index=segment_index, detector='deterministic',
        ))
    return issues


# Entity classes whose members legitimately change in translation.
#
# Matched as substrings of whatever label the analysis stage chose, because
# that label is free-form by design (spec 1 / RULE 8: we do not constrain
# the model to a fixed taxonomy). So "Nationality", "nationality_adjective"
# and "demonym" all match, and an unrecognized label falls through to being
# treated as invariant -- the conservative direction, since a missed check
# only loses a finding while a wrong one blocks correct work.
_TRANSLATABLE_ENTITY_CLASSES = (
    'nationalit', 'demonym', 'language', 'langue', 'ethnic',
    'location', 'place', 'country', 'city', 'region', 'geograph',
    'currency', 'date', 'time', 'number', 'unit',
)


def _is_translatable_class(entity_type) -> bool:
    label = str(entity_type or '').strip().casefold()
    if not label:
        return False
    return any(token in label for token in _TRANSLATABLE_ENTITY_CLASSES)


def _confidence_of(anchor: Dict) -> float:
    try:
        value = float(anchor.get('confidence') or 0.0)
    except (TypeError, ValueError):
        return 0.5
    # An anchor with no stated confidence is treated as moderately reliable
    # rather than worthless: analysis produced it deliberately, and zeroing
    # it would make every finding it raises look like noise in the report.
    return value if value > 0 else 0.5


def run_anchor_checks(
    source_text: str,
    target_text: str,
    domain_profile,
    segment_index: Optional[int] = None,
    pairs: Optional[Sequence[Dict]] = None,
) -> List[QualityIssue]:
    """
    Every anchor-derived deterministic check, in one call. Applied at
    DOCUMENT level (segment_index=None) by the engine, because consistency
    is a property of the whole range and not of any one segment.

    `pairs` is optional only for backward compatibility with callers that
    have the joined text but not the alignment. Pass it when you have it:
    drift detection needs per-occurrence alignment, and a joined blob cannot
    distinguish "used twice, the same" from "used twice, differently".
    """
    if domain_profile is None:
        return []
    issues = []
    issues += check_terminology_consistency(
        target_text, source_text, domain_profile.terminology, segment_index
    )
    if pairs:
        issues += check_rendering_consistency(pairs, domain_profile.terminology)
    issues += check_distinct_concepts(
        target_text, domain_profile.terminology, segment_index
    )
    issues += check_entity_preservation(
        target_text, domain_profile.entities, segment_index
    )
    return issues
