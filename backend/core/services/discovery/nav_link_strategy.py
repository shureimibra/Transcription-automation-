import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from .base import DiscoveryStrategy
from .utils import fetch_page

try:
    from backend.core.services.ai_pipeline import MediaDiscovery
except ModuleNotFoundError:
    from core.services.ai_pipeline import MediaDiscovery

logger = logging.getLogger("AIPipeline")

# Link text/href hints that suggest a page is part of a series. Matched
# case-insensitively against anchor text -- deliberately broad, since a
# false positive here only adds a candidate the user can decline, while a
# false negative silently loses a real series (the worse failure mode).
NAV_KEYWORDS = re.compile(
    r'\b(next(?:\s+(?:episode|part|lesson|module|lecture|chapter|audio))?|continue(?:\s+(?:learning|series))?|'
    r'play\s+next|episode\s*\d+|part\s*\d+|lesson\s*\d+|module\s*\d+|'
    r'chapter\s*\d+|playlist|series|full\s+course)\b',
    re.IGNORECASE,
)

MAX_CHAIN_LENGTH = 25  # safety cap when following a "next"-style chain


class NavLinkStrategy(DiscoveryStrategy):
    """
    Strategy: sequential navigation link detection (Next, Continue,
    Episode N, Part N, Lesson N, Module N, Chapter N, ...). The broadest,
    least precise strategy -- deliberately tried last, after the other
    strategies had a chance to find something more structured.
    """

    name = "nav_links"

    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if MediaDiscovery.classify(url) != "generic":
            # known_platform: YtdlpPlaylistStrategy already gave an
            # authoritative answer for these; scraping a heavy JS-rendered
            # platform page for loosely-matching link text is both wasteful
            # and false-positive-prone. direct_file/stream_manifest/feed:
            # raw media or XML, not an HTML page -- running an anchor-tag
            # regex against a binary file misdecoded as "text" can trigger
            # catastrophic regex backtracking and hang for minutes.
            return None

        # Only the first page can reuse the engine's shared fetch -- any
        # subsequent page in a "next"-style chain is discovered from links
        # on THIS page, so it can't be known ahead of time.
        html = html or fetch_page(url, extra_headers=extra_headers)
        if not html:
            return None

        nav_links = self._find_nav_links(html, url)
        if not nav_links:
            return None

        # Prefer a single clear "next"-style link and follow the chain
        # forward; if the page instead lists many numbered items (a
        # course/lesson index), treat all of them as the series in order.
        next_like = [l for l in nav_links if re.search(r'^\s*(next|continue|play\s+next)\b', l["title"], re.IGNORECASE)]

        items = [{"url": url, "title": "Current page"}]
        if next_like and len(nav_links) <= 2:
            visited = {url}
            while next_like and len(items) < MAX_CHAIN_LENGTH:
                candidate = next_like[0]["url"]
                if candidate in visited:
                    break
                items.append({"url": candidate, "title": next_like[0]["title"]})
                visited.add(candidate)
                page_html = fetch_page(candidate, extra_headers=extra_headers)
                if not page_html:
                    break
                next_like = [l for l in self._find_nav_links(page_html, candidate)
                             if re.search(r'^\s*(next|continue|play\s+next)\b', l["title"], re.IGNORECASE)]
        else:
            items.extend(nav_links[:MAX_CHAIN_LENGTH - 1])

        if len(items) <= 1:
            return None
        return {"items": items, "current_index": 0}

    @staticmethod
    def _find_nav_links(html: str, base_url: str) -> List[Dict[str, str]]:
        """Finds anchor tags whose visible text matches a series/navigation
        keyword, in document order, deduplicated by URL."""
        links: List[Dict[str, str]] = []
        seen = set()
        for match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
            href, inner_html = match.group(1), match.group(2)
            text = re.sub(r'<[^>]+>', ' ', inner_html)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text or not NAV_KEYWORDS.search(text):
                continue
            absolute_url = urljoin(base_url, href)
            if absolute_url in seen or absolute_url == base_url:
                continue
            seen.add(absolute_url)
            links.append({"url": absolute_url, "title": text[:120]})
        return links
