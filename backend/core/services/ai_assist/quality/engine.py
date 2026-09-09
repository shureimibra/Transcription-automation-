"""
The orchestrator: runs the stages, iterates, and decides.

    analysis -> [ audit + deterministic -> gate -> repair -> re-audit ]* -> verdict

THE GATE IS HERE AND NOWHERE ELSE. Every other module in this package
reports; this one is the only thing that returns a QualityStatus. That
concentration is deliberate -- RULE 12 is a single-place invariant, and it
is far easier to prove "nothing outside engine.py constructs a VALIDATED
status" than to audit a decision spread across six modules.

Three invariants this file exists to hold:

  1. TERMINATION. `policy.max_iterations` bounds the loop absolutely, and a
     `_ProgressGuard` stops it early when an iteration changes nothing --
     because a loop that is not converging will not converge, and each turn
     of it costs real money (RULE 13).
  2. NO SILENT PASS. Every path that fails to establish quality returns
     QUALITY_REVIEW_REQUIRED or TECHNICAL_FAILURE. There is no branch where
     an unusable audit response, an exhausted iteration budget, or a
     cancelled run yields VALIDATED (RULE 11, spec 35).
  3. THE CANDIDATE IS ALWAYS RETURNED. Even a refused result hands back its
     best text and its unresolved findings, so a human reviewer starts from
     the work rather than from nothing.
"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from ..errors import AIAssistError
from ..providers import TokenUsage, build_provider, int_config
from . import deterministic, prompts, stages, terminology
from .issues import (
    IssueCategory, QualityIssue, QualityStatus, Severity, sort_issues, summarize,
)
from .typography import normalize_typography
from .profiles import (
    STAGE_ANALYSIS, STAGE_AUDIT, STAGE_DETERMINISTIC, STAGE_REAUDIT,
    STAGE_REPAIR, DomainProfile, OperationProfile, get_profile,
)

logger = logging.getLogger('AIPipeline')

# Hard ceiling on QA iterations, independent of any profile or setting.
# A profile is data and a setting is operator input; neither should be able
# to authorize an unbounded spend loop (RULE 13).
ABSOLUTE_MAX_ITERATIONS = 5


class QualityResult:
    """
    Everything one quality run concluded -- the deliverable, the verdict,
    the evidence, and the telemetry (spec 41).

    `texts` is always the same length as the input pairs, whatever happened,
    so a caller can persist results without branching on status.
    """

    __slots__ = (
        'status', 'texts', 'issues', 'domain_profile', 'iterations',
        'confidence', 'usage', 'reason', 'stage_latencies', 'stage_usage',
        'model_name', 'prompt_version', 'profile_name', 'degraded_stages',
        'model_status', 'request_count',
    )

    def __init__(
        self,
        status: QualityStatus,
        texts: List[str],
        issues: Optional[List[QualityIssue]] = None,
        domain_profile: Optional[DomainProfile] = None,
        iterations: int = 0,
        confidence: float = 0.0,
        usage: Optional[TokenUsage] = None,
        reason: str = '',
        stage_latencies: Optional[Dict[str, float]] = None,
        stage_usage: Optional[Dict[str, int]] = None,
        model_name: str = '',
        profile_name: str = '',
        degraded_stages: Optional[List[str]] = None,
        model_status: str = '',
        request_count: int = 0,
    ):
        self.status = status
        self.texts = texts
        self.issues = sort_issues(issues or [])
        self.domain_profile = domain_profile
        self.iterations = iterations
        self.confidence = confidence
        self.usage = usage or TokenUsage()
        self.reason = reason
        self.stage_latencies = stage_latencies or {}
        self.stage_usage = stage_usage or {}
        self.model_name = model_name
        self.prompt_version = prompts.PROMPT_VERSION
        self.profile_name = profile_name
        # Stages that ran but could not be trusted. Recorded rather than
        # inferred, because "the audit produced no findings" and "the audit
        # failed" look identical in an issue list and mean opposite things.
        self.degraded_stages = degraded_stages or []
        self.model_status = model_status
        # Actual provider calls. Counted rather than inferred: the gate makes
        # a variable number depending on chunking and escalation, and a
        # requests-per-day quota cannot be enforced against a guess.
        self.request_count = request_count

    @property
    def tier_reached(self) -> int:
        """
        How far up the cost tiers this run climbed (see profiles.py).

        0 -- deterministic only, no provider calls.
        1 -- analysis and audit ran; nothing was repaired.
        2 -- at least one repair/re-audit round was needed.

        Derived rather than stored, so it cannot disagree with what actually
        happened: iterations is incremented only by the repair path, and
        request_count is zero only when no stage called the provider.
        """
        if self.iterations > 0:
            return 2
        return 1 if self.request_count else 0

    @property
    def validated(self) -> bool:
        return self.status == QualityStatus.VALIDATED

    @property
    def blocking_issues(self) -> List[QualityIssue]:
        return [issue for issue in self.issues if issue.is_blocking]

    def counts(self) -> Dict[str, int]:
        return summarize(self.issues)

    def to_report(self) -> Dict:
        """The serializable audit record. Contains no prompts, no provider
        payloads and no credentials -- it is stored in the database and
        surfaced to the browser (spec 27, spec 36)."""
        return {
            'status': self.status.value,
            'profile': self.profile_name,
            'reason': self.reason,
            'iterations': self.iterations,
            'confidence': round(self.confidence, 4),
            'model_name': self.model_name,
            'prompt_version': self.prompt_version,
            'model_self_reported_status': self.model_status,
            'degraded_stages': list(self.degraded_stages),
            'counts': self.counts(),
            'issues': [issue.to_dict() for issue in self.issues],
            'domain_profile': (
                self.domain_profile.to_dict() if self.domain_profile else None
            ),
            'stage_latencies': {
                key: round(value, 3) for key, value in self.stage_latencies.items()
            },
            'stage_tokens': dict(self.stage_usage),
            'total_tokens': self.usage.total_tokens,
            'request_count': self.request_count,
            'tier_reached': self.tier_reached,
        }


class _ProgressGuard:
    """
    Stops a loop that is spending money without changing anything.

    Compares the candidate text between iterations. Identical text means the
    repair stage declined every finding or failed silently, and re-auditing
    byte-identical input can only produce the same verdict -- so the loop is
    not converging, it is stuck, and the honest move is to stop and say so.
    """

    def __init__(self, initial: Optional[List[str]] = None):
        # PRIMED WITH THE CANDIDATE THE LOOP STARTS FROM, so a repair pass
        # that returns its input unchanged is caught on the FIRST iteration
        # rather than the second. Left unprimed, the guard needs two repair
        # calls to notice a repair stage that is declining every finding --
        # one wasted repair and one wasted audit, on every such run.
        self._previous: Optional[List[str]] = list(initial) if initial is not None else None

    def stalled(self, texts: List[str]) -> bool:
        stalled = self._previous is not None and self._previous == texts
        self._previous = list(texts)
        return stalled


class _CountingProvider:
    """
    Transparent proxy that tallies provider calls.

    A proxy rather than a counter threaded through every stage signature:
    stages.py deliberately knows nothing about budgets, and adding a count
    parameter to three functions to serve one caller would leak that concern
    downward. This keeps the accounting where the accounting is used.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def generate_json(self, *args, **kwargs):
        self.calls += 1
        return self._inner.generate_json(*args, **kwargs)


class AIQualityEngine:
    """
    Runs a quality profile over aligned (source, target) pairs.

    The provider is injected exactly as AIPostProcessor's is, so the whole
    engine is exercisable with the FakeStructuredProvider the existing suite
    already uses, and so nothing here needs an API key to be tested.
    """

    def __init__(
        self,
        profile: OperationProfile,
        provider: Optional[Any] = None,
        max_iterations: Optional[int] = None,
    ):
        self.profile = profile
        self.provider = provider if provider is not None else build_provider()
        configured = (
            max_iterations
            if max_iterations is not None
            else int_config('AI_QUALITY_MAX_ITERATIONS', profile.policy.max_iterations)
        )
        self.max_iterations = max(0, min(int(configured), ABSOLUTE_MAX_ITERATIONS))

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run(
        self,
        pairs: List[Dict],
        target_language: Optional[str] = None,
        target_language_code: Optional[str] = None,
        source_language: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_stage: Optional[Callable[[str, Dict], None]] = None,
        domain_profile: Optional[DomainProfile] = None,
    ) -> QualityResult:
        """
        `pairs` is [{'source': str, 'target': str}, ...] in transcript order.

        `target_language` is the human-readable name for prompts ("French");
        `target_language_code` is the ISO code for the deterministic
        typography rules ("fr"). Two parameters rather than one because
        models translate more reliably into a named language while
        TypographyRegistry keys on the code -- the same split
        _ai_language_name already makes in tasks.py.

        `on_stage(stage, payload)` is the progress hook. Broadcast-shaped
        rather than returning a generator so the caller can forward it to
        the existing WebSocket bus without restructuring its loop.

        `domain_profile`, when given, is used AS-IS instead of running
        STAGE_ANALYSIS again. The caller that already spent a request
        analysing this document -- to feed the initial draft's own prompt,
        see run_ai_revision_pipeline -- passes its result back in here so
        the gate doesn't pay for the same answer twice.
        """
        texts = [pair.get('target') or '' for pair in pairs]

        # Mechanical typography first, once, before anything looks at the
        # text. `normalize_typography` only applies substitutions that cannot
        # change meaning (see its docstring -- guillemets and decimal
        # separators are deliberately excluded as judgement calls).
        #
        # Doing this here rather than at the end is what makes it free: a
        # first real run produced 13 LOW findings that were all "you used a
        # plain space where French wants a non-breaking one", every one of
        # them fixable by a regex. Reporting those to a human, or spending a
        # repair call on them, is waste -- and worse, it buries the findings
        # that actually need judgement under a pile of ones that do not.
        if target_language_code:
            texts = [normalize_typography(text, target_language_code) for text in texts]

        if not pairs:
            return QualityResult(
                QualityStatus.NOT_RUN, texts,
                reason='Nothing to review.', profile_name=self.profile.name,
            )

        provider = _CountingProvider(self.provider)
        model_name = getattr(self.provider, 'model', '') or ''
        latencies: Dict[str, float] = {}
        stage_tokens: Dict[str, int] = {}
        usage = TokenUsage()
        degraded: List[str] = []
        notify = on_stage or (lambda stage, payload: None)

        def record(stage: str, started: float, stage_usage: TokenUsage):
            latencies[stage] = latencies.get(stage, 0.0) + (time.monotonic() - started)
            stage_tokens[stage] = stage_tokens.get(stage, 0) + stage_usage.total_tokens

        try:
            # -------------------------------------------------- analysis
            #
            # `domain_profile` is a parameter now, not always a local: a
            # caller that already analysed this document (to feed the
            # initial draft's own prompt) passed its result in, and that
            # value must survive to here untouched -- this used to
            # unconditionally reset it to None before the check below,
            # which would have thrown away a passed-in profile and paid for
            # analyze_source a second time regardless.
            if domain_profile is None and self.profile.runs(STAGE_ANALYSIS):
                notify(STAGE_ANALYSIS, {'message': 'Analysing source material...'})
                started = time.monotonic()
                domain_profile, stage_usage = stages.analyze_source(
                    provider, pairs,
                    target_language=target_language,
                    source_language=source_language,
                )
                usage = usage + stage_usage
                record(STAGE_ANALYSIS, started, stage_usage)
            if domain_profile is not None:
                if domain_profile.domain == 'unknown':
                    degraded.append(STAGE_ANALYSIS)
                notify(STAGE_ANALYSIS, {
                    'message': f'Detected domain: {domain_profile.domain}',
                    'domain': domain_profile.domain,
                    'subdomain': domain_profile.subdomain,
                    'register': domain_profile.register,
                    'risk_level': domain_profile.risk_level,
                })

            # A detected high-risk domain can tighten the policy but never
            # loosen it -- see QualityPolicy.for_domain.
            policy = self.profile.policy
            if domain_profile is not None:
                policy = policy.for_domain(domain_profile)

            guard = _ProgressGuard(texts)
            issues: List[QualityIssue] = []
            confidence = 0.0
            model_status = ''
            iterations = 0

            while True:
                if should_cancel is not None and should_cancel():
                    return self._result(
                        QualityStatus.QUALITY_REVIEW_REQUIRED, texts, issues,
                        domain_profile, iterations, confidence, usage,
                        'Cancelled before the quality gate completed.',
                        latencies, stage_tokens, model_name, degraded, model_status,
                        provider.calls,
                    )

                stage = STAGE_REAUDIT if iterations else STAGE_AUDIT

                # ------------------------------------- deterministic pass
                det_issues: List[QualityIssue] = []
                if self.profile.runs(STAGE_DETERMINISTIC):
                    started = time.monotonic()
                    det_issues = self._deterministic_pass(
                        pairs, texts, target_language_code, domain_profile,
                    )
                    record(STAGE_DETERMINISTIC, started, TokenUsage())
                    notify(STAGE_DETERMINISTIC, {
                        'message': f'{len(det_issues)} automated check finding(s).',
                        'issue_count': len(det_issues),
                    })

                # ------------------------------------------- AI audit pass
                ai_issues: List[QualityIssue] = []
                if self.profile.runs(STAGE_AUDIT):
                    notify(stage, {
                        'message': 'Auditing against the source...',
                        'iteration': iterations,
                    })
                    started = time.monotonic()
                    audit = stages.audit_pairs(
                        provider,
                        _pairs_with(pairs, texts),
                        target_language=target_language,
                        domain_profile=domain_profile,
                        deterministic_issues=det_issues,
                        previous_issues=issues if iterations else None,
                        should_cancel=should_cancel,
                    )
                    usage = usage + audit.usage
                    record(stage, started, audit.usage)
                    ai_issues = audit.issues
                    confidence = audit.confidence
                    model_status = audit.model_status
                    if audit.chunks_failed:
                        # Part of the range was never reviewed. This must
                        # block: an unaudited chunk is not a clean one, and
                        # treating silence as approval is the exact failure
                        # this package exists to prevent.
                        if stage not in degraded:
                            degraded.append(stage)
                        ai_issues = ai_issues + [QualityIssue(
                            category='semantic', severity=Severity.HIGH,
                            problem=(
                                f'{audit.chunks_failed} section(s) could not be '
                                f'audited: the reviewer returned an unusable '
                                f'response. That text is unverified.'
                            ),
                            correction='Re-run the quality pass, or review manually.',
                            rule='audit.chunk-unavailable', detector='deterministic',
                        )]

                # REPLACED, not accumulated. The verdict is always about the
                # CURRENT text, so each pass re-derives its findings from
                # scratch: an issue the repair actually fixed must stop
                # blocking, and a chunk that failed to audit last time but
                # audited cleanly this time is genuinely verified now.
                #
                # The inverse is what makes this safe rather than lax -- a
                # defect that survived the repair is re-reported by this
                # pass, so nothing can be forgotten into acceptance. What
                # gates is always "was the final text fully audited and
                # found clean", never "did some earlier pass approve it".
                ai_issues = _drop_unverifiable(ai_issues, texts)
                issues = _dedupe(det_issues + ai_issues)
                accepted, reason = policy.evaluate(issues, confidence)

                notify(stage, {
                    'message': (
                        'No blocking issues.' if accepted
                        else f'{len(policy.blocking_issues(issues))} blocking issue(s).'
                    ),
                    'iteration': iterations,
                    'issue_count': len(issues),
                    'counts': summarize(issues),
                    'accepted': accepted,
                })

                if accepted:
                    return self._result(
                        QualityStatus.VALIDATED, texts, issues, domain_profile,
                        iterations, confidence, usage, '', latencies,
                        stage_tokens, model_name, degraded, model_status,
                        provider.calls,
                    )

                # ------------------------------------------------- repair
                if iterations >= self.max_iterations or not self.profile.runs(STAGE_REPAIR):
                    return self._result(
                        QualityStatus.QUALITY_REVIEW_REQUIRED, texts, issues,
                        domain_profile, iterations, confidence, usage,
                        reason or 'Unresolved quality issues remain.',
                        latencies, stage_tokens, model_name, degraded, model_status,
                        provider.calls,
                    )

                repairable = policy.blocking_issues(issues)
                if not repairable:
                    # Nothing blocking is repairable, yet the gate refused --
                    # only reachable via min_confidence. Repairing would be
                    # spending on a problem no edit can fix.
                    return self._result(
                        QualityStatus.QUALITY_REVIEW_REQUIRED, texts, issues,
                        domain_profile, iterations, confidence, usage, reason,
                        latencies, stage_tokens, model_name, degraded, model_status,
                        provider.calls,
                    )

                notify(STAGE_REPAIR, {
                    'message': f'Repairing {len(repairable)} issue(s)...',
                    'iteration': iterations,
                    'issue_count': len(repairable),
                })
                started = time.monotonic()
                texts, repair_usage, repair_failed = stages.repair_pairs(
                    provider,
                    _pairs_with(pairs, texts),
                    repairable,
                    target_language=target_language,
                    domain_profile=domain_profile,
                    should_cancel=should_cancel,
                )
                usage = usage + repair_usage
                record(STAGE_REPAIR, started, repair_usage)
                if repair_failed and STAGE_REPAIR not in degraded:
                    degraded.append(STAGE_REPAIR)

                iterations += 1

                if guard.stalled(texts):
                    return self._result(
                        QualityStatus.QUALITY_REVIEW_REQUIRED, texts, issues,
                        domain_profile, iterations, confidence, usage,
                        'The repair pass produced no change; the remaining '
                        'issues need a human decision.',
                        latencies, stage_tokens, model_name, degraded, model_status,
                        provider.calls,
                    )

        except AIAssistError as exc:
            # Transport, budget, rate-limit and configuration failures.
            # TECHNICAL_FAILURE, distinct from a quality refusal (spec 35):
            # one means "try again", the other means "a person must look".
            logger.error('[AI QUALITY] Technical failure: %s', exc.detail, exc_info=True)
            return self._result(
                QualityStatus.TECHNICAL_FAILURE, texts, [], None, 0, 0.0,
                usage, exc.safe_message, latencies, stage_tokens,
                model_name, degraded, '', provider.calls,
            )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _deterministic_pass(
        self,
        pairs: List[Dict],
        texts: List[str],
        target_language_code: Optional[str],
        domain_profile: Optional[DomainProfile],
    ) -> List[QualityIssue]:
        """
        Per-segment checks plus whole-document anchor checks.

        The split matters. Numbers and typography are properties of one
        segment and are checked there so the finding carries a position a
        repair can act on. Terminology consistency and entity preservation
        are properties of the WHOLE range -- a rendering is only inconsistent
        relative to the rest of the document -- so they run once over the
        joined text with segment_index=None.
        """
        is_translation = bool(target_language_code or self.profile.name == 'translation')
        issues: List[QualityIssue] = []

        for index, pair in enumerate(pairs):
            issues += deterministic.run_deterministic_checks(
                pair.get('source') or '',
                texts[index] if index < len(texts) else '',
                target_language=target_language_code,
                segment_index=index,
                is_translation=is_translation,
                submission_ready=self.profile.submission_ready,
            )

        if domain_profile is not None:
            issues += terminology.run_anchor_checks(
                '\n'.join(pair.get('source') or '' for pair in pairs),
                '\n'.join(texts),
                domain_profile,
                segment_index=None,
                # Aligned pairs, not just the joined text: whether a term
                # DRIFTS is only visible per occurrence, and drift is the
                # part of terminology control that can be measured rather
                # than opined about.
                pairs=[
                    {'source': pair.get('source') or '',
                     'target': texts[index] if index < len(texts) else ''}
                    for index, pair in enumerate(pairs)
                ],
            )
        return issues

    def _result(
        self, status, texts, issues, domain_profile, iterations, confidence,
        usage, reason, latencies, stage_tokens, model_name, degraded, model_status,
        request_count=0,
    ) -> QualityResult:
        result = QualityResult(
            status=status, texts=texts, issues=issues,
            domain_profile=domain_profile, iterations=iterations,
            confidence=confidence, usage=usage, reason=reason,
            stage_latencies=latencies, stage_usage=stage_tokens,
            model_name=model_name, profile_name=self.profile.name,
            degraded_stages=degraded, model_status=model_status,
            request_count=request_count,
        )
        logger.info(
            '[AI QUALITY] profile=%s status=%s iterations=%d issues=%s '
            'confidence=%.2f tokens=%d requests=%d degraded=%s',
            self.profile.name, status.value, iterations, result.counts(),
            confidence, usage.total_tokens, request_count, degraded or '-',
        )
        return result


def _pairs_with(pairs: List[Dict], texts: List[str]) -> List[Dict]:
    """Re-pairs the original sources with the current candidate texts.

    The SOURCE never changes across iterations -- it is the authority
    (spec 6) -- so each pass rebuilds pairs from the untouched source and
    the latest candidate, rather than mutating pairs in place where a stray
    write could corrupt the thing everything is measured against."""
    return [
        {'source': pair.get('source') or '', 'target': texts[index] if index < len(texts) else ''}
        for index, pair in enumerate(pairs)
    ]


def _drop_unverifiable(
    issues: List[QualityIssue], texts: List[str]
) -> List[QualityIssue]:
    """
    Demotes a blocking finding whose own quoted `target_span` does not
    appear anywhere in the candidate.

    THE FIRST REAL RUN NEEDED THIS. On a live law lecture the auditor
    reported, at HIGH, that the English word "organized" had been left
    untranslated. It had not: the word occurs in the SOURCE, and appears
    nowhere in the French. The deterministic leakage checker -- which
    compares actual strings -- correctly found nothing.

    The cost of believing it was severe. The finding blocked, so a repair
    pass ran; the repair could not fix a defect that was not there; the next
    audit reported it again. Three iterations, 383 seconds and roughly
    108,000 tokens later the run was refused, on the strength of a word that
    was never in the document.

    So: if an auditor quotes target text, that quote has to exist. A finding
    that fails this check is not necessarily wrong -- the model may have
    paraphrased the span rather than copying it -- which is why it is
    DEMOTED to LOW and kept in the report for a human, rather than deleted.
    Deleting it would hide a real defect behind a sloppy quotation; blocking
    on it lets a hallucinated quotation stop a submission.

    Deterministic findings are never touched: they were measured, and their
    spans are formatted summaries rather than verbatim quotes.
    """
    if not texts:
        return issues

    haystack = '\n'.join(texts).casefold()
    verified: List[QualityIssue] = []
    for issue in issues:
        span = (issue.target_span or '').strip()
        if (
            issue.detector != 'deterministic'
            and issue.is_blocking
            and len(span) >= MIN_VERIFIABLE_SPAN_CHARS
            and span.casefold() not in haystack
        ):
            logger.warning(
                '[AI QUALITY] Demoting unverifiable %s finding: quoted target '
                'text %r does not appear in the candidate.',
                issue.severity.value, span[:60],
            )
            issue.severity = Severity.LOW
            issue.problem = (
                'UNVERIFIED (the quoted text does not appear in the output): '
                + issue.problem
            )
            issue.rule = issue.rule or 'audit.unverifiable-span'
        verified.append(issue)
    return verified


# Below this length a quote is too short to check honestly -- a two-letter
# span appears in almost any text by chance, which would let a hallucinated
# finding pass verification rather than being caught by it.
MIN_VERIFIABLE_SPAN_CHARS = 4


def _dedupe(issues: List[QualityIssue]) -> List[QualityIssue]:
    """
    Collapses issues that name the same defect twice.

    The audit prompt already tells the model not to repeat a deterministic
    finding, but a prompt is a request and this is the enforcement. Keyed on
    (category, segment, rule, problem-prefix); where a deterministic and an
    AI issue collide, the DETERMINISTIC one wins, because it was measured
    rather than judged.
    """
    chosen: Dict[tuple, QualityIssue] = {}
    for issue in issues:
        # Document-level findings ignore segment_index in the key.
        #
        # A deterministic terminology check reports once for the whole range
        # (segment_index=None) while an AI auditor reports the same thing
        # against whichever segment it noticed it in -- so keying on position
        # let one defect through twice, at two severities. Terms are the
        # identity for those, so source_span joins the key.
        document_level = issue.category in (
            IssueCategory.TERMINOLOGY, IssueCategory.ENTITY,
        )
        key = (
            issue.category,
            None if document_level else issue.segment_index,
            issue.rule or issue.problem[:60].lower(),
            issue.source_span.casefold() if document_level else '',
        )
        existing = chosen.get(key)
        if existing is None:
            chosen[key] = issue
            continue
        if existing.detector != 'deterministic' and issue.detector == 'deterministic':
            chosen[key] = issue
        elif issue.severity.rank < existing.severity.rank:
            # Same defect reported at two severities: keep the worse one.
            # Fail closed, exactly as Severity.parse does.
            chosen[key] = issue
    return list(chosen.values())


def build_engine(
    profile_name: str, provider: Optional[Any] = None
) -> Optional[AIQualityEngine]:
    """Returns None for an unknown or disabled profile, which every caller
    reads as "run no quality pass" -- so a typo in configuration degrades to
    the previous behaviour rather than crashing a revision."""
    profile = get_profile(profile_name)
    if profile is None:
        return None
    return AIQualityEngine(profile, provider=provider)
