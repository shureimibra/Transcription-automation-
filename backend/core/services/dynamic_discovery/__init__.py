"""
Dynamic media discovery for JavaScript-rendered pages.

The static pipeline resolves a page to audio by reading the HTML the server
sent. That fails completely for a client-rendered application: the server
sends a shell, and the player -- along with the media URL -- only exists after
the page's JavaScript has run. The confirmed case is a Vue app whose <audio>
element lives inside an open shadow root with a `blob:` src, meaning even a
post-render scrape of the DOM finds nothing fetchable.

This package closes that gap by executing the page just far enough to observe
which HTTP request supplies the media, then handing that URL back to the
existing download pipeline. It resolves URLs; it does not download, transcode,
or transcribe anything.

Where it sits in the chain (see AudioExtractor.download_and_extract_url):

    static scrape  ->  yt-dlp generic  ->  DYNAMIC DISCOVERY  ->  fail

Last, deliberately. Any URL that works today is resolved before this code is
reached, so no currently-working path gains a millisecond of latency. And even
when reached, `should_attempt` must first find SPA evidence in the HTML that
was already fetched -- no extra request, no browser -- before a browser is
launched at all.

The whole feature degrades silently. If Playwright isn't installed, or the
browser binary is missing, or the switch is off, callers see "found nothing"
and the download fails exactly as it does today.

Public API:

    is_available()                -> bool
    should_attempt(html)          -> (bool, reason)
    resolve(url, ...)             -> ResolvedMedia | None
    ResolvedMedia, DiscoveryCancelled, DiscoveryUnavailable, DynamicDiscoveryError
"""
from typing import Optional, Tuple

from .candidates import ResolvedMedia
from .eagle import is_eagle_task_url, parse_eagle_task_id, resolve_eagle_task
from .errors import DiscoveryCancelled, DiscoveryUnavailable, DynamicDiscoveryError
from .resolver import resolve
from .spa_detect import looks_like_js_app

__all__ = [
    'ResolvedMedia',
    'DiscoveryCancelled',
    'DiscoveryUnavailable',
    'DynamicDiscoveryError',
    'is_available',
    'should_attempt',
    'resolve',
    'looks_like_js_app',
    'is_eagle_task_url',
    'parse_eagle_task_id',
    'resolve_eagle_task',
]


def is_available() -> bool:
    """
    Whether dynamic discovery could run at all: switched on and Playwright
    importable. Cheap -- no browser launch, no network. A True here still
    doesn't guarantee a browser binary exists; that surfaces as
    DiscoveryUnavailable at launch and is handled identically.
    """
    from . import config
    from .pool import playwright_available
    return config.is_enabled() and playwright_available()


def should_attempt(html: Optional[str]) -> Tuple[bool, str]:
    """
    The gate. Returns (verdict, reason) where `reason` is log-ready.

    `html` is whatever the caller already fetched for this URL -- this
    function never fetches anything itself. That is the entire point: the
    decision to spend a browser launch is made from evidence already in hand.

    DYNAMIC_DISCOVERY_FORCE skips the SPA heuristic but not the availability
    check, so forcing it on a machine without Playwright still can't break a
    download.
    """
    from . import config

    if not config.is_enabled():
        return False, 'disabled by configuration'

    from .pool import playwright_available
    if not playwright_available():
        return False, 'playwright not installed'

    if config.force_attempt():
        return True, 'forced by DYNAMIC_DISCOVERY_FORCE'

    verdict, signals = looks_like_js_app(html or '')
    detail = ','.join(signals) if signals else 'none'
    if verdict:
        return True, f'js-app signals: {detail}'
    if not html:
        return False, 'no page HTML available to assess'
    return False, f'not a js-app (signals: {detail})'
