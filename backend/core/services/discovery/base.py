from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class DiscoveryStrategy(ABC):
    """
    One technique for detecting that a URL is part of a larger media
    collection (playlist, podcast feed, course, next-page chain, ...).

    Each strategy is self-contained: it decides for itself whether it
    applies to a given URL (e.g. by checking MediaDiscovery.classify()) and
    returns None immediately if not, rather than relying on the engine to
    route to it. This is what makes the list of strategies genuinely
    pluggable -- adding support for a new kind of site/platform means
    writing one new strategy class and adding it to the list in engine.py,
    nothing else changes.
    """

    name: str = "unknown"

    @abstractmethod
    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Returns {'items': [{'url': str, 'title': str}, ...], 'current_index': int | None}
        if this strategy found 2+ related items, or None if it doesn't
        apply to this URL or found nothing. Must never raise -- catch your
        own errors and return None, since a broken strategy should degrade
        to "no collection found", not break discovery for everyone else.

        extra_headers: optional per-job auth (see core.services.auth_helper)
        the user explicitly provided for this URL -- pass through to any
        HTTP fetch this strategy makes, for sites that require a login
        session to show real content instead of a login/paywall page.

        html: the page's HTML, if the engine already fetched it for a
        sibling strategy -- use this instead of fetching it again when
        given. Every page-inspecting strategy would otherwise each fetch
        the same 'generic'-classified URL independently (confirmed
        directly: 3 separate fetches added ~27s total against a real
        moderately-slow authenticated page). Strategies that don't apply
        to plain HTML pages (e.g. yt-dlp-based ones) can ignore this.
        """
        raise NotImplementedError
