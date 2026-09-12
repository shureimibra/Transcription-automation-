"""
Domain-aware quality control for AI Assist output.

The problem this package exists for: a single AI pass that returns
well-formed text tells you the request succeeded, not that the text is
correct. AIRevision.status='completed' was being read as "safe to submit",
and a translation that read beautifully was rejected downstream for
misstating a legal obligation, drifting between renderings of one term, and
collapsing two distinct concepts into one. None of those are visible to
anyone reading only the output.

    SOURCE
      -> source analysis        domain, register, risk, terminology, entities
      -> primary generation     (AIPostProcessor -- unchanged, upstream)
      -> deterministic checks   numbers, entities, typography, leakage
      -> adversarial audit      source-authoritative, structured findings
      -> acceptance gate        the APPLICATION decides, never the model
      -> targeted repair        only verified defects, bounded iterations
      -> re-audit
      -> VALIDATED | QUALITY_REVIEW_REQUIRED | TECHNICAL_FAILURE

Layering, outermost to innermost:

    tasks.run_ai_revision_pipeline  persistence, progress, cancellation
      -> AIQualityEngine            iteration, acceptance gate  (engine)
        -> stages                   analyse / audit / repair calls
        -> deterministic, terminology, typography   no provider, no cost
        -> prompts / schemas        untrusted-data handling, forced JSON
          -> GeminiStructuredProvider   reused; still the only key holder

WHAT THIS PACKAGE IS NOT:

  * Not a second provider. It borrows ai_assist.providers wholesale (spec
    23) -- there is exactly one Gemini client in this codebase.
  * Not a second task system, event bus, or telemetry framework. It returns
    a result object; the existing pipeline persists and broadcasts it.
  * Not legal-specific, and not French-specific. There is no `if domain ==`
    anywhere in it. Domain knowledge is DISCOVERED per document and reaches
    the model as data (DomainProfile), and language-specific rules live
    behind a registry keyed on language code (RULE 8, spec 20, spec 22).
  * Not on by default. `AI_QUALITY_DEFAULT_PROFILE` defaults to 'none', so
    every existing caller behaves exactly as before until a profile is
    asked for (RULE 17, RULE 18).
"""
from .deterministic import (
    check_commentary,
    check_address_consistency,
    check_autocorrect,
    check_common_word_leakage,
    check_currency_format,
    check_encoding,
    check_foreign_language,
    check_invented_references,
    check_duplication,
    check_length_anomaly,
    check_numbers,
    check_source_leakage,
    check_verbatim_tokens,
    extract_numbers,
    run_deterministic_checks,
)
from .engine import AIQualityEngine, QualityResult, build_engine
from .issues import (
    BLOCKING_SEVERITIES,
    IssueCategory,
    QualityIssue,
    QualityStatus,
    Severity,
    sort_issues,
    summarize,
)
from .profiles import (
    ACADEMIC,
    ALL_STAGES,
    EAGLE_WEB,
    GENERAL,
    NONE,
    TRANSLATION,
    VOLUME,
    DomainProfile,
    OperationProfile,
    QualityPolicy,
    default_profile_for,
    describe_profiles,
    get_profile,
    profile_names,
)
from .prompts import PROMPT_VERSION
from .terminology import (
    check_distinct_concepts,
    check_entity_preservation,
    check_rendering_consistency,
    check_terminology_consistency,
    run_anchor_checks,
)
from .transcription import (
    EDITED_VERBATIM,
    STRICT_VERBATIM,
    TranscriptionStandard,
    check_capitalization,
    check_number_rules,
    check_paragraphing,
    check_quote_style,
    check_speaker_labels,
    check_speaker_numbering,
    check_standard_policy,
    check_timestamps,
    describe_standards,
    get_standard,
    standard_for_project,
    PROJECT_STANDARDS,
    run_transcript_checks,
    run_transcript_content_checks,
)
from .typography import (
    TypographyRegistry,
    check_typography,
    normalize_typography,
)

__all__ = [
    # engine
    'AIQualityEngine', 'QualityResult', 'build_engine',
    # vocabulary
    'QualityIssue', 'QualityStatus', 'Severity', 'IssueCategory',
    'BLOCKING_SEVERITIES', 'summarize', 'sort_issues',
    # profiles
    'DomainProfile', 'OperationProfile', 'QualityPolicy', 'get_profile',
    'profile_names', 'describe_profiles', 'default_profile_for', 'ALL_STAGES',
    'NONE', 'TRANSLATION', 'ACADEMIC', 'GENERAL', 'EAGLE_WEB', 'VOLUME',
    # deterministic checks
    'run_deterministic_checks', 'check_numbers', 'check_verbatim_tokens',
    'check_invented_references', 'check_foreign_language',
    'check_duplication', 'check_length_anomaly', 'check_commentary',
    'check_encoding', 'check_autocorrect', 'check_address_consistency',
    'check_source_leakage', 'check_common_word_leakage',
    'check_currency_format', 'extract_numbers',
    # terminology / entities
    'run_anchor_checks', 'check_terminology_consistency',
    'check_distinct_concepts', 'check_entity_preservation',
    'check_rendering_consistency',
    # typography
    'check_typography', 'normalize_typography', 'TypographyRegistry',
    # transcription standards (Eagle Web's two verbatim rubrics)
    'run_transcript_checks', 'run_transcript_content_checks',
    'get_standard', 'describe_standards',
    'standard_for_project', 'PROJECT_STANDARDS',
    'TranscriptionStandard', 'EDITED_VERBATIM', 'STRICT_VERBATIM',
    'check_speaker_labels', 'check_timestamps', 'check_number_rules',
    'check_capitalization', 'check_paragraphing', 'check_quote_style',
    'check_speaker_numbering',
    'check_standard_policy',
    # prompts
    'PROMPT_VERSION',
]
