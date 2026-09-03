import logging
import urllib.request
from typing import Dict, Optional

try:
    from backend.core.services.ai_pipeline import (
        DEFAULT_USER_AGENT, MediaDiscovery, read_response_body_with_deadline,
        PAGE_FETCH_DEADLINE_SECONDS, fetch_page_impersonated,
    )
except ModuleNotFoundError:
    from core.services.ai_pipeline import (
        DEFAULT_USER_AGENT, MediaDiscovery, read_response_body_with_deadline,
        PAGE_FETCH_DEADLINE_SECONDS, fetch_page_impersonated,
    )

logger = logging.getLogger("AIPipeline")


def fetch_page(url: str, timeout: int = 15, extra_headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Best-effort static HTML fetch shared by every page-inspecting strategy.
    Returns None on any failure rather than raising -- discovery strategies
    must degrade gracefully, never break the caller.

    Sends cookies/headers in this priority order:
      1. extra_headers, if given -- per-job auth the user explicitly pasted
         for this specific URL (see core.services.auth_helper). Takes full
         precedence when present, rather than merging with #2, to avoid
         sending two conflicting Cookie headers.
      2. Otherwise, YTDLP_COOKIES_FILE when configured (MediaDiscovery.cookie_jar()) --
         a server-wide default.
    Some pages require a real login session to show their actual content
    rather than a login form -- without either of these, discovery on such
    a page always sees the login page and correctly (but unhelpfully)
    reports no series found, since a login form has no episode/lesson
    structure.
    """
    try:
        headers = {'User-Agent': DEFAULT_USER_AGENT}
        if extra_headers:
            headers.update(extra_headers)

        # Tried first: some sites tarpit plain urllib/http.client requests
        # based on their TLS/HTTP2 handshake rather than rejecting them
        # outright -- see fetch_page_impersonated's docstring for the
        # confirmed real-world case (discovery on this exact kind of URL
        # was taking 200+ seconds because of this). Falls through to the
        # plain urllib path below on any failure.
        impersonated = fetch_page_impersonated(url, headers, timeout=timeout)
        if impersonated is not None:
            return impersonated.decode('utf-8', errors='ignore')

        req = urllib.request.Request(url, headers=headers)

        if extra_headers and extra_headers.get('Cookie'):
            opener = urllib.request.build_opener()
        else:
            cookie_jar = MediaDiscovery.cookie_jar()
            opener = (
                urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                if cookie_jar is not None else urllib.request.build_opener()
            )

        with opener.open(req, timeout=timeout) as response:
            # timeout= above only bounds each individual read/connect --
            # a page that stays alive but trickles bytes slowly enough to
            # never go fully silent for that long can otherwise stall far
            # past `timeout` (confirmed live: a 65s page fetch despite
            # timeout=30 elsewhere in this same fallback chain). This adds
            # the missing overall wall-clock cap on top.
            return read_response_body_with_deadline(response, PAGE_FETCH_DEADLINE_SECONDS).decode('utf-8', errors='ignore')
    except Exception as e:
        logger.warning(f"Could not fetch page for discovery: {e}")
        return None
