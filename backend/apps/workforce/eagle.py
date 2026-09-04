"""
The seam Phase 4 will implement behind. Nothing here talks to Eagle yet.

WHY THIS EXISTS NOW, EMPTY. The rest of the application is about to grow
code paths that fetch media and submit work. If those call `curl_cffi`,
Playwright, or a cookie jar directly, then Phase 4 is not an integration
task any more -- it is a refactor of everything that reached around this
boundary. Defining the interface first costs nothing and keeps the blast
radius of the real integration inside one file.

WHAT IS DELIBERATELY NOT DECIDED HERE. Phase 1 established that nothing
about Eagle's session handling should be assumed, and two concrete facts
already contradict the obvious guesses:

  * the submission document is currently built CLIENT-SIDE and copied to
    the clipboard -- it never passes through the server, so `submit_task`
    has no existing server-side path to reuse;
  * task URLs span two hosts and several path shapes, already handled by
    `core.services.dynamic_discovery.eagle`.

So the signatures below describe WHAT the application needs, not HOW it
will be obtained. Return types are plain dataclasses rather than provider
objects for the same reason.

CREDENTIAL BOUNDARY. Workers authenticate to THIS platform; the Eagle
session belongs to the platform, not to them. No method here accepts or
returns credentials, and no result carries a cookie, token or signed URL
back toward a worker-facing serializer. Phase 4 must keep that true.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class EagleIntegrationError(Exception):
    """
    Base failure. Carries the same two-message split used throughout this
    codebase: `.detail` for logs, `.safe_message` for responses, because an
    upstream error body can quote request URLs and session internals.
    """

    SAFE_MESSAGE = 'The external task platform could not be reached.'

    def __init__(self, detail: str = '', safe_message: str = ''):
        self.detail = detail or self.SAFE_MESSAGE
        self._safe = safe_message
        super().__init__(self.detail)

    @property
    def safe_message(self) -> str:
        return self._safe or self.SAFE_MESSAGE


class EagleNotConfigured(EagleIntegrationError):
    SAFE_MESSAGE = 'External platform integration is not configured on this server.'


class EagleAuthError(EagleIntegrationError):
    SAFE_MESSAGE = 'The external platform rejected this server\'s session.'


class EagleLoginFailed(EagleAuthError):
    """
    A real username/password login attempt was rejected or could not
    complete. Its own subclass of EagleAuthError -- not just its own
    message -- so a caller (the CLI, the retry cooldown bookkeeping) can
    tell "the stored PASSWORD doesn't work" apart from "the stored SESSION
    merely lapsed" without parsing text.
    """
    SAFE_MESSAGE = "The external platform rejected this server's login."


class EagleTaskNotFound(EagleIntegrationError):
    SAFE_MESSAGE = 'That task was not found on the external platform.'


@dataclass
class EagleTask:
    """What the platform knows about a task, normalized."""
    external_id: str
    source: str = ''
    title: str = ''
    kind: str = ''
    source_language: str = ''
    target_language: str = ''
    duration_seconds: Optional[int] = None
    #: The platform's project / category label, which DECIDES THE
    #: TRANSCRIPTION STANDARD, and its own billing figure in whole minutes.
    #: Both are first-class fields rather than entries in `raw` because the
    #: sync writes them to columns -- and a value that only exists inside a
    #: dict is a value the next reader forgets to carry across.
    project: str = ''
    minutes_required: Optional[int] = None
    #: When the platform's own clock runs out, ISO-8601 as Eagle sends it.
    #: The single highest-stakes number on a worker's screen.
    deadline_at: str = ''
    page_url: str = ''
    raw: Dict = field(default_factory=dict)


@dataclass
class EagleMedia:
    """
    A resolved media reference.

    `url` may require the platform's session to fetch, which is exactly why
    `headers` stays server-side and is never serialized toward a client.
    """
    url: str
    content_type: str = ''
    size_bytes: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class EagleSubmissionResult:
    accepted: bool
    reference: str = ''
    message: str = ''
    raw: Dict = field(default_factory=dict)


class EagleIntegrationService:
    """
    The interface the application codes against.

    This base class is intentionally inert: every method raises
    `EagleNotConfigured`. That is a feature, not a placeholder -- it means a
    caller wired up before Phase 4 fails loudly and immediately rather than
    silently doing nothing, and it means the null case is the DEFAULT rather
    than something a future implementer must remember to handle.
    """

    name = 'null'

    def is_configured(self) -> bool:
        return False

    # ---------------------------------------------------------------- #
    # Task discovery
    # ---------------------------------------------------------------- #

    def get_task(self, external_id: str, source: str = '') -> EagleTask:
        """Metadata for one external task."""
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def list_available_tasks(self, source: str = '') -> List[EagleTask]:
        """
        Tasks the platform is offering.

        Phase 4 note: whether this is even possible is unverified. It may
        require a browser session rather than an API, in which case task
        intake becomes an operator action rather than a poll. The interface
        allows either without the callers caring.
        """
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    # ---------------------------------------------------------------- #
    # Media
    # ---------------------------------------------------------------- #

    def get_task_media(self, external_id: str, source: str = '') -> EagleMedia:
        """Resolve the media reference for a task."""
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def get_authenticated_media(self, external_id: str, source: str = '') -> EagleMedia:
        """
        Media plus whatever the platform's session requires to fetch it.

        Separate from `get_task_media` because the credential-bearing form
        must be requestable explicitly. A caller that only needs a public
        URL should not receive headers it then has to remember not to leak.
        """
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    # ---------------------------------------------------------------- #
    # Submission
    # ---------------------------------------------------------------- #

    def submit_task(
        self, external_id: str, content: str, source: str = '',
        metadata: Optional[Dict] = None,
    ) -> EagleSubmissionResult:
        """
        Submit finished work.

        `content` is the rendered submission document, produced server-side
        by `core.services.export_formats`. Phase 1 found the UI currently
        builds this in the browser and copies it to the clipboard; a
        controlled workflow has to move that server-side, and this signature
        assumes it has been.
        """
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def check_session(self, source: str = '') -> Dict:
        """
        Is the stored session still usable, and what does the account look
        like? Called every poll cycle, which doubles as the keep-alive.
        """
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def minutes_balance(self, source: str = '') -> Optional[int]:
        """Purchased minutes remaining -- the real throughput cap."""
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def list_my_tasks(self, source: str = '') -> List[EagleTask]:
        """Tasks this account already holds."""
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def claim_task(self, external_id: str, source: str = '') -> Dict:
        """
        Accept a task. SPENDS MINUTES and starts the external deadline, which
        is why the inert default refuses rather than quietly doing nothing.
        """
        raise EagleNotConfigured('Eagle integration is not implemented yet.')

    def get_submission_status(self, external_id: str, source: str = '') -> str:
        """Current outcome as the platform reports it: submitted, approved,
        rejected, or unknown."""
        raise EagleNotConfigured('Eagle integration is not implemented yet.')


_service: Optional[EagleIntegrationService] = None


def get_eagle_service() -> EagleIntegrationService:
    """
    The single accessor. Everything that needs Eagle goes through this.

    Returns the inert base until Phase 4 registers a real implementation,
    so importing this module never pulls in Playwright, curl_cffi or a
    cookie jar -- and a unit test can substitute a fake with one call to
    `set_eagle_service`.
    """
    global _service
    if _service is None:
        # Phase 4 registers the real implementation lazily, so importing this
        # module still pulls in nothing heavy and a test that installs a fake
        # first is never overridden. Falling back to the inert base keeps the
        # null case the DEFAULT rather than a failure mode.
        try:
            from .eagle_impl import EagleService
            _service = EagleService()
        except Exception:  # pragma: no cover - import-time safety net
            _service = EagleIntegrationService()
    return _service


def set_eagle_service(service: Optional[EagleIntegrationService]) -> None:
    """Install an implementation (Phase 4) or a fake (tests). None resets."""
    global _service
    _service = service
