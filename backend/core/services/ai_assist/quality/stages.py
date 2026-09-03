"""
The three provider-backed stages: analyse, audit, repair.

Each is a plain function taking a provider and returning typed results plus
a TokenUsage, so the orchestrator in engine.py owns all control flow and
none of the wire handling, and so every stage is testable with the
FakeStructuredProvider the existing suite already has.

WHAT THIS MODULE REFUSES TO DO is as important as what it does:

  * It never decides acceptance. Stages report; engine.py judges.
  * It never trusts a returned index. A repair response is validated against
    the exact chunk that was sent, the same way
    ai_assist.processor._validate_and_map guards segment alignment -- and
    for a sharper reason here, since a mis-mapped repair would write one
    segment's corrected text onto a different segment's timestamp.
  * It never raises past a recoverable content failure. A malformed audit
    response degrades to "this chunk was not audited", which engine.py turns
    into a review-required verdict. It must never become a silent pass.
"""
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...translation.batching import batch_by_size
from ..errors import AIResponseSchemaError, SegmentIntegrityError
from ..providers import TokenUsage, int_config, size_output_tokens
from . import prompts
from .issues import QualityIssue
from .profiles import DomainProfile
from .schemas import ANALYSIS_SCHEMA, AUDIT_SCHEMA, REPAIR_SCHEMA

logger = logging.getLogger('AIPipeline')

# An audit chunk carries the source AND the candidate, so its character
# budget covers roughly twice the text an ai_assist translation batch does.
# Larger than that batch on purpose all the same: terminology drift and
# completeness are only visible across a span, and a chunk of one segment
# can no more detect an inconsistent rendering than a single word can.
DEFAULT_AUDIT_MAX_ITEMS = 40
DEFAULT_AUDIT_MAX_CHARS = 12000

# Analysis reads a SAMPLE, not the whole document. Domain and register are
# established within a few thousand characters, and paying to classify a
# two-hour lecture in full would cost more than the translation it is meant
# to protect.
DEFAULT_ANALYSIS_SAMPLE_CHARS = 6000


def _text_of(pair: Dict) -> str:
    return (pair.get('source') or '') + (pair.get('target') or '')


def build_audit_chunks(pairs: List[Dict]) -> List[List[Dict]]:
    return batch_by_size(
        list(pairs),
        _text_of,
        max_items=int_config('AI_QUALITY_AUDIT_MAX_ITEMS', DEFAULT_AUDIT_MAX_ITEMS),
        max_chars=int_config('AI_QUALITY_AUDIT_MAX_CHARS', DEFAULT_AUDIT_MAX_CHARS),
    )


# ---------------------------------------------------------------------- #
# Stage: analysis
# ---------------------------------------------------------------------- #

def _sample_source(pairs: List[Dict], limit: int) -> List[Dict]:
    """
    A head-and-tail sample of the source.

    Both ends, not just the head: a lecture opens with administrative
    throat-clearing that classifies as "general" and only reaches its actual
    subject minutes in, while a document's closing section is where
    citations and formal register concentrate. Sampling only the beginning
    reliably mis-classifies exactly the long material where getting the
    domain right matters most.
    """
    if not pairs:
        return []
    half = max(1, limit // 2)

    head, used = [], 0
    for pair in pairs:
        text = pair.get('source') or ''
        if used + len(text) > half and head:
            break
        head.append(pair)
        used += len(text)

    tail, used = [], 0
    for pair in reversed(pairs[len(head):]):
        text = pair.get('source') or ''
        if used + len(text) > half and tail:
            break
        tail.append(pair)
        used += len(text)

    return head + list(reversed(tail))


def analyze_source(
    provider: Any,
    pairs: List[Dict],
    target_language: Optional[str] = None,
    source_language: Optional[str] = None,
) -> Tuple[DomainProfile, TokenUsage]:
    """
    One call establishing domain, register, risk, terminology and entities.

    A failure here is NOT fatal. `DomainProfile.unknown()` is returned
    instead, which carries risk_level='high' -- so a document we could not
    classify is audited more strictly, not less. Falling back to "general,
    standard risk" would let a failed analysis quietly relax the gate.
    """
    sample = _sample_source(
        pairs,
        int_config('AI_QUALITY_ANALYSIS_SAMPLE_CHARS', DEFAULT_ANALYSIS_SAMPLE_CHARS),
    )
    if not sample:
        return DomainProfile.unknown(source_language), TokenUsage()

    payload = prompts.build_pair_payload(
        [{'source': pair.get('source') or ''} for pair in sample]
    )
    prompt = prompts.build_analysis_prompt(payload, target_language=target_language)

    try:
        response, usage = provider.generate_json(
            prompts.ANALYSIS_SYSTEM, prompt, ANALYSIS_SCHEMA
        )
    except (AIResponseSchemaError, SegmentIntegrityError) as exc:
        logger.warning('[AI QUALITY] Source analysis returned an unusable response: %s', exc.detail)
        return DomainProfile.unknown(source_language), TokenUsage()

    if not isinstance(response, dict):
        return DomainProfile.unknown(source_language), usage

    profile = DomainProfile(
        domain=response.get('domain') or 'general',
        subdomain=response.get('subdomain') or '',
        register=response.get('register') or 'neutral',
        audience=response.get('audience') or '',
        risk_level=response.get('risk_level') or 'standard',
        is_transcript=response.get('is_transcript', True),
        terminology=_clean_terms(response.get('terminology')),
        entities=_clean_entities(response.get('entities')),
        formatting_notes=response.get('formatting_notes') or '',
        confidence=response.get('confidence', 0.0),
        source_language=source_language or '',
        notes=response.get('notes') or '',
    )
    logger.info(
        '[AI QUALITY] Analysis: domain=%s subdomain=%s register=%s risk=%s '
        'terms=%d entities=%d confidence=%.2f',
        profile.domain, profile.subdomain or '-', profile.register,
        profile.risk_level, len(profile.terminology), len(profile.entities),
        profile.confidence,
    )
    return profile, usage


def _clean_terms(raw: Any) -> List[Dict]:
    """Drops entries missing either half of the mapping. A term with no
    target rendering anchors nothing, and rendering it into a prompt as
    '"principal" -> ""' actively invites the model to leave it blank."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source_term = str(entry.get('source_term') or '').strip()
        target_term = str(entry.get('target_term') or '').strip()
        if not source_term or not target_term:
            continue
        forbidden = entry.get('forbidden_variants')
        cleaned.append({
            'source_term': source_term,
            'target_term': target_term,
            'context': str(entry.get('context') or '').strip(),
            'confidence': _float_or(entry.get('confidence'), 0.0),
            'forbidden_variants': [
                str(v).strip() for v in forbidden if str(v).strip()
            ] if isinstance(forbidden, list) else [],
        })
    return cleaned


def _clean_entities(raw: Any) -> List[Dict]:
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        surface = str(entry.get('surface_form') or '').strip()
        if not surface:
            continue
        cleaned.append({
            'surface_form': surface,
            'entity_type': str(entry.get('entity_type') or 'entity').strip(),
            'normalized': str(entry.get('normalized') or '').strip(),
            'confidence': _float_or(entry.get('confidence'), 0.0),
            'evidence': str(entry.get('evidence') or '').strip(),
        })
    return cleaned


def _float_or(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------- #
# Stage: audit
# ---------------------------------------------------------------------- #

class AuditResult:
    """What one audit pass concluded over the whole reviewed range."""

    __slots__ = ('issues', 'confidence', 'model_status', 'usage', 'chunks_failed')

    def __init__(self, issues, confidence, model_status, usage, chunks_failed=0):
        self.issues = issues
        self.confidence = confidence
        # The model's own verdict, recorded but never consulted by the gate.
        self.model_status = model_status
        self.usage = usage
        # Chunks whose response could not be used. Non-zero means part of
        # the range was NOT audited, which engine.py must never read as
        # "audited and clean".
        self.chunks_failed = chunks_failed


def audit_pairs(
    provider: Any,
    pairs: List[Dict],
    target_language: Optional[str] = None,
    domain_profile: Optional[DomainProfile] = None,
    deterministic_issues: Optional[List[QualityIssue]] = None,
    previous_issues: Optional[List[QualityIssue]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> AuditResult:
    """
    Adversarially reviews `pairs`, chunk by chunk.

    Chunk-local indices are translated back to range-global ones before the
    issues are returned, so every caller downstream -- the repair prompt, the
    stored report, the UI -- speaks one coordinate system. Getting this wrong
    would point every finding after the first chunk at the wrong segment.
    """
    chunks = build_audit_chunks(pairs)
    terminology = domain_profile.terminology if domain_profile else []
    entities = domain_profile.entities if domain_profile else []

    all_issues: List[QualityIssue] = []
    confidences: List[float] = []
    usage = TokenUsage()
    statuses = set()
    chunks_failed = 0
    offset = 0

    for position, chunk in enumerate(chunks):
        if should_cancel is not None and should_cancel():
            break

        payload = prompts.build_pair_payload(chunk)
        chunk_deterministic = _issues_in_range(
            deterministic_issues or [], offset, offset + len(chunk)
        )
        prompt = prompts.build_audit_prompt(
            payload,
            target_language=target_language,
            domain_profile=domain_profile,
            terminology=terminology,
            entities=entities,
            deterministic_findings=[
                _issue_line(issue, issue.segment_index - offset
                            if issue.segment_index is not None else None)
                for issue in chunk_deterministic
            ],
            previous_issues=[
                _issue_line(issue, None) for issue in (previous_issues or [])
            ],
        )

        try:
            response, chunk_usage = provider.generate_json(
                prompts.AUDIT_SYSTEM, prompt, AUDIT_SCHEMA,
                # An audit emits findings, not a copy of the text, so it needs
                # far less room than a translation of the same chunk.
                max_output_tokens=size_output_tokens(
                    sum(len(_text_of(pair)) for pair in chunk) // 4
                ),
            )
            usage = usage + chunk_usage
        except (AIResponseSchemaError, SegmentIntegrityError) as exc:
            logger.warning(
                '[AI QUALITY] Audit chunk %d/%d returned an unusable response: %s',
                position + 1, len(chunks), exc.detail,
            )
            chunks_failed += 1
            offset += len(chunk)
            continue

        parsed, chunk_confidence, status = _parse_audit(response, offset, len(chunk))
        all_issues.extend(parsed)
        if chunk_confidence is not None:
            confidences.append(chunk_confidence)
        statuses.add(status)
        offset += len(chunk)

    # The MINIMUM, not the mean. Confidence gates acceptance for the strict
    # profiles, and averaging lets a long stretch of easy, confidently-clean
    # text drown out the one chunk the auditor was unsure about -- which is
    # precisely the chunk the gate exists to catch.
    confidence = min(confidences) if confidences else 0.0
    model_status = 'issues_found' if 'issues_found' in statuses else (
        'clean' if statuses else 'unknown'
    )
    return AuditResult(all_issues, confidence, model_status, usage, chunks_failed)


def _issues_in_range(issues: List[QualityIssue], start: int, end: int) -> List[QualityIssue]:
    return [
        issue for issue in issues
        if issue.segment_index is None or start <= issue.segment_index < end
    ]


def _issue_line(issue: QualityIssue, local_index: Optional[int]) -> str:
    where = f'segment {local_index}' if isinstance(local_index, int) else 'document-wide'
    return (
        f'[{issue.severity.value}/{issue.category.value}] {where} '
        f'({issue.rule or "unspecified"}): {issue.problem}'
    )


def _parse_audit(
    response: Dict, offset: int, chunk_length: int
) -> Tuple[List[QualityIssue], Optional[float], str]:
    """
    Turns one chunk's response into issues with range-global indices.

    An out-of-range segment_index is kept as a DOCUMENT-LEVEL issue rather
    than discarded or clamped. Discarding would let a model suppress a
    finding by mis-numbering it; clamping would attach it to an innocent
    segment and send the repair pass to edit the wrong text. Neither is
    acceptable, and "somewhere in this document" is both true and useful.
    """
    raw_issues = response.get('issues')
    if not isinstance(raw_issues, list):
        raw_issues = []

    issues = []
    for entry in raw_issues:
        if not isinstance(entry, dict):
            continue
        problem = str(entry.get('problem') or '').strip()
        if not problem:
            # A finding with no description cannot be repaired and cannot be
            # reviewed. Dropping it is safe because it blocks nothing that a
            # real finding elsewhere would not already block.
            continue
        local_index = entry.get('segment_index')
        if (
            isinstance(local_index, int)
            and not isinstance(local_index, bool)
            and 0 <= local_index < chunk_length
        ):
            global_index = offset + local_index
        else:
            global_index = None
        issue = QualityIssue.from_dict(entry, detector='ai')
        issue.segment_index = global_index
        issues.append(issue)

    try:
        confidence = max(0.0, min(1.0, float(response.get('confidence'))))
    except (TypeError, ValueError):
        confidence = None

    status = str(response.get('status') or 'unknown').strip().lower()
    return issues, confidence, status


# ---------------------------------------------------------------------- #
# Stage: repair
# ---------------------------------------------------------------------- #

def repair_pairs(
    provider: Any,
    pairs: List[Dict],
    issues: List[QualityIssue],
    target_language: Optional[str] = None,
    domain_profile: Optional[DomainProfile] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Tuple[List[str], TokenUsage, int]:
    """
    Applies `issues` to the candidate texts.

    Returns (texts, usage, chunks_failed) where `texts` is always exactly as
    long as `pairs`. A chunk whose response fails validation contributes its
    ORIGINAL candidate text unchanged -- the same degrade-rather-than-guess
    policy as ai_assist.processor, and for the same reason: a partially
    applied repair mapped onto the wrong segments is worse than no repair,
    because the audit that follows would be reviewing text nobody produced
    deliberately.
    """
    terminology = domain_profile.terminology if domain_profile else []
    entities = domain_profile.entities if domain_profile else []

    chunks = build_audit_chunks(pairs)
    repaired: List[str] = []
    usage = TokenUsage()
    chunks_failed = 0
    offset = 0

    for position, chunk in enumerate(chunks):
        original = [pair.get('target') or '' for pair in chunk]

        if should_cancel is not None and should_cancel():
            repaired.extend(original)
            offset += len(chunk)
            continue

        chunk_issues = _issues_in_range(issues, offset, offset + len(chunk))
        if not chunk_issues:
            # Nothing to fix here. Not calling the provider at all is the
            # single biggest cost saving in the loop: on a long document the
            # findings usually cluster in a handful of chunks.
            repaired.extend(original)
            offset += len(chunk)
            continue

        payload = prompts.build_pair_payload(chunk)
        issue_dicts = []
        for issue in chunk_issues:
            data = issue.to_dict()
            if issue.segment_index is not None:
                data['segment_index'] = issue.segment_index - offset
            issue_dicts.append(data)

        prompt = prompts.build_repair_prompt(
            payload, issue_dicts,
            target_language=target_language,
            domain_profile=domain_profile,
            terminology=terminology,
            entities=entities,
        )

        try:
            response, chunk_usage = provider.generate_json(
                prompts.REPAIR_SYSTEM, prompt, REPAIR_SCHEMA,
                # Repair returns every segment's full text, so it needs the
                # same room a translation of that chunk would.
                max_output_tokens=size_output_tokens(
                    sum(len(pair.get('target') or '') for pair in chunk)
                ),
            )
            usage = usage + chunk_usage
            repaired.extend(_validate_repair(response, original))
        except (AIResponseSchemaError, SegmentIntegrityError) as exc:
            logger.warning(
                '[AI QUALITY] Repair chunk %d/%d unusable, keeping candidate: %s',
                position + 1, len(chunks), exc.detail,
            )
            chunks_failed += 1
            repaired.extend(original)

        offset += len(chunk)

    return repaired, usage, chunks_failed


def _validate_repair(response: Dict, original: List[str]) -> List[str]:
    """
    Maps a repair response onto the chunk that produced it, or raises.

    Exact set equality against range(len(original)) -- the identical check
    ai_assist.processor._validate_and_map performs, rejecting a short
    response, a long one, a duplicate and an invented index in one
    comparison.

    An empty repaired_text for a non-empty candidate keeps the candidate.
    A repair pass that blanks a segment has deleted content, not fixed it,
    and an Accept All must never be able to erase speech that was said.
    """
    raw = response.get('segments')
    if not isinstance(raw, list):
        raise AIResponseSchemaError(
            f"Repair response has no 'segments' array (keys: {sorted(response)!r})."
        )

    by_index: Dict[int, Dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise AIResponseSchemaError(
                f"Expected objects in 'segments', got {type(entry).__name__}."
            )
        index = entry.get('segment_index')
        if not isinstance(index, int) or isinstance(index, bool):
            raise SegmentIntegrityError(f'Non-integer segment_index: {index!r}.')
        if index in by_index:
            raise SegmentIntegrityError(f'Duplicate segment_index {index}.')
        by_index[index] = entry

    expected = set(range(len(original)))
    if set(by_index) != expected:
        missing = sorted(expected - set(by_index))
        unexpected = sorted(set(by_index) - expected)
        raise SegmentIntegrityError(
            f'Repair indices do not match the request. Sent {len(original)}, '
            f'got {len(by_index)}; missing={missing[:10]}, unexpected={unexpected[:10]}.'
        )

    result = []
    for index, candidate in enumerate(original):
        text = by_index[index].get('repaired_text')
        if not isinstance(text, str):
            raise AIResponseSchemaError(
                f'repaired_text for segment {index} was '
                f'{type(text).__name__}, expected a string.'
            )
        text = text.strip()
        if not text and candidate.strip():
            logger.warning(
                '[AI QUALITY] Repair returned empty text for segment %d; '
                'keeping the candidate.', index,
            )
            text = candidate
        result.append(text)
    return result
