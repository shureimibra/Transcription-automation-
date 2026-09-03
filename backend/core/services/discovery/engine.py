import logging
from typing import Any, Dict, Optional

from .ytdlp_strategy import YtdlpPlaylistStrategy
from .feed_strategy import FeedStrategy
from .jsonld_strategy import JsonLdStrategy
from .embed_strategy import EmbedStrategy
from .nav_link_strategy import NavLinkStrategy
from .sequential_param_strategy import SequentialQueryParamStrategy
from .utils import fetch_page

try:
    from backend.core.services.ai_pipeline import MediaDiscovery
except ModuleNotFoundError:
    from core.services.ai_pipeline import MediaDiscovery

logger = logging.getLogger("AIPipeline")

# Tried in order, cheapest/most authoritative first. The first strategy that
# finds 2+ items wins. Adding support for a new kind of site/platform means
# writing one new DiscoveryStrategy subclass and adding it to this list --
# nothing else in the application needs to change.
STRATEGIES = [
    YtdlpPlaylistStrategy(),
    FeedStrategy(),
    JsonLdStrategy(),
    EmbedStrategy(),
    NavLinkStrategy(),
]


def discover(url: str, extra_headers: Optional[Dict[str, str]] = None,
             try_sequential: bool = False) -> Dict[str, Any]:
    """
    Returns:
      {
        'is_series': bool,
        'method': str,  # which strategy found it, or 'none'
        'items': [{'url': str, 'title': str}, ...],  # ordered
        'current_index': int | None,
      }

    Never assumes a site supports this: if no strategy finds a multi-item
    collection, returns is_series=False and the caller proceeds with
    exactly the single-URL workflow that already exists -- no errors, no
    extra complexity surfaced to the user.

    extra_headers: optional per-job auth (see core.services.auth_helper)
    the user explicitly pasted for this URL, forwarded to every strategy's
    own HTTP fetches -- for pages that require a login session to show
    real content instead of a login/paywall page.

    try_sequential: explicit opt-in for SequentialQueryParamStrategy (a
    numbered-sequence query parameter like ?seq_num=0). Not in the default
    STRATEGIES list and not tried automatically, because walking this
    pattern can have real side effects on the source site (confirmed:
    speech.intron.health's practice-clip queue advances server-side state
    per fetch) -- auto-probing every numeric query parameter on every
    generic URL by default would silently consume state on sites where
    that's never wanted.
    """
    strategies = list(STRATEGIES)
    if try_sequential:
        strategies.append(SequentialQueryParamStrategy())

    # JsonLdStrategy, EmbedStrategy, and NavLinkStrategy all inspect plain
    # HTML and all gate on classify(url) == 'generic' -- fetch that page
    # exactly once here and hand it to each of them, instead of three
    # independent fetches of the same URL (confirmed directly: this added
    # ~27s total against a real moderately-slow authenticated page). A
    # fetch failure here just means html stays None and each strategy
    # falls back to fetching it itself, so this is a pure optimization,
    # never a new failure mode.
    shared_html = None
    if MediaDiscovery.classify(url) == "generic":
        shared_html = fetch_page(url, extra_headers=extra_headers)

    for strategy in strategies:
        try:
            result = strategy.discover(url, extra_headers=extra_headers, html=shared_html)
        except Exception as e:
            logger.warning(f"Discovery strategy '{strategy.name}' raised an error for '{url}': {e}")
            continue

        if result and len(result.get("items", [])) > 1:
            logger.info(f"Series discovery via '{strategy.name}': {len(result['items'])} item(s) for '{url}'")
            return {
                "is_series": True,
                "method": strategy.name,
                "items": result["items"],
                "current_index": result.get("current_index"),
            }

    return {"is_series": False, "method": "none", "items": [], "current_index": None}
