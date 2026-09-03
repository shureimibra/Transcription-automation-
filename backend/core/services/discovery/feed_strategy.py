import logging
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

from .base import DiscoveryStrategy

try:
    from backend.core.services.ai_pipeline import MediaDiscovery, DEFAULT_USER_AGENT
except ModuleNotFoundError:
    from core.services.ai_pipeline import MediaDiscovery, DEFAULT_USER_AGENT

logger = logging.getLogger("AIPipeline")

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class FeedStrategy(DiscoveryStrategy):
    """
    Strategy: full podcast/RSS episode list. Fixes a real gap from the
    Phase 2 media-discovery layer, where a feed URL only ever resolved to
    its single latest episode (MediaDiscovery.parse_rss_enclosure) -- fine
    for "transcribe the newest episode", but it meant a podcast feed never
    surfaced as a multi-episode collection the way "Podcasts" is explicitly
    meant to here. This reads every <item>/<entry> in the feed instead.

    Episodes are sorted oldest-first when publish dates are available, to
    match "Episode 1 before Episode 2" sequential semantics -- feeds
    themselves are conventionally newest-first, which is right for
    browsing but wrong for "process this series in order".
    """

    name = "feed_episodes"

    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        # html (the engine's shared page fetch) never applies here -- this
        # only ever runs for classify(url) == 'feed' (XML, not HTML), and
        # parses it via its own dedicated urllib + ElementTree fetch below.
        if MediaDiscovery.classify(url) != "feed":
            return None

        try:
            headers = {"User-Agent": DEFAULT_USER_AGENT}
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            root = ET.fromstring(data)
        except Exception as e:
            logger.warning(f"Feed discovery failed for '{url}': {e}")
            return None

        episodes = self._rss_episodes(root) or self._atom_episodes(root)
        if len(episodes) <= 1:
            return None

        if all(pubdate is not None for pubdate, _, _ in episodes):
            episodes.sort(key=lambda e: e[0])

        items = [{"url": ep_url, "title": title} for _, ep_url, title in episodes]
        return {"items": items, "current_index": None}

    @staticmethod
    def _rss_episodes(root):
        episodes = []
        for item in root.findall(".//item"):
            enclosure = item.find("enclosure")
            if enclosure is None or not enclosure.get("url"):
                continue
            title_el = item.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else enclosure.get("url")
            pubdate = None
            pubdate_el = item.find("pubDate")
            if pubdate_el is not None and pubdate_el.text:
                try:
                    pubdate = parsedate_to_datetime(pubdate_el.text)
                except Exception:
                    pubdate = None
            episodes.append((pubdate, enclosure.get("url"), title))
        return episodes

    @staticmethod
    def _atom_episodes(root):
        episodes = []
        for entry in root.findall(f".//{_ATOM_NS}entry"):
            for link in entry.findall(f"{_ATOM_NS}link"):
                if link.get("rel") == "enclosure" and link.get("href"):
                    title_el = entry.find(f"{_ATOM_NS}title")
                    title = title_el.text.strip() if title_el is not None and title_el.text else link.get("href")
                    episodes.append((None, link.get("href"), title))
                    break
        return episodes
