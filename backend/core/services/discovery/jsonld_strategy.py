import json
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

_JSONLD_BLOCK = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


class JsonLdStrategy(DiscoveryStrategy):
    """
    Strategy: schema.org structured data (JSON-LD). Many course/lesson/
    learning platforms describe their own content structure this way --
    ItemList (an ordered list of lessons/episodes) or Course.hasPart
    (syllabus sections) -- which is far more reliable than guessing from
    link text when it's present, and works regardless of the site's visual
    design or navigation wording.
    """

    name = "jsonld"

    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if MediaDiscovery.classify(url) != "generic":
            # known_platform: yt-dlp's own extractor is authoritative.
            # direct_file/stream_manifest/feed: raw media or XML, not an
            # HTML page -- fetching and regex-scanning it here wastes a
            # request at best, and at worst risks catastrophic regex
            # backtracking when binary bytes get decoded as "text".
            return None

        html = html or fetch_page(url, extra_headers=extra_headers)
        if not html:
            return None

        items: List[Dict[str, str]] = []
        for match in _JSONLD_BLOCK.finditer(html):
            try:
                data = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
            for obj in (data if isinstance(data, list) else [data]):
                items.extend(self._extract_items(obj, url))

        seen = set()
        deduped = []
        for item in items:
            if item["url"] not in seen and item["url"] != url:
                seen.add(item["url"])
                deduped.append(item)

        if len(deduped) <= 1:
            return None
        return {"items": deduped, "current_index": None}

    @staticmethod
    def _extract_items(obj: Any, base_url: str) -> List[Dict[str, str]]:
        if not isinstance(obj, dict):
            return []
        results: List[Dict[str, str]] = []

        # ItemList: {"@type": "ItemList", "itemListElement": [{"item": {"url":..., "name":...}}, ...]}
        list_elements = obj.get("itemListElement")
        if isinstance(list_elements, list):
            for element in list_elements:
                if not isinstance(element, dict):
                    continue
                item = element.get("item") if isinstance(element.get("item"), dict) else element
                item_url = item.get("url") or element.get("url")
                name = item.get("name") or element.get("name")
                if item_url:
                    results.append({"url": urljoin(base_url, item_url), "title": name or item_url})

        # Course.hasPart / PodcastSeries.hasPart: an ordered syllabus/episode list
        has_part = obj.get("hasPart")
        if isinstance(has_part, list):
            for part in has_part:
                if isinstance(part, dict) and part.get("url"):
                    results.append({"url": urljoin(base_url, part["url"]), "title": part.get("name", part["url"])})

        return results
