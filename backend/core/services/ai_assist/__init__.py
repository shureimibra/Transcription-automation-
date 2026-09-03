"""
AI Assist: server-side Gemini post-processing for finished transcripts.

The public surface deliberately stays small -- views and tasks should reach
for these names and nothing deeper, so the provider wire format stays an
implementation detail of providers.py.

    from core.services.ai_assist import AIPostProcessor, OPERATIONS

Layering, outermost to innermost:

    tasks.run_ai_revision_pipeline   persistence, progress, cancellation
      -> AIPostProcessor             batching, validation, fallback policy
        -> prompts / sanitize        untrusted-data handling (RULE 5)
        -> GeminiStructuredProvider  HTTP, forced JSON, token accounting
                                     (the only holder of the API key)

Optionally, a quality profile runs AFTER the pass above:

    tasks.run_ai_revision_pipeline
      -> AIQualityEngine             domain analysis, adversarial audit,
                                     deterministic checks, bounded repair,
                                     and the acceptance gate
                                     (core/services/ai_assist/quality/)

That layer is opt-in per request and defaults to off, so every caller that
does not ask for it behaves exactly as it did before. Where it does run, it
reuses this package's provider, errors, sanitizer and batching rather than
introducing its own.

The deterministic engines in core/services/academic/ and the local
translation engines are NOT replaced by any of this. They remain the
offline path and keep working unchanged.
"""
from .errors import (
    AIAssistError,
    AIBudgetExceededError,
    AINotConfiguredError,
    AIProviderError,
    AIRateLimitedError,
    AIResponseSchemaError,
    SegmentIntegrityError,
)
from .hashing import (
    compute_range_text_hash,
    compute_request_hash,
    compute_segment_content_hash,
    compute_source_text_hash,
    get_prompt_version,
    normalize_segment_text,
)
from .processor import AIPostProcessor, BatchResult, ProcessedSegment, build_batches
from .prompts import (
    ACADEMIC,
    CORRECT,
    CUSTOM,
    IMPROVE,
    MAX_CUSTOM_INSTRUCTION_CHARS,
    OPERATION_LABELS,
    OPERATIONS,
    TRANSLATE,
)
from .providers import (
    GeminiStructuredProvider,
    TokenUsage,
    build_provider,
    config,
    float_config,
    int_config,
    is_configured,
    resolve_model_name,
)
from .quality import (
    AIQualityEngine,
    DomainProfile,
    QualityIssue,
    QualityResult,
    QualityStatus,
    Severity,
    build_engine,
    default_profile_for,
    describe_profiles,
    get_profile,
    profile_names,
)
from .schemas import RESPONSE_SCHEMA

__all__ = [
    # errors
    'AIAssistError', 'AIBudgetExceededError', 'AINotConfiguredError',
    'AIProviderError', 'AIRateLimitedError', 'AIResponseSchemaError',
    'SegmentIntegrityError',
    # operations
    'OPERATIONS', 'OPERATION_LABELS', 'CORRECT', 'ACADEMIC', 'TRANSLATE',
    'IMPROVE', 'CUSTOM', 'MAX_CUSTOM_INSTRUCTION_CHARS',
    # processing
    'AIPostProcessor', 'BatchResult', 'ProcessedSegment', 'RESPONSE_SCHEMA',
    'build_batches',
    # provider / config
    'GeminiStructuredProvider', 'TokenUsage', 'build_provider',
    'is_configured', 'resolve_model_name', 'config', 'int_config',
    'float_config',
    # caching
    'compute_request_hash', 'compute_range_text_hash',
    'compute_source_text_hash', 'compute_segment_content_hash',
    'normalize_segment_text', 'get_prompt_version',
    # quality pipeline
    'AIQualityEngine', 'QualityResult', 'QualityStatus', 'QualityIssue',
    'Severity', 'DomainProfile', 'build_engine', 'get_profile',
    'profile_names', 'describe_profiles', 'default_profile_for',
]
