"""
The browser strategy: execute the page's JavaScript just far enough to see
which network request supplies the media, then stop.

What this does NOT do, deliberately:

  * It does not wait for the page to go idle. Navigation uses
    wait_until='commit' -- the earliest possible return -- and everything
    after that is a bounded poll. An analytics-heavy app may never reach
    networkidle at all.
  * It does not render images, fonts, or analytics. Those are aborted at the
    route layer before a byte moves. Stylesheets continue because Vite can
    make them a required dependency of a lazy JavaScript import.
  * It does not download the audio. A request the browser itself types as
    `media` is captured and ABORTED at request time -- zero bytes. A
    fetch()-driven one (the blob case) is recognised from its response
    HEADERS, which arrive before the body, and the context is torn down
    immediately afterwards.
  * It never returns a blob: URL. A blob exists only inside the renderer;
    what the download pipeline needs is the HTTP request that fed it. See
    candidates.make_candidate, the single chokepoint that enforces this.
"""
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from . import config
from .candidates import (
    BLOCKED_RESOURCE_TYPES, ResolvedMedia, classify_content_type, is_blob_url,
    is_blocked_host, make_candidate, pick_best,
)
from .errors import DiscoveryCancelled
from .pool import POOL

logger = logging.getLogger("AIPipeline")

_POLL_MS = 100

# Small viewport: less layout work, and a player still mounts.
_VIEWPORT = {'width': 1280, 'height': 720}

# Playback nudges, in seconds after navigation commits. Many players only
# issue their media request once playback begins, and a headless page has
# nobody to press play. Repeated a few times because the player element may
# not exist yet on the first attempt -- an SPA mounts it after its own data
# fetch resolves.
_NUDGE_SCHEDULE_SECONDS = (0.4, 1.5, 3.5)

# Collects <audio>/<video>/<source> sources across the document AND every
# OPEN shadow root. A bare document.querySelectorAll('audio') would miss the
# player on the confirmed real page, whose <audio> lives inside
# `.waveform-player`'s #shadow-root. Closed roots are unreachable by design
# and simply yield nothing.
_MEDIA_SRC_SCAN_JS = """
() => {
  const found = [];
  const seen = new Set();
  const visit = (root) => {
    if (!root || seen.has(root)) return;
    seen.add(root);
    let media = [];
    try { media = root.querySelectorAll('audio, video, source'); } catch (e) { return; }
    for (const el of media) {
      const src = el.currentSrc || el.src || el.getAttribute('src');
      if (src) found.push(src);
    }
    let all = [];
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of all) if (el.shadowRoot) visit(el.shadowRoot);
  };
  visit(document);
  return found;
}
"""

# Starts playback on every media element it can reach, shadow roots included.
# Muted first: an unmuted autoplay is refused by policy even with the
# command-line flag, and we never want audio out of a server anyway.
_NUDGE_PLAYBACK_JS = """
() => {
  let started = 0;
  const seen = new Set();
  const visit = (root) => {
    if (!root || seen.has(root)) return;
    seen.add(root);
    let media = [];
    try { media = root.querySelectorAll('audio, video'); } catch (e) { return; }
    for (const el of media) {
      try {
        el.muted = true;
        if (el.readyState === 0 && typeof el.load === 'function') el.load();
        const p = el.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
        started++;
      } catch (e) { /* autoplay refusal is not our problem */ }
    }
    let all = [];
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of all) if (el.shadowRoot) visit(el.shadowRoot);
  };
  visit(document);
  return started;
}
"""


def _cookies_for(url: str, cookie_header: str) -> List[Dict[str, str]]:
    """
    Converts a `Cookie: a=b; c=d` header into Playwright cookie objects
    scoped to the target host.

    Done as cookies rather than as a raw extra header because a browser
    context manages its own Cookie header -- setting one via
    set_extra_http_headers would either be overwritten or produce two
    conflicting values, which is the same trap fetch_page documents in the
    static path.
    """
    host = urlparse(url).hostname or ''
    if not host:
        return []
    cookies = []
    for part in cookie_header.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        name, value = part.split('=', 1)
        name, value = name.strip(), value.strip()
        if name:
            cookies.append({'name': name, 'value': value, 'domain': host, 'path': '/'})
    return cookies


def _declared_size(headers: Dict[str, str]) -> Optional[int]:
    """
    Body size from Content-Length, or the total from a Content-Range
    (`bytes 0-1/12345`). Range responses matter here: a media player's very
    first request is often a tiny probe range, whose Content-Length is
    meaningless but whose Content-Range names the real file size.
    """
    length = headers.get('content-length')
    if length:
        try:
            return int(length)
        except (TypeError, ValueError):
            pass
    content_range = headers.get('content-range') or ''
    if '/' in content_range:
        total = content_range.rsplit('/', 1)[-1].strip()
        if total.isdigit():
            return int(total)
    return None


def _resolve_in_browser(browser, url: str, extra_headers: Optional[Dict[str, str]],
                        cancel_check, user_agent: Optional[str]) -> Optional[ResolvedMedia]:
    """
    Runs on the pool's owner thread. Every Playwright call in the package
    happens inside this function (and nothing here touches job state), which
    is what keeps the sync API's thread affinity intact.
    """
    started = time.monotonic()
    hard_deadline = started + config.total_deadline_seconds()
    min_octet = config.min_octet_stream_bytes()
    allow_private = config.allow_private_hosts()

    candidates: List[ResolvedMedia] = []
    saw_blob = [False]

    context_options: Dict[str, Any] = {'viewport': _VIEWPORT, 'ignore_https_errors': False}
    if user_agent:
        context_options['user_agent'] = user_agent

    context = browser.new_context(**context_options)
    try:
        if extra_headers:
            cookie_header = extra_headers.get('Cookie') or extra_headers.get('cookie')
            if cookie_header:
                try:
                    context.add_cookies(_cookies_for(url, cookie_header))
                except Exception as exc:
                    logger.warning('[DYNAMIC DISCOVERY] Could not seed cookies: %s', exc)
            # User-Agent goes through new_context above; Cookie is handled as
            # real cookies. Everything else the user pasted (an
            # Authorization header, a custom session header) is forwarded.
            passthrough = {
                key: value for key, value in extra_headers.items()
                if key.lower() not in ('cookie', 'user-agent') and value
            }
            if passthrough:
                try:
                    context.set_extra_http_headers(passthrough)
                except Exception as exc:
                    logger.warning('[DYNAMIC DISCOVERY] Could not set extra headers: %s', exc)

        page = context.new_page()
        page.set_default_timeout(config.nav_timeout_seconds() * 1000)

        def on_route(route):
            request = route.request
            try:
                if request.resource_type in BLOCKED_RESOURCE_TYPES or is_blocked_host(
                    request.url, allow_private=allow_private
                ):
                    route.abort()
                    return

                if request.resource_type == 'media':
                    # The browser has told us this request is for a media
                    # element. That is everything we need, so abort it: not
                    # one byte of audio is transferred.
                    try:
                        headers = request.all_headers()
                    except Exception:
                        headers = request.headers
                    candidate = make_candidate(
                        request.url, headers=headers, via='request:media',
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                    route.abort()
                    return

                route.continue_()
            except Exception:
                # A route handler that raises stalls the request forever.
                try:
                    route.continue_()
                except Exception:
                    pass

        page.route('**/*', on_route)

        def on_response(response):
            try:
                headers = {str(k).lower(): v for k, v in (response.headers or {}).items()}
                content_type = headers.get('content-type', '')
                if not classify_content_type(content_type):
                    return
                if is_blob_url(response.url):
                    saw_blob[0] = True
                    return
                try:
                    request_headers = response.request.all_headers()
                except Exception:
                    request_headers = response.request.headers
                candidate = make_candidate(
                    response.url,
                    headers=request_headers,
                    content_type=content_type,
                    byte_size=_declared_size(headers),
                    via=f'response:{classify_content_type(content_type)}',
                )
                if candidate is not None:
                    candidates.append(candidate)
            except Exception:
                # Listeners must never raise into Playwright's event loop.
                pass

        page.on('response', on_response)

        if cancel_check and cancel_check():
            raise DiscoveryCancelled('Cancelled before dynamic discovery navigation.')

        nav_budget = min(config.nav_timeout_seconds(), max(1.0, hard_deadline - time.monotonic()))
        try:
            page.goto(url, wait_until='commit', timeout=nav_budget * 1000)
        except Exception as exc:
            # A commit failure still leaves anything already captured usable.
            logger.warning('[DYNAMIC DISCOVERY] Navigation did not commit cleanly: %s', exc)

        navigated_at = time.monotonic()
        logger.info('[DYNAMIC DISCOVERY] Navigation committed in %.2fs.', navigated_at - started)

        settle_deadline = min(hard_deadline, navigated_at + config.settle_seconds())
        nudges = list(_NUDGE_SCHEDULE_SECONDS)

        while True:
            if cancel_check and cancel_check():
                raise DiscoveryCancelled('Cancelled during dynamic discovery.')

            best = pick_best(candidates, min_octet_bytes=min_octet)
            if best is not None:
                logger.info(
                    '[DYNAMIC DISCOVERY] Media source found in %.2fs: %s',
                    time.monotonic() - started, best.describe(),
                )
                return best

            now = time.monotonic()
            if now >= settle_deadline:
                break

            if nudges and now - navigated_at >= nudges[0]:
                nudges.pop(0)
                try:
                    page.evaluate(_NUDGE_PLAYBACK_JS)
                except Exception:
                    pass

            page.wait_for_timeout(_POLL_MS)

        # Nothing was intercepted. Last resort: read the live DOM (shadow
        # roots included). This catches media that was already cached, or an
        # element whose src was assigned without a fetch we saw. A blob: src
        # here is informative but not usable -- it proves a player exists and
        # tells us interception missed the feeding request.
        try:
            sources = page.evaluate(_MEDIA_SRC_SCAN_JS) or []
        except Exception:
            sources = []

        try:
            base_url = page.url or url
        except Exception:
            base_url = url

        for source in sources:
            if is_blob_url(source):
                saw_blob[0] = True
                continue
            # el.src reads back absolute, but getAttribute('src') does not --
            # resolve against the page's own (possibly redirected) URL.
            try:
                absolute = urljoin(base_url, source)
            except Exception:
                continue
            candidate = make_candidate(absolute, via='dom:media-element')
            if candidate is not None:
                candidates.append(candidate)

        best = pick_best(candidates, min_octet_bytes=min_octet)
        if best is not None:
            logger.info(
                '[DYNAMIC DISCOVERY] Media source found via DOM scan in %.2fs: %s',
                time.monotonic() - started, best.describe(),
            )
            return best

        if saw_blob[0]:
            logger.warning(
                '[DYNAMIC DISCOVERY] A blob-backed player was found but the request that '
                'created it was not observed within the budget. Not returning a blob: URL.'
            )
        logger.info(
            '[DYNAMIC DISCOVERY] No media source found for %s after %.2fs.',
            url, time.monotonic() - started,
        )
        return None
    finally:
        try:
            context.close()
        except Exception as exc:
            logger.warning('[DYNAMIC DISCOVERY] Error closing browser context: %s', exc)


def resolve(url: str, extra_headers: Optional[Dict[str, str]] = None,
            cancel_check=None, user_agent: Optional[str] = None) -> Optional[ResolvedMedia]:
    """
    Resolves a JavaScript-rendered page to a fetchable media URL, or returns
    None if it cannot.

    Raises DiscoveryCancelled if the job was cancelled, DiscoveryUnavailable
    if Playwright/Chromium isn't installed, and DynamicDiscoveryError if the
    shared browser thread doesn't answer in time. Callers treat everything
    except DiscoveryCancelled as "this strategy found nothing".
    """
    # Slack over the internal deadline so the pool's wait is the outer bound
    # and the resolver's own deadline is what normally fires -- the internal
    # one can tear the context down cleanly, the outer one cannot.
    timeout = config.total_deadline_seconds() + 15.0
    return POOL.submit(
        lambda browser: _resolve_in_browser(browser, url, extra_headers, cancel_check, user_agent),
        timeout=timeout,
    )
