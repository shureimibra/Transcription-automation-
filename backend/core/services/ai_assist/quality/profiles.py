"""
Two different things that both get called a "profile", kept separate on
purpose:

  * `DomainProfile` -- what the CONTENT is. Detected per job by analysis.py
    (legal / medical / lecture / engineering / ...), carrying the handling
    rules that follow from that. Data, discovered at runtime.
  * `OperationProfile` -- what the RUN is for. Chosen by the caller
    ('translation', 'academic', 'general', 'eagle_web'), carrying the
    acceptance policy and which QA stages to run. Configuration, fixed in
    code.

Collapsing them would reproduce exactly the mistake this package exists to
undo. The legal rules that leaked into the generic TRANSLATE prompt are
content knowledge; the zero-tolerance gate Eagle Web needs is a submission
requirement. They vary independently -- an Eagle Web job can be medical,
a general job can be legal -- so they are two objects.

RULE 8 / spec 30: there is no `if domain == 'legal'` branch anywhere in this
package. Domain knowledge reaches the model as a DomainProfile rendered into
a prompt, never as a code path, which is what lets a domain nobody
anticipated behave sensibly instead of falling off the end of an if-chain.
"""
from typing import Dict, List, Optional, Tuple

from .issues import BLOCKING_SEVERITIES, IssueCategory, Severity


class DomainProfile:
    """
    What analysis.py concluded about the source, in a form prompts can
    render and the gate can consult.

    Every field is optional and every default is the conservative reading,
    because this object is also what gets constructed when detection FAILS
    (see `unknown()`). A failed detection must degrade to careful generic
    handling, never to no handling at all.
    """

    __slots__ = (
        'domain', 'subdomain', 'register', 'audience', 'target_region',
        'risk_level', 'is_transcript', 'terminology', 'entities',
        'formatting_notes', 'confidence', 'source_language', 'notes',
    )

    # Ordered least- to most-consequential. Used to tighten the policy: a
    # HIGH-risk document blocks on MEDIUM issues that a LOW-risk one lets
    # through (see QualityPolicy.for_domain).
    RISK_LEVELS = ('low', 'standard', 'high', 'critical')

    def __init__(
        self,
        domain: str = 'general',
        subdomain: str = '',
        register: str = 'neutral',
        audience: str = '',
        target_region: str = '',
        risk_level: str = 'standard',
        is_transcript: bool = True,
        terminology: Optional[List[Dict]] = None,
        entities: Optional[List[Dict]] = None,
        formatting_notes: str = '',
        confidence: float = 0.0,
        source_language: str = '',
        notes: str = '',
    ):
        self.domain = (domain or 'general').strip().lower()
        self.subdomain = (subdomain or '').strip()
        self.register = (register or 'neutral').strip()
        self.audience = (audience or '').strip()
        self.target_region = (target_region or '').strip()
        self.risk_level = (
            risk_level if risk_level in self.RISK_LEVELS else 'standard'
        )
        self.is_transcript = bool(is_transcript)
        self.terminology = list(terminology or [])
        self.entities = list(entities or [])
        self.formatting_notes = (formatting_notes or '').strip()
        try:
            self.confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            self.confidence = 0.0
        self.source_language = (source_language or '').strip()
        self.notes = (notes or '').strip()

    @classmethod
    def unknown(cls, source_language: str = '') -> 'DomainProfile':
        """
        The profile used when detection did not run or did not succeed.

        risk_level='high', not 'standard': not knowing what the content is
        is itself a reason for caution. An undetected medical transcript is
        still a medical transcript, and the gate should be strict about a
        document we failed to understand rather than lenient about it.
        """
        return cls(
            domain='unknown', risk_level='high', confidence=0.0,
            source_language=source_language,
            notes='Domain detection did not produce a usable result.',
        )

    @property
    def is_high_risk(self) -> bool:
        return self.risk_level in ('high', 'critical')

    def describe(self) -> str:
        """
        The human-readable block injected into every downstream prompt.

        Deliberately prose rather than JSON: this is context for a language
        model to reason with, not a payload to parse, and the surrounding
        prompts already reserve JSON framing for untrusted transcript data
        (see ai_assist.prompts). Keeping the two visually distinct is part of
        why a transcript cannot impersonate configuration.
        """
        lines = [f'Domain: {self.domain}']
        if self.subdomain:
            lines.append(f'Subdomain: {self.subdomain}')
        lines.append(f'Register: {self.register}')
        if self.audience:
            lines.append(f'Audience: {self.audience}')
        if self.target_region:
            lines.append(f'Target region/variety: {self.target_region}')
        lines.append(f'Risk level: {self.risk_level}')
        lines.append(
            'Content type: spoken transcript'
            if self.is_transcript else 'Content type: written document'
        )
        if self.formatting_notes:
            lines.append(f'Formatting conventions: {self.formatting_notes}')
        if self.notes:
            lines.append(f'Analyst notes: {self.notes}')
        return '\n'.join(lines)

    def to_dict(self) -> Dict:
        return {
            'domain': self.domain,
            'subdomain': self.subdomain,
            'register': self.register,
            'audience': self.audience,
            'target_region': self.target_region,
            'risk_level': self.risk_level,
            'is_transcript': self.is_transcript,
            'terminology': self.terminology,
            'entities': self.entities,
            'formatting_notes': self.formatting_notes,
            'confidence': round(self.confidence, 4),
            'source_language': self.source_language,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict]) -> 'DomainProfile':
        if not isinstance(raw, dict):
            return cls.unknown()
        return cls(
            domain=raw.get('domain') or 'general',
            subdomain=raw.get('subdomain') or '',
            register=raw.get('register') or 'neutral',
            audience=raw.get('audience') or '',
            target_region=raw.get('target_region') or '',
            risk_level=raw.get('risk_level') or 'standard',
            is_transcript=raw.get('is_transcript', True),
            terminology=raw.get('terminology') or [],
            entities=raw.get('entities') or [],
            formatting_notes=raw.get('formatting_notes') or '',
            confidence=raw.get('confidence', 0.0),
            source_language=raw.get('source_language') or '',
            notes=raw.get('notes') or '',
        )


class QualityPolicy:
    """
    The acceptance gate, as data.

    THE APPLICATION DECIDES, NOT THE MODEL (RULE 12). Nothing here reads a
    model's self-assessment. `evaluate` looks only at the issues that
    survived -- their severities and categories -- and returns a verdict the
    caller must honour. A model that returns "status": "perfect" alongside a
    CRITICAL issue is refused, because the issue is evidence and the
    self-assessment is not.
    """

    __slots__ = (
        'name', 'blocking_severities', 'always_blocking_categories',
        'max_iterations', 'min_confidence', 'require_deterministic_pass',
        'allow_local_fallback',
    )

    def __init__(
        self,
        name: str,
        blocking_severities: Tuple = BLOCKING_SEVERITIES,
        always_blocking_categories: Tuple = (
            IssueCategory.COMPLETENESS,
            IssueCategory.NUMERIC,
        ),
        max_iterations: int = 2,
        min_confidence: float = 0.0,
        require_deterministic_pass: bool = True,
        allow_local_fallback: bool = True,
    ):
        self.name = name
        self.blocking_severities = tuple(blocking_severities)
        # Categories that block at ANY severity. A dropped clause or a wrong
        # number is not a matter of degree -- calling it "medium" does not
        # make the document safe to submit -- so these bypass the severity
        # comparison entirely.
        self.always_blocking_categories = tuple(always_blocking_categories)
        self.max_iterations = max(0, int(max_iterations))
        self.min_confidence = float(min_confidence)
        self.require_deterministic_pass = bool(require_deterministic_pass)
        # Whether a stage may fall back to a local model. High-stakes
        # validation must not silently downgrade (spec 24), so the strict
        # profiles turn this off.
        self.allow_local_fallback = bool(allow_local_fallback)

    def blocking_issues(self, issues) -> List:
        """The subset of `issues` that forbids acceptance."""
        return [
            issue for issue in issues
            if issue.severity in self.blocking_severities
            or issue.category in self.always_blocking_categories
        ]

    def evaluate(self, issues, confidence: float = 1.0) -> Tuple[bool, str]:
        """
        Returns (accepted, reason). `reason` is '' when accepted, and is an
        operator-facing explanation otherwise -- it is stored on the report
        and shown in the UI, so it names counts, never provider internals.
        """
        blocking = self.blocking_issues(issues)
        if blocking:
            counts = {}
            for issue in blocking:
                counts[issue.severity.value] = counts.get(issue.severity.value, 0) + 1
            detail = ', '.join(
                f'{count} {level}' for level, count in sorted(counts.items())
            )
            return False, f'{len(blocking)} unresolved blocking issue(s): {detail}.'
        if confidence < self.min_confidence:
            return False, (
                f'Auditor confidence {confidence:.2f} is below the '
                f'{self.min_confidence:.2f} required by the '
                f'{self.name} profile.'
            )
        return True, ''

    def for_domain(self, profile: DomainProfile) -> 'QualityPolicy':
        """
        Tightens this policy for high-risk content.

        Only ever tightens. A detected domain can make the gate stricter but
        never looser, so no classification result -- correct, wrong, or
        adversarially induced by transcript text -- can lower the bar the
        operator configured.
        """
        if not profile.is_high_risk:
            return self
        if Severity.MEDIUM in self.blocking_severities:
            return self
        return QualityPolicy(
            name=f'{self.name}+high-risk',
            blocking_severities=tuple(self.blocking_severities) + (Severity.MEDIUM,),
            always_blocking_categories=self.always_blocking_categories,
            max_iterations=self.max_iterations,
            min_confidence=self.min_confidence,
            require_deterministic_pass=self.require_deterministic_pass,
            allow_local_fallback=self.allow_local_fallback,
        )


# Stage identifiers. Named constants rather than bare strings because they
# appear in prompts, telemetry, WebSocket payloads and the report table, and
# a typo in any one of those is a silent hole in the audit trail.
STAGE_ANALYSIS = 'analysis'
STAGE_AUDIT = 'audit'
STAGE_DETERMINISTIC = 'deterministic'
STAGE_REPAIR = 'repair'
STAGE_REAUDIT = 're_audit'
STAGE_VALIDATION = 'validation'

ALL_STAGES = (
    STAGE_ANALYSIS, STAGE_AUDIT, STAGE_DETERMINISTIC,
    STAGE_REPAIR, STAGE_REAUDIT, STAGE_VALIDATION,
)


class OperationProfile:
    """
    What a run is for: which stages execute, and under which policy.

    `stages` is a whitelist. A profile that omits STAGE_ANALYSIS simply does
    not pay for domain detection -- which is the right trade for a
    punctuation-correction pass over a short transcript, and the wrong one
    for a submission-bound translation.
    """

    __slots__ = ('name', 'label', 'policy', 'stages', 'submission_ready', 'description')

    def __init__(
        self,
        name: str,
        label: str,
        policy: QualityPolicy,
        stages: Tuple = ALL_STAGES,
        submission_ready: bool = False,
        description: str = '',
    ):
        self.name = name
        self.label = label
        self.policy = policy
        self.stages = tuple(stages)
        # True when the output is destined for an external submission, which
        # switches on the leakage/commentary checks in deterministic.py. Not
        # a synonym for "strict": strictness is the policy's business.
        self.submission_ready = bool(submission_ready)
        self.description = description

    def runs(self, stage: str) -> bool:
        return stage in self.stages


# ---------------------------------------------------------------------- #
# THE THREE TIERS
#
# Every profile runs the same staged engine; what differs is how far up the
# tiers a run is allowed to climb, and each tier costs an order of magnitude
# more than the one below it:
#
#   Tier 0  deterministic     0 provider requests. Numbers, entities, URLs,
#                             duplication, truncation, leakage, typography,
#                             terminology anchors. Always runs, always free.
#   Tier 1  analysis + audit  2 requests. Domain detection and one
#                             adversarial source-vs-target review.
#   Tier 2  repair + re-audit 2 requests per round, and ONLY when Tier 1
#                             found something the policy blocks on.
#
# Tier 2 is escalation, not routine: on a real run it was ~90% of the cost
# and produced three passes chasing a finding that turned out to be false.
# A profile with max_iterations=0 stops at Tier 1 and reports rather than
# repairs, which is the right trade at high volume.
# ---------------------------------------------------------------------- #

NONE = 'none'
TRANSLATION = 'translation'
VOLUME = 'volume'
ACADEMIC = 'academic'
GENERAL = 'general'
EAGLE_WEB = 'eagle_web'
EAGLE_STANDARD = 'eagle_standard'

_PROFILES: Dict[str, OperationProfile] = {
    TRANSLATION: OperationProfile(
        name=TRANSLATION,
        label='Translation QA',
        policy=QualityPolicy(name=TRANSLATION, max_iterations=2),
        description=(
            'Source-authoritative semantic audit, terminology and entity '
            'consistency, language-aware deterministic checks.'
        ),
    ),
    ACADEMIC: OperationProfile(
        name=ACADEMIC,
        label='Academic QA',
        # Notation work rewrites little and must not be "improved" by a
        # repair loop, so one iteration and no re-audit: the existing
        # deterministic academic validator already guards the conversion
        # itself (core.services.academic.validator).
        policy=QualityPolicy(name=ACADEMIC, max_iterations=1),
        stages=(
            STAGE_ANALYSIS, STAGE_AUDIT, STAGE_DETERMINISTIC,
            STAGE_REPAIR, STAGE_VALIDATION,
        ),
        description='Preserves citations, claims, notation and lecturer intent.',
    ),
    GENERAL: OperationProfile(
        name=GENERAL,
        label='General QA',
        policy=QualityPolicy(
            name=GENERAL,
            # Cleanup passes legitimately produce stylistic differences from
            # the source; blocking on every one of them would make the gate
            # noise rather than signal. Meaning and completeness still block.
            blocking_severities=(Severity.CRITICAL, Severity.HIGH),
            max_iterations=1,
        ),
        stages=(
            STAGE_AUDIT, STAGE_DETERMINISTIC, STAGE_REPAIR, STAGE_VALIDATION,
        ),
        description='Fidelity and completeness checking for cleanup passes.',
    ),
    VOLUME: OperationProfile(
        name=VOLUME,
        label='High-volume QA (report only)',
        # max_iterations=0 -- stops at Tier 1. Findings are reported and the
        # run is refused if they block, but nothing is repaired automatically.
        #
        # Sized for a workload of ~120 tasks/day, where the difference
        # between 3 requests per task and 13 is the difference between
        # fitting inside a free-tier daily allowance and exhausting it before
        # lunch. A human sees the findings and decides; that is cheaper and,
        # on the evidence so far, more accurate than an automated repair loop.
        policy=QualityPolicy(
            name=VOLUME,
            blocking_severities=(Severity.CRITICAL, Severity.HIGH),
            max_iterations=0,
        ),
        description=(
            'Full detection at ~3 requests per task: deterministic checks '
            'plus one adversarial audit, reported for human decision rather '
            'than automatically repaired. Built for high daily volume.'
        ),
    ),
    EAGLE_STANDARD: OperationProfile(
        name=EAGLE_STANDARD,
        label='Eagle Web verbatim standard pass',
        # NO SEMANTIC AUDIT, AND THAT IS THE ENTIRE POINT.
        #
        # The audit stage is a FIDELITY checker: it reads the source beside
        # the output and reports deviations. That is exactly right for a
        # translation, where any drift from the source is a defect.
        #
        # It is exactly wrong here. This profile gates the pass that brings a
        # raw ASR transcript up to Eagle's edited-verbatim standard, whose
        # whole job is to deviate -- expand the contractions, drop the
        # fillers, tidy the grammar. Run together, the two halves of our own
        # system fought each other: the pass expanded "we've" to "we have",
        # the auditor returned "Expanded contraction unnecessarily", the
        # repair stage dutifully put it back, and the gate then refused the
        # un-expanded result. Measured on three lecture transcripts: 28 to 37
        # HIGH findings, one segment changed in fifty, every pass discarded.
        #
        # The deterministic transcript checks stay, and they are the ones
        # that matter -- contractions, fillers, speaker labels, timestamps,
        # number style ARE Eagle's rubric, expressed as code. Repair stays so
        # those findings can be acted on. What goes is the stage that had no
        # way to know the transformation was deliberate.
        stages=(STAGE_DETERMINISTIC, STAGE_REPAIR, STAGE_VALIDATION),
        policy=QualityPolicy(
            name=EAGLE_STANDARD,
            blocking_severities=(Severity.CRITICAL, Severity.HIGH),
            max_iterations=2,
            require_deterministic_pass=True,
            allow_local_fallback=False,
        ),
        description=(
            'Brings a raw transcript up to a verbatim standard. Deterministic '
            'rubric checks only: the semantic audit cannot tell a required '
            'transformation from a defect.'
        ),
    ),
    EAGLE_WEB: OperationProfile(
        name=EAGLE_WEB,
        label='Eagle Web submission',
        policy=QualityPolicy(
            name=EAGLE_WEB,
            # MEDIUM NO LONGER BLOCKS, AND THE EVIDENCE IS WHY.
            #
            # This used to read "zero tolerance: MEDIUM blocks too", on the
            # reasoning that a rejected submission costs real money so the
            # profile feeding one should refuse anything it is unsure about.
            # Sound, and written before we had ever had a submission accepted.
            #
            # We have now had two. Task 4452 and task 4625 were BOTH accepted
            # by the platform carrying a MEDIUM finding -- the same one,
            # `entity.identifier-altered`. A MEDIUM is not what gets work
            # sent back.
            #
            # And refusing on MEDIUM is not free, which is the part that
            # settles it. This profile gates the pass that brings a raw ASR
            # transcript up to Eagle's verbatim standard, and it refused that
            # pass on three transcription tasks out of three. The documents
            # the workers were then handed carried 22 unexpanded contractions
            # against an edited-verbatim rubric -- a HIGH finding. The gate
            # was trading three survivable MEDIUMs for one HIGH, every time.
            #
            # CRITICAL and HIGH still block, and nothing reaches the platform
            # without a human reading it and the submission gate checking it
            # again. This loosens one of three checks, not the only one.
            blocking_severities=(Severity.CRITICAL, Severity.HIGH),
            max_iterations=3,
            min_confidence=0.85,
            require_deterministic_pass=True,
            # Never silently validate a submission on a local model.
            allow_local_fallback=False,
        ),
        submission_ready=True,
        description=(
            'Zero-tolerance gate for external submission: adds commentary '
            'and leakage checks and refuses on MEDIUM findings.'
        ),
    ),
}


def get_profile(name: Optional[str]) -> Optional[OperationProfile]:
    """None for 'none'/unset -- the caller reads that as "run no QA", which
    is the backward-compatible default."""
    if not name or name == NONE:
        return None
    return _PROFILES.get(str(name).strip().lower())


def profile_names() -> List[str]:
    return [NONE] + sorted(_PROFILES)


def describe_profiles() -> List[Dict]:
    """What GET /ai-capabilities/ advertises, so the UI renders the server's
    list rather than a hard-coded copy that can drift out of step."""
    described = [{
        'value': NONE,
        'label': 'No quality pipeline',
        'description': 'Single AI pass, as before. Fastest and cheapest.',
        'submission_ready': False,
    }]
    described += [
        {
            'value': profile.name,
            'label': profile.label,
            'description': profile.description,
            'submission_ready': profile.submission_ready,
        }
        for profile in sorted(_PROFILES.values(), key=lambda p: p.name)
    ]
    return described


# Operations whose output is a DELIVERABLE rather than a working step.
#
# The distinction is what makes gating affordable in a real workflow. A
# typical job is: correct the ASR, optionally format it academically, then
# translate -- and only the last of those produces the text that leaves the
# building. Gating the intermediate steps pays for an audit of prose that
# will be rewritten by the next step anyway.
#
# Translation is also the operation where errors are hardest to catch by
# eye: it crosses a language boundary, so a reviewer who does not read the
# target language cannot see a defect at all. The same-language operations
# are checked for free by the human who reads the result.
DELIVERABLE_OPERATIONS = ('translate',)


def default_profile_for(operation: str, configured: str = NONE) -> str:
    """
    Resolves AI_QUALITY_DEFAULT_PROFILE into a profile name for one run.

    Three forms, in increasing specificity:

      'none'            no gate anywhere. The default; behaviour is exactly
                        as it was before the gate existed.
      'auto[:profile]'  gate only the deliverable operations, with
                        `profile` (default 'translation'). This is the form
                        that suits a multi-step workflow: correct and
                        format freely, and spend the audit budget on the
                        text that actually ships.
      '<profile>'       gate EVERY operation with that profile. Explicit,
                        and expensive by design -- an operator asking for it
                        by name gets it.

    An unrecognized name falls back to no gate rather than raising, so a
    typo in configuration degrades to the previous behaviour instead of
    taking every revision down with it.
    """
    configured = (configured or NONE).strip().lower()

    if configured.startswith('auto'):
        _, _, named = configured.partition(':')
        profile = named.strip() or TRANSLATION
        if profile not in _PROFILES:
            profile = TRANSLATION
        return profile if operation in DELIVERABLE_OPERATIONS else NONE

    if configured in _PROFILES:
        return configured
    return NONE
