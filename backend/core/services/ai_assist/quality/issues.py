"""
The vocabulary the whole quality layer speaks: what a defect is, how bad it
is, and what a finished audit concluded.

Kept free of Django, provider, and prompt imports on purpose -- every other
module in this package imports this one, and a deterministic checker must be
testable without an API key or a database.

SEVERITY IS A GATE INPUT, NOT A LABEL. `Severity` is ordered, and
`QualityPolicy` (profiles.py) decides acceptance by comparing counts per
level. A model that invents its own severity string therefore cannot widen
the gate: `Severity.parse` maps anything unrecognized to HIGH, which blocks
rather than passes. Failing closed is the whole point -- an unparseable
severity means we do not know how bad the defect is, and "unknown" must
never be cheaper than "known bad".
"""
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    """Ordered worst-first. `str` mixin so these serialize as plain strings
    into JSONField and WebSocket payloads with no custom encoder."""

    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @classmethod
    def parse(cls, raw: Any) -> 'Severity':
        """
        Coerces a provider-supplied string to a Severity, failing CLOSED.

        Anything we cannot recognize becomes HIGH, which the default policy
        refuses to accept. The alternative -- defaulting to LOW, or dropping
        the issue -- would mean a model could bypass the acceptance gate by
        returning "severity": "moderate", and the run would be marked
        validated on the strength of a typo.
        """
        if isinstance(raw, cls):
            return raw
        try:
            return cls(str(raw).strip().lower())
        except (ValueError, AttributeError):
            return cls.HIGH


_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}

# Blocking by default. Named here rather than inlined in the gate so the
# policy and its tests refer to the same tuple.
BLOCKING_SEVERITIES = (Severity.CRITICAL, Severity.HIGH)


class IssueCategory(str, Enum):
    """
    What KIND of defect this is -- orthogonal to how bad it is.

    The distinction is load-bearing in two places. `QualityPolicy` blocks
    some categories at any severity (a completeness failure is never "minor
    enough"), and repair.py groups issues by category because the repair
    instruction for a terminology drift reads nothing like the one for a
    dropped negation.
    """

    SEMANTIC = 'semantic'              # meaning diverges from source
    COMPLETENESS = 'completeness'      # omission, addition, duplication
    TERMINOLOGY = 'terminology'        # inconsistent or wrong domain term
    ENTITY = 'entity'                  # name/org/citation corrupted
    NUMERIC = 'numeric'                # number, unit, dose, date, currency
    DOMAIN = 'domain'                  # domain convention violated
    LINGUISTIC = 'linguistic'          # target-language quality/register
    TYPOGRAPHY = 'typography'          # spacing, quotes, separators
    STRUCTURE = 'structure'            # markup, brackets, formatting integrity
    TRANSCRIPT = 'transcript'          # transcript artifact mishandled
    LEAKAGE = 'leakage'                # untranslated source / AI commentary

    @classmethod
    def parse(cls, raw: Any) -> 'IssueCategory':
        """Unrecognized categories become SEMANTIC -- the category the
        default policy treats most strictly -- for the same fail-closed
        reason as Severity.parse."""
        if isinstance(raw, cls):
            return raw
        try:
            return cls(str(raw).strip().lower())
        except (ValueError, AttributeError):
            return cls.SEMANTIC


class QualityStatus(str, Enum):
    """
    The three outcomes a caller must be able to tell apart (spec 35).

    NOT the same axis as AIRevision.status. A run can finish technically
    (status='completed') while its content is untrustworthy
    (quality_status='quality_review_required'), and conflating those is
    exactly the bug that let task 4130 ship: "the job finished" was read as
    "the text is good".
    """

    NOT_RUN = 'not_run'
    VALIDATED = 'validated'
    QUALITY_REVIEW_REQUIRED = 'quality_review_required'
    TECHNICAL_FAILURE = 'technical_failure'


class QualityIssue:
    """
    One defect, from an LLM auditor or a deterministic checker alike.

    `segment_index` is the position within the reviewed range, matching the
    positional ids used in prompts.build_payload -- never a database UUID
    (same reasoning: identifiers do not leave the server, and ints are
    cheaper). None means the issue is document-level, e.g. a terminology
    inconsistency that has no single home.

    `source_span` / `target_span` are quoted excerpts, deliberately not
    offsets: a repair pass edits text, so an offset computed against one
    iteration is meaningless in the next, whereas a quote stays findable.
    """

    __slots__ = (
        'category', 'severity', 'problem', 'correction', 'source_span',
        'target_span', 'rule', 'confidence', 'segment_index', 'detector',
    )

    def __init__(
        self,
        category,
        severity,
        problem: str,
        correction: str = '',
        source_span: str = '',
        target_span: str = '',
        rule: str = '',
        confidence: float = 1.0,
        segment_index: Optional[int] = None,
        detector: str = 'ai',
    ):
        self.category = IssueCategory.parse(category)
        self.severity = Severity.parse(severity)
        self.problem = str(problem or '').strip()
        self.correction = str(correction or '').strip()
        self.source_span = str(source_span or '').strip()
        self.target_span = str(target_span or '').strip()
        self.rule = str(rule or '').strip()
        try:
            self.confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            self.confidence = 1.0
        self.segment_index = (
            segment_index
            if isinstance(segment_index, int) and not isinstance(segment_index, bool)
            else None
        )
        # 'ai' or 'deterministic'. Recorded because they carry different
        # weight: a deterministic numeric mismatch is a fact, an LLM's
        # semantic objection is an opinion, and spec 19 / RULE 4 require that
        # the former is never silently replaced by the latter.
        self.detector = detector or 'ai'

    @property
    def is_blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

    def to_dict(self) -> Dict:
        return {
            'category': self.category.value,
            'severity': self.severity.value,
            'problem': self.problem,
            'correction': self.correction,
            'source_span': self.source_span,
            'target_span': self.target_span,
            'rule': self.rule,
            'confidence': round(self.confidence, 4),
            'segment_index': self.segment_index,
            'detector': self.detector,
        }

    @classmethod
    def from_dict(cls, raw: Dict, detector: str = 'ai') -> 'QualityIssue':
        return cls(
            category=raw.get('category'),
            severity=raw.get('severity'),
            problem=raw.get('problem') or '',
            correction=raw.get('correction') or '',
            source_span=raw.get('source_span') or '',
            target_span=raw.get('target_span') or '',
            rule=raw.get('rule') or '',
            confidence=raw.get('confidence', 1.0),
            segment_index=raw.get('segment_index'),
            detector=raw.get('detector') or detector,
        )

    def __repr__(self):
        where = '' if self.segment_index is None else f' @{self.segment_index}'
        return (
            '<QualityIssue ' + self.severity.value + '/' + self.category.value
            + where + ': ' + self.problem[:60] + '>'
        )


def summarize(issues: List[QualityIssue]) -> Dict[str, int]:
    """Counts per severity, every level present even at zero -- so a caller
    reading counts['critical'] never has to guard for a missing key, and a
    serialized report has a stable shape."""
    counts = {level.value: 0 for level in Severity}
    for issue in issues:
        counts[issue.severity.value] += 1
    return counts


def sort_issues(issues: List[QualityIssue]) -> List[QualityIssue]:
    """Worst first, then by position. What the repair prompt and the UI both
    want, and stable so two runs over the same issue set order identically."""
    return sorted(
        issues,
        key=lambda i: (
            i.severity.rank,
            i.segment_index if i.segment_index is not None else -1,
            i.category.value,
        ),
    )
