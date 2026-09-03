"""
Eagle-specific fast-path discovery provider.

Target platform: https://translation.eaglewebcommerce.com/tasks/<numeric_id>

Confirmed delivery pattern:
  1. Authenticated Vite SPA loads.
  2. SPA calls /api/user/tasks/<id> for task metadata.
  3. Player / waveform requests /api/user/tasks/<id>/source (Content-Type: audio/mpeg).

Performance benefit:
  Bypasses the generic extensionless classification sniff (~1.4s), static HTML
  embedded scrape (~1s), and failed yt-dlp page extraction (~6-23s). Navigates
  directly in the existing shared Chromium POOL, intercepts the authenticated
  /source endpoint, and returns immediately without downloading the audio body
  in the browser.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import config
from .candidates import (
    BLOCKED_RESOURCE_TYPES, ResolvedMedia, classify_content_type, is_blob_url,
    is_blocked_host, make_candidate,
)
from .errors import DiscoveryCancelled, DiscoveryUnavailable
from .pool import POOL
from .resolver import _VIEWPORT, _cookies_for, _declared_size

logger = logging.getLogger("AIPipeline")

EAGLE_HOST_SUFFIX = "eaglewebcommerce.com"

# The exact hosts that serve task pages. An explicit set, NOT a suffix match.
#
# The previous `host.endswith(".eaglewebcommerce.com")` accepted anything
# under the domain, including `api.translation.eaglewebcommerce.com` -- an
# API host that serves JSON, not a task page. Sending the browser fast-path
# at it wastes a headless navigation and then falls back anyway. Hosts are a
# short, known list; matching them exactly costs nothing and cannot drift.
EAGLE_TASK_HOSTS = frozenset({
    "translation.eaglewebcommerce.com",
    "transcribe.eaglewebcommerce.com",
})

# Task-page paths across both products.
#
# `tasks?` -- singular AND plural -- because the transcription product is
# reachable at both /user/stt/task/<id>/show and /user/stt/tasks/<id>.
# Only the singular form was matched before, so a plural URL pasted from the
# browser fell off the fast path into generic discovery: slower, and it has
# to guess at the media endpoint instead of being handed it.
_EAGLE_TASK_PATH_PATTERNS = [
    re.compile(r"^/tasks?/(\d+)(?:/show)?/?$"),
    re.compile(r"^/user/stt/tasks?/(\d+)(?:/show)?/?$"),
    re.compile(r"^/user/tasks?/(\d+)(?:/show)?/?$"),
]
_POLL_INTERVAL_MS = 100


def parse_eagle_task_id(url: str) -> Optional[int]:
    """
    Returns the integer task ID if url matches an Eagle task page,
    e.g. 'https://translation.eaglewebcommerce.com/tasks/4058' -> 4058
    or 'https://transcribe.eaglewebcommerce.com/user/stt/task/37137/show' -> 37137.
    Returns None for any other host, path, or malformed URL.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in EAGLE_TASK_HOSTS:
            return None
        for pattern in _EAGLE_TASK_PATH_PATTERNS:
            match = pattern.match(parsed.path)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return None


def is_eagle_task_url(url: str) -> bool:
    """Returns True if url is an Eagle task URL."""
    return parse_eagle_task_id(url) is not None


def _resolve_eagle_in_browser(browser, url: str, task_id: int,
                              extra_headers: Optional[Dict[str, str]],
                              cancel_check=None,
                              user_agent: Optional[str] = None) -> Optional[ResolvedMedia]:
    """
    Executes in the shared POOL thread.
    Opens the Eagle task URL in an isolated context, listens specifically for
    the /api/user/tasks/{task_id}/source endpoint, aborts media body download,
    and returns immediately upon observation.
    """
    started = time.monotonic()
    deadline = started + config.total_deadline_seconds()
    allow_private = config.allow_private_hosts()

    target_path = f"/api/user/tasks/{task_id}/source"
    resolved_media: Optional[ResolvedMedia] = None

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
                    logger.warning('[EAGLE FAST-PATH] Could not seed cookies: %s', exc)

            passthrough = {
                k: v for k, v in extra_headers.items()
                if k.lower() not in ('cookie', 'user-agent') and v
            }
            if passthrough:
                try:
                    context.set_extra_http_headers(passthrough)
                except Exception as exc:
                    logger.warning('[EAGLE FAST-PATH] Could not set extra headers: %s', exc)

        page = context.new_page()
        page.set_default_timeout(config.nav_timeout_seconds() * 1000)

        def on_route(route):
            nonlocal resolved_media
            request = route.request
            try:
                req_path = urlparse(request.url).path
                if request.resource_type in BLOCKED_RESOURCE_TYPES or is_blocked_host(
                    request.url, allow_private=allow_private
                ):
                    route.abort()
                    return

                is_target_source = (f"/{task_id}/source" in req_path) or (req_path == target_path) or req_path.endswith("/source")
                # If the browser issues a media request directly (e.g. <audio src="...">)
                # or a request to the source endpoint, intercept it immediately without
                # downloading any audio bytes.
                if request.resource_type == 'media' or is_target_source:
                    try:
                        headers = request.all_headers()
                    except Exception:
                        headers = request.headers
                    if resolved_media is None:
                        resolved_media = make_candidate(
                            request.url, headers=headers, content_type='audio/mpeg', via='request:media',
                        )
                    route.abort()
                    return

                route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        page.route('**/*', on_route)

        def on_response(response):
            nonlocal resolved_media
            try:
                resp_path = urlparse(response.url).path
                headers = {str(k).lower(): v for k, v in (response.headers or {}).items()}
                content_type = (headers.get('content-type') or '').split(';')[0].strip().lower()
                is_target_source = (f"/{task_id}/source" in resp_path) or (resp_path == target_path) or resp_path.endswith("/source")

                if is_target_source or (classify_content_type(content_type) and not is_blob_url(response.url)):
                    if 200 <= response.status < 400 and resolved_media is None:
                        try:
                            request_headers = response.request.all_headers()
                        except Exception:
                            request_headers = response.request.headers
                        resolved_media = make_candidate(
                            response.url,
                            headers=request_headers,
                            content_type=content_type or 'audio/mpeg',
                            byte_size=_declared_size(headers),
                            via='response:audio',
                        )
            except Exception:
                pass

        page.on('response', on_response)

        try:
            page.goto(url, wait_until='commit', timeout=config.nav_timeout_seconds() * 1000)
        except Exception as exc:
            logger.warning(f"[EAGLE FAST-PATH] Navigation commit warning: {exc}")

        # Settle loop: poll until the target source endpoint is seen or timeout
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                raise DiscoveryCancelled(f"Discovery cancelled for Eagle task {task_id}")
            if resolved_media is not None:
                break
            page.wait_for_timeout(_POLL_INTERVAL_MS)

        if resolved_media is not None:
            try:
                cookies = context.cookies()
                if cookies:
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                    resolved_media.headers['Cookie'] = cookie_str
                    resolved_media.headers['cookie'] = cookie_str
            except Exception as exc:
                logger.warning(f"[EAGLE FAST-PATH] Could not harvest context cookies: {exc}")

            elapsed = time.monotonic() - started
            logger.info(f"[EAGLE FAST-PATH] Discovered media endpoint in {elapsed:.2f}s: {resolved_media.describe()}")
        else:
            logger.warning(f"[EAGLE FAST-PATH] No source endpoint seen for task {task_id} after {time.monotonic() - started:.2f}s")

        return resolved_media
    finally:
        context.close()


def resolve_eagle_task(url: str, extra_headers: Optional[Dict[str, str]] = None,
                       cancel_check=None, user_agent: Optional[str] = None) -> Optional[ResolvedMedia]:
    """
    Public entry point for Eagle task discovery.
    Validates the task URL, submits the browser worker to the shared POOL,
    and returns ResolvedMedia if found.
    """
    task_id = parse_eagle_task_id(url)
    if task_id is None:
        return None

    if cancel_check and cancel_check():
        raise DiscoveryCancelled(f"Cancelled before resolving Eagle task {task_id}")

    timeout = config.total_deadline_seconds() + 5

    return POOL.submit(
        lambda browser: _resolve_eagle_in_browser(
            browser, url, task_id, extra_headers, cancel_check=cancel_check, user_agent=user_agent
        ),
        timeout=timeout,
    )
