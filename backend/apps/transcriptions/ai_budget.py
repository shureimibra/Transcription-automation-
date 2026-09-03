"""
Server-side cost control for AI Assist.

This module is the only thing that decides whether an AI request is allowed
to happen. It lives in the app layer rather than in core/services/ai_assist
because it queries models, and services in this codebase stay
database-agnostic (core.services.academic.hashing takes a duck-typed job,
never an ORM import).

Why it's DB-backed and not a Redis counter: there is no Redis in this
deployment. settings.py configures InMemoryChannelLayer and SQLite, so an
in-process dictionary would reset on every restart and wouldn't be shared
between the WSGI worker and the background threads. The AIUsageRecord table
IS the counter.

Why it's global rather than per-user: this app has no authentication (see
AIRevision.owner's comment). A per-user quota needs a user, so the budget is
enforced for the whole server instead. The brief's requirement that "the
server decides, never the frontend" is satisfied either way -- every check
here runs before dispatch, and no client input can raise a limit.

TWO CEILINGS, AND THE SECOND IS THE ONE THAT BINDS. Tokens are metered here
because they are what a request costs; REQUESTS are metered because they are
what the provider's free tier actually rations. A run can be comfortably
inside the token budget and still be refused by Gemini for being the 21st
call of the day against a model whose free allowance is 20 -- which is
exactly what happened in testing.

The defaults below are sized from measured runs (see each constant) rather
than guessed, but the authoritative per-model numbers live in the provider's
own dashboard and change without notice. Everything is settings-overridable
precisely so correcting them stays a config edit.
"""
import logging
import math
from datetime import timedelta
from typing import Any, Dict, List

from django.db.models import Sum
from django.utils import timezone

from .models import AIUsageRecord

try:
    from backend.core.services.ai_assist import (
        AIBudgetExceededError, AIRateLimitedError, int_config, is_configured,
        resolve_model_name,
    )
except ModuleNotFoundError:
    from core.services.ai_assist import (
        AIBudgetExceededError, AIRateLimitedError, int_config, is_configured,
        resolve_model_name,
    )

logger = logging.getLogger("AIPipeline")

# Sized for the real workload this serves: 120 tasks/day at ~20 minutes of
# audio each. Measured on task 4199 (5,151 source chars -> 3,980 tokens for
# translation, 6,313 for analysis+audit) and scaled: ~29k tokens per 20-min
# task for translation plus a report-only gate, ~4M/day with a quarter of
# tasks escalating to repair.
#
# The previous 1,000,000 default would have refused everything after roughly
# 35 tasks -- a budget tuned for occasional use silently becomes an outage
# when the workload becomes routine.
DEFAULT_DAILY_TOKEN_BUDGET = 6_000_000

# THE LIMIT THAT ACTUALLY BINDS. Gemini's free tier meters REQUESTS per day,
# not tokens, and the per-model allowances differ by more than an order of
# magnitude: a live probe of one key found gemini-3.6-flash capped at 20/day
# while three flash-lite models answered freely. Published free-tier figures
# for the lite family are ~1,000-1,500 RPD.
#
# 900 is deliberately below the lower published figure, because the real
# number lives in the project's AI Studio dashboard and can change without
# notice. Refusing a request we could have made costs one delayed task;
# discovering the ceiling by hitting it costs a run mid-flight.
DEFAULT_MAX_REQUESTS_PER_DAY = 900
DEFAULT_MAX_TOKENS_PER_REQUEST = 200_000
DEFAULT_MAX_REQUESTS_PER_MINUTE = 10

# Characters per token. 4 is the usual English rule of thumb; transcripts of
# other languages tokenize less efficiently, so this under-estimates for
# them. Only ever used for the pre-flight estimate -- actual spend is read
# back from the provider's usageMetadata, never from this.
CHARS_PER_TOKEN = 4

# Per-batch JSON envelope, ids, and the fixed prompt preamble.
PROMPT_OVERHEAD_TOKENS_PER_BATCH = 400


def daily_token_budget() -> int:
    return int_config('AI_DAILY_TOKEN_BUDGET', DEFAULT_DAILY_TOKEN_BUDGET)


def max_requests_per_day() -> int:
    return int_config('AI_MAX_REQUESTS_PER_DAY', DEFAULT_MAX_REQUESTS_PER_DAY)


def requests_today() -> int:
    """
    Provider calls made since local midnight.

    Sums `request_count` rather than counting rows: the quality gate writes
    one row for a run that may have made five calls, so a row count would
    under-report exactly the traffic most at risk of exhausting the quota.
    """
    start_of_day = timezone.localtime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    total = AIUsageRecord.objects.filter(created_at__gte=start_of_day).aggregate(
        total=Sum('request_count')
    )['total']
    return int(total or 0)


def remaining_daily_requests() -> int:
    return max(0, max_requests_per_day() - requests_today())


def check_daily_requests(estimated_requests: int = 1) -> None:
    """
    Raises AIRateLimitedError when this dispatch would cross the daily
    request ceiling.

    AIRateLimitedError rather than AIBudgetExceededError, because that is
    what it is: the provider will start refusing, and the honest advice is
    to wait for the reset (midnight Pacific for Gemini) rather than to
    process a smaller range.
    """
    remaining = remaining_daily_requests()
    if estimated_requests > remaining:
        raise AIRateLimitedError(
            f'This run needs about {estimated_requests} provider request(s) '
            f'but only {remaining} remain of the daily cap '
            f'({max_requests_per_day()}).',
            safe_message=(
                "The server's daily AI request allowance is nearly used up. "
                'Please try again after it resets.'
            ),
        )


def max_tokens_per_request() -> int:
    return int_config('AI_MAX_TOKENS_PER_REQUEST', DEFAULT_MAX_TOKENS_PER_REQUEST)


def max_requests_per_minute() -> int:
    return int_config('AI_MAX_REQUESTS_PER_MINUTE', DEFAULT_MAX_REQUESTS_PER_MINUTE)


def tokens_used_today() -> int:
    """
    Sum of recorded spend since local midnight.

    Local midnight, not a rolling 24h window: a daily cap the operator can
    reason about ("it resets overnight") is worth more here than one that
    matches the provider's own reset, which we can't observe anyway.
    """
    start_of_day = timezone.localtime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    total = AIUsageRecord.objects.filter(created_at__gte=start_of_day).aggregate(
        total=Sum('total_tokens')
    )['total']
    return int(total or 0)


def remaining_daily_tokens() -> int:
    return max(0, daily_token_budget() - tokens_used_today())


def estimate_tokens(segments: List[Any], batch_count: int = 1) -> int:
    """
    Pre-flight estimate for one revision.

    Doubled because this path pays for output as well as input: the response
    repeats every segment's text inside a JSON envelope, so a request's true
    cost is roughly twice its transcript size. Deliberately pessimistic --
    an over-estimate refuses a borderline request, which is the safe
    direction for a budget check.
    """
    total_chars = sum(len(segment.text or '') for segment in segments)
    text_tokens = math.ceil(total_chars / CHARS_PER_TOKEN)
    return text_tokens * 2 + PROMPT_OVERHEAD_TOKENS_PER_BATCH * max(1, batch_count)


def check_rate_limit() -> None:
    """
    Trailing-60-second request count. Raises AIRateLimitedError.

    Counts ledger rows, each of which is one batch sent to the provider, so
    this reflects real request volume rather than button presses. Checked at
    dispatch: it stops a user hammering the AI Assist button from queueing
    twenty overlapping revisions.

    IT CANNOT SEE REFUSED REQUESTS. A ledger row is written only after a
    response comes back, so anything the provider rejects leaves no trace
    here -- meaning this counter goes QUIET exactly when the provider starts
    refusing, and reports headroom that does not exist. It showed 18
    requests in two hours while Google was refusing us continuously.

    That is why it is not the only guard: providers._RequestPacer counts
    ATTEMPTS at the outbound call itself and blocks. This one remains useful
    for what it was written for -- bounding how many RUNS start -- and must
    not be mistaken for protection against bursting.

    Pacing *within* a run is AIPostProcessor's job, not this function's: it
    spaces batches by AI_ASSIST_INTER_BATCH_DELAY_SECONDS (defaulted to match
    DEFAULT_MAX_REQUESTS_PER_MINUTE below) and waits out a 429 with its own
    exponential backoff. Both layers are needed -- this one bounds how many
    runs start, that one bounds how fast a single run spends its allowance.
    """
    window_start = timezone.now() - timedelta(seconds=60)
    recent = AIUsageRecord.objects.filter(created_at__gte=window_start).count()
    limit = max_requests_per_minute()
    if recent >= limit:
        raise AIRateLimitedError(
            f'{recent} AI requests in the last 60s exceeds the limit of {limit}.'
        )


def check_budget(segments: List[Any], batch_count: int = 1, multiplier: int = 1) -> int:
    """
    Raises AIBudgetExceededError if this request shouldn't be dispatched.
    Returns the estimate on success.

    Both checks happen before any provider call, so a refusal costs nothing.

    `multiplier` scales the estimate for runs that make more than one pass
    over the text -- see QUALITY_PROFILE_COST_MULTIPLIER. Defaults to 1 so
    every existing caller prices a request exactly as it did before.
    """
    estimate = estimate_tokens(segments, batch_count) * max(1, int(multiplier))

    per_request = max_tokens_per_request()
    if estimate > per_request:
        raise AIBudgetExceededError(
            f'Estimated {estimate} tokens exceeds the per-request cap of {per_request}.',
            safe_message=(
                'This selection is too large for one AI request. '
                'Process a smaller range of the transcript.'
            ),
        )

    remaining = remaining_daily_tokens()
    if estimate > remaining:
        raise AIBudgetExceededError(
            f'Estimated {estimate} tokens exceeds the {remaining} remaining today '
            f'(daily budget {daily_token_budget()}).',
            safe_message=(
                "The server's daily AI usage budget is exhausted. "
                'It resets at midnight.'
            ),
        )
    return estimate


# Multiplier applied to the estimate when a quality profile is requested.
#
# A gated run pays for: one analysis call over a sample, one audit call per
# chunk carrying BOTH source and candidate (so ~2x the text of the primary
# pass), and then repair + re-audit only over the chunks that actually had
# findings. 3x is deliberately pessimistic for the same reason
# estimate_tokens doubles: an over-estimate refuses a borderline request,
# which is the safe direction for a budget check.
#
# Without this the pre-flight would price a gated run as though it were an
# ungated one, and the daily budget would be silently overspent by the
# difference -- the check would pass, the run would start, and the ledger
# would only find out afterwards.
QUALITY_PROFILE_COST_MULTIPLIER = 3

# Provider calls a gated run adds on top of its translation batches:
# analysis (1) + audit (1) + a repair/re-audit pair if the audit blocks (2).
# The escalating figure is used for pre-flight so a budget refusal reflects
# the worst case rather than the hoped-for one.
QUALITY_REQUESTS_PER_RUN = 4


def preflight(segments: List[Any], quality_profile: str = '') -> Dict:
    """
    The single call a dispatch endpoint makes before starting a revision.

    Raises AIRateLimitedError or AIBudgetExceededError; both carry a
    `.safe_message` the view can hand to the client verbatim. Returns the
    estimate and batch count, which are worth logging.

    Batching is derived here rather than passed in so the view doesn't have to
    know how the processor chunks work -- and because the estimate's per-batch
    prompt overhead is only meaningful if the count matches what the run will
    actually send.

    `quality_profile`, when set, scales the estimate by
    QUALITY_PROFILE_COST_MULTIPLIER. A gated run genuinely costs several
    times an ungated one, and pricing it as though it did not would let the
    daily budget be overspent by exactly that difference.
    """
    try:
        from backend.core.services.ai_assist import build_batches
    except ModuleNotFoundError:
        from core.services.ai_assist import build_batches

    check_rate_limit()
    batch_count = len(build_batches(segments))
    multiplier = QUALITY_PROFILE_COST_MULTIPLIER if quality_profile else 1

    # One request per translation batch, plus the gate's own calls when a
    # profile is set: analysis (1) + audit (1 per chunk, ~1 for a normal
    # job), and a repair + re-audit pair only if the audit finds something
    # blocking. Estimated at the escalating case so the check is honest
    # about the worst outcome rather than the median one.
    estimated_requests = batch_count
    if quality_profile:
        estimated_requests += QUALITY_REQUESTS_PER_RUN

    check_daily_requests(estimated_requests)
    estimate = check_budget(segments, batch_count, multiplier=multiplier)
    return {
        'estimated_tokens': estimate,
        'estimated_requests': estimated_requests,
        'batch_count': batch_count,
        'quality_profile': quality_profile or '',
    }


def record_usage(revision, usage, job=None, request_count: int = 1) -> AIUsageRecord:
    """
    Appends one ledger row. Called once per batch, so a row is one logical
    provider request -- which is what makes the trailing-window rate limiter
    meaningful. Provider-internal retries (a 429 re-attempt, a split batch)
    are not counted separately.

    Never raises: metering must not be able to fail a revision whose text
    already came back successfully. A missing row under-counts the budget,
    which is recoverable; a lost transcript is not.
    """
    try:
        return AIUsageRecord.objects.create(
            revision=revision,
            job=job or getattr(revision, 'job', None),
            owner=getattr(revision, 'owner', None),
            operation=getattr(revision, 'operation', '') or '',
            model_name=getattr(revision, 'model_name', '') or '',
            request_count=max(1, int(request_count or 1)),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )
    except Exception as exc:
        logger.error('[AI ASSIST] Failed to record usage: %s', exc, exc_info=True)
        return None


def capabilities() -> Dict:
    """
    What GET /ai-capabilities/ returns: the server telling the frontend what
    is permitted, so the client never decides entitlement itself.

    Notably absent: the API key, the model's prompts, and any provider
    configuration beyond the model's public name.
    """
    try:
        from backend.core.services.ai_assist import (
            OPERATIONS, OPERATION_LABELS, config, default_profile_for,
            describe_profiles,
        )
        from backend.core.services.ai_assist.quality import describe_standards
    except ModuleNotFoundError:
        from core.services.ai_assist import (
            OPERATIONS, OPERATION_LABELS, config, default_profile_for,
            describe_profiles,
        )
        from core.services.ai_assist.quality import describe_standards

    configured = is_configured()
    return {
        'ai_enabled': configured,
        'model': resolve_model_name() if configured else None,
        'operations': [
            {'value': operation, 'label': OPERATION_LABELS[operation]}
            for operation in OPERATIONS
        ],
        'daily_token_budget': daily_token_budget(),
        'tokens_used_today': tokens_used_today(),
        'tokens_remaining_today': remaining_daily_tokens(),
        'max_tokens_per_request': max_tokens_per_request(),
        'max_requests_per_minute': max_requests_per_minute(),
        'max_requests_per_day': max_requests_per_day(),
        'requests_used_today': requests_today(),
        'requests_remaining_today': remaining_daily_requests(),
        # The quality gate, advertised the same way operations are: the
        # server states what exists and what it will do by default, and the
        # UI renders that answer rather than keeping its own copy that can
        # drift out of step with the registry.
        'quality_profiles': describe_profiles(),
        # Eagle Web's two transcription rubrics. Advertised so the UI offers
        # the choice explicitly: they contradict each other, and picking the
        # wrong one fails the whole document regardless of how good the
        # words are, so it must never be an implicit default.
        'transcription_standards': describe_standards(),
        'default_quality_profile': {
            operation: default_profile_for(
                operation, config('AI_QUALITY_DEFAULT_PROFILE', 'none'),
            )
            for operation in OPERATIONS
        },
    }
