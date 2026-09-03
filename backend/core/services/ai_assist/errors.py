"""
Typed failures for the AI Assist path.

Every error here carries TWO messages, deliberately:

  * str(e) / .detail -- the full diagnostic, including provider response
    bodies. Logged server-side only.
  * .safe_message    -- what the client is allowed to see.

That split exists because AIRevision.error_message is serialized straight
back to the browser (see AIRevisionSerializer), and a provider error body
can contain the request URL, quota internals, or echoed prompt text. The
existing academic pipeline stores str(e) directly (tasks.py:760) which is
fine for a pure-local formatter with no secrets in scope, but would be a
disclosure bug here. run_ai_revision_pipeline stores .safe_message instead
and logs the detail -- see its own comment.
"""


class AIAssistError(Exception):
    """Base class. Subclasses override SAFE_MESSAGE with a client-safe string."""

    SAFE_MESSAGE = 'AI processing failed.'

    def __init__(self, detail: str = '', safe_message: str = ''):
        self.detail = detail or self.SAFE_MESSAGE
        self._safe_message = safe_message
        super().__init__(self.detail)

    @property
    def safe_message(self) -> str:
        return self._safe_message or self.SAFE_MESSAGE


class AINotConfiguredError(AIAssistError):
    """No API key / provider configured. Distinct from a provider failure so
    the capabilities endpoint can report 'unavailable' rather than 'broken',
    and so the UI can hide AI Assist instead of offering a button that
    always errors."""

    SAFE_MESSAGE = 'AI assistance is not configured on this server.'


class AIProviderError(AIAssistError):
    """Network/HTTP/transport failure talking to the provider. The detail may
    contain the raw response body -- never let it reach a response."""

    SAFE_MESSAGE = 'The AI service could not be reached. Please try again.'


class AIRateLimitedError(AIAssistError):
    """Provider returned 429, or our own limiter refused before dispatch."""

    SAFE_MESSAGE = 'AI request rate limit reached. Please wait and try again.'

    def __init__(self, detail: str = '', safe_message: str = '', retry_after=None):
        super().__init__(detail, safe_message)
        # Seconds the provider itself asked us to wait, parsed from its
        # google.rpc.RetryInfo -- or None when it didn't say.
        #
        # Carried on the exception because the caller that decides whether to
        # wait it out (AIPostProcessor) is two layers above the code that
        # reads the response. The distinction matters in both directions: a
        # short delay is worth sleeping through, while a delay measured in
        # hours means the *daily* free-tier quota is gone and retrying at all
        # is just a slower failure.
        self.retry_after = retry_after


class AIBudgetExceededError(AIAssistError):
    """Our own server-side token budget would be exceeded by this request.
    Refused before any provider call, so it costs nothing."""

    SAFE_MESSAGE = 'The AI usage budget for this server has been reached.'


class AIResponseSchemaError(AIAssistError):
    """Response wasn't parseable JSON, or didn't match the declared schema.
    Retried once by AIPostProcessor before surfacing."""

    SAFE_MESSAGE = 'The AI service returned an unusable response.'


class SegmentIntegrityError(AIAssistError):
    """
    The returned segment ids don't match the requested batch exactly --
    wrong count, missing, duplicated, or unknown id.

    This is the AI-Assist analogue of translation's MarkerIntegrityError
    (llm_fast_engine.py:17) and exists for the same load-bearing reason:
    without it, a model that drops or reorders one segment would shift
    every subsequent text onto the WRONG timestamp and speaker, silently
    desynchronizing the transcript from its audio. RULE 4 makes that
    unacceptable, so an integrity failure always falls back to leaving the
    batch's text unchanged rather than trusting a partial mapping.
    """

    SAFE_MESSAGE = 'The AI response did not line up with the transcript and was discarded.'
