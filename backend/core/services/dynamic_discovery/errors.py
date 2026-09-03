"""
Exceptions for dynamic media discovery.

Deliberately defined here rather than reusing ai_pipeline's JobCancelledError:
ai_pipeline imports THIS package (see AudioExtractor._try_dynamic_discovery),
so importing back the other way would be a circular import. The thin wrapper
in ai_pipeline translates DiscoveryCancelled into JobCancelledError, keeping
this package free of any dependency on the module that calls it.
"""


class DynamicDiscoveryError(Exception):
    """Base for every error raised by this package."""
    pass


class DiscoveryUnavailable(DynamicDiscoveryError):
    """
    Raised when dynamic discovery cannot run at all: the optional Playwright
    dependency isn't installed, no browser binary is present, or the feature
    is switched off by configuration.

    Treated by callers as "this strategy doesn't apply", never as a download
    failure -- exactly like a discovery strategy returning None.
    """
    pass


class DiscoveryCancelled(DynamicDiscoveryError):
    """
    Raised when the job was cancelled mid-discovery. Distinct from "found
    nothing" because the caller must not report a cancelled job as failed
    (see run_transcription_pipeline's handling of JobCancelledError).
    """
    pass
