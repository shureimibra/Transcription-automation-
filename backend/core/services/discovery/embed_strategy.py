import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from .base import DiscoveryStrategy
from .utils import fetch_page

try:
    from backend.core.services.ai_pipeline import MediaDiscovery
except ModuleNotFoundError:
    from core.services.ai_pipeline import MediaDiscovery

logger = logging.getLogger("AIPipeline")

# iframe src patterns from embed players named in the spec: YouTube, Vimeo,
# Cloudflare Stream, JWPlayer, Brightcove, Kaltura. Framework-agnostic --
# matches regardless of what CMS/JS framework wraps the iframe.
_EMBED_HOST_PATTERN = re.compile(
    r'<iframe[^>]+src=["\']((?:https?:)?//[^"\']*(?:'
    r'youtube(?:-nocookie)?\.com/embed|player\.vimeo\.com/video|'
    r'iframe\.videodelivery\.net|cdn\.jwplayer\.com|players\.brightcove\.net|'
    r'(?:cdnapisec|www)\.kaltura\.com/(?:p|embedPlaykitJs)'
    r')[^"\']*)["\']',
    re.IGNORECASE,
)

# Plain HTML5 <video>/<audio> tags with a direct src -- catches the simplest
# case where a page just embeds several native players with no JS SDK at all.
_HTML5_MEDIA_PATTERN = re.compile(
    r'<(?:video|audio)[^>]+src=["\']([^"\']+\.(?:mp4|webm|m3u8|mp3|wav|m4a|ogg)[^"\']*)["\']',
    re.IGNORECASE,
)


class EmbedStrategy(DiscoveryStrategy):
    """
    Strategy: multiple embedded media players on one page. A lesson-index
    or "watch all episodes here" page often embeds several distinct
    YouTube/Vimeo/Cloudflare-Stream/JWPlayer/Brightcove/Kaltura iframes (or
    plain HTML5 <video>/<audio> tags) rather than linking out to separate
    pages -- each embed is one series item.

    This does not attempt JavaScript-rendered embeds (players injected
    after page load via client-side JS): that requires a headless browser,
    which is a deliberately out-of-scope dependency for now. Pages like
    that simply find nothing here and fall through to the next strategy,
    same as any other unsupported site.
    """

    name = "embedded_media"

    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if MediaDiscovery.classify(url) != "generic":
            # known_platform: yt-dlp owns these. direct_file/stream_manifest/
            # feed: raw media or XML, not an HTML page to scan for embeds --
            # skip rather than waste a request (or worse, regex-scan a
            # binary file misdecoded as text).
            return None

        html = html or fetch_page(url, extra_headers=extra_headers)
        if not html:
            return None

        sources = _EMBED_HOST_PATTERN.findall(html) + _HTML5_MEDIA_PATTERN.findall(html)

        seen = set()
        unique_urls = []
        for src in sources:
            absolute = urljoin(url, src)
            if absolute not in seen:
                seen.add(absolute)
                unique_urls.append(absolute)

        if len(unique_urls) <= 1:
            return None

        items = [{"url": u, "title": f"Embedded media {i + 1}"} for i, u in enumerate(unique_urls)]
        return {"items": items, "current_index": None}
