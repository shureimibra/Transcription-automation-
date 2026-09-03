import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .base import DiscoveryStrategy
from . import metadata_cache

try:
    from backend.core.services.ai_pipeline import MediaDiscovery, DEFAULT_USER_AGENT
    from backend.core.services.profiling import profile_stage
except ModuleNotFoundError:
    from core.services.ai_pipeline import MediaDiscovery, DEFAULT_USER_AGENT
    from core.services.profiling import profile_stage

logger = logging.getLogger("AIPipeline")

MAX_PLAYLIST_ITEMS = 100  # safety cap on enumerated playlist size


class YtdlpPlaylistStrategy(DiscoveryStrategy):
    """
    Strategy 1: direct playlist detection for any platform yt-dlp already
    understands (YouTube playlists/channels, Vimeo showcases, SoundCloud
    sets, and hundreds more via yt-dlp's own extractor library). Uses
    --flat-playlist so this never downloads any media, only metadata.
    """

    name = "ytdlp_playlist"

    def discover(self, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 html: Optional[str] = None) -> Optional[Dict[str, Any]]:
        # html (the engine's shared page fetch) never applies here -- this
        # only ever runs for classify(url) == 'known_platform', where
        # yt-dlp's own extractor is used instead of scanning HTML.
        if MediaDiscovery.classify(url) != "known_platform":
            return None  # trust the other strategies for anything yt-dlp doesn't own

        command = [
            sys.executable, "-m", "yt_dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(MAX_PLAYLIST_ITEMS),
            "--user-agent", DEFAULT_USER_AGENT,
            # This check is explicitly best-effort: the caller already treats
            # any failure here as "not a series" and falls through to the
            # normal single-URL flow. yt-dlp's own default is 10 retries,
            # which on a rate-limited or flaky connection can burn 30+
            # seconds to reach the exact same graceful fallback 2 retries
            # would reach in a few seconds -- so cap it low here specifically.
            "--retries", "2",
            "--socket-timeout", "10",
        ]
        command.extend(MediaDiscovery.ytdlp_auth_args())
        temp_cookies_path = None
        for header_name, header_value in (extra_headers or {}).items():
            if header_name.lower() == "cookie":
                # --add-header "Cookie:..." is unreliable for yt-dlp (it
                # warns about this itself); --cookies <file> is its own
                # recommended mechanism -- see MediaDiscovery.write_temp_cookies_file.
                domain = urlparse(url).hostname or ""
                temp_cookies_path = MediaDiscovery.write_temp_cookies_file(header_value, domain)
                command.extend(["--cookies", temp_cookies_path])
            else:
                command.extend(["--add-header", f"{header_name}:{header_value}"])
        command.append(url)

        try:
            with profile_stage("ytdlp_playlist_discovery", url=url):
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except Exception as e:
            logger.warning(f"yt-dlp playlist discovery failed for '{url}': {e}")
            return None
        finally:
            if temp_cookies_path and os.path.exists(temp_cookies_path):
                os.remove(temp_cookies_path)

        entries = []
        for line in result.stdout.decode(errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if len(entries) <= 1:
            if not entries:
                stderr_tail = result.stderr.decode(errors="ignore").strip().splitlines()[-3:]
                logger.info(f"yt-dlp reported no playlist entries for '{url}': {stderr_tail}")
            elif entries[0].get("_type") not in ("url", "url_transparent"):
                # --flat-playlist only flattens entries encountered *inside*
                # an actual playlist (see yt-dlp's extract_flat='in_playlist'
                # semantics); a bare single-video URL is extracted in full
                # regardless, so this one entry already has real format
                # info. Cache it so the extraction step can reuse it via
                # --load-info-json instead of re-resolving this same URL.
                metadata_cache.store(url, entries[0])
            return None

        items = [
            {"url": e.get("url") or e.get("webpage_url") or e.get("original_url") or "",
             "title": e.get("title") or f"Item {i + 1}"}
            for i, e in enumerate(entries)
        ]
        items = [item for item in items if item["url"]]
        if len(items) <= 1:
            return None

        current_index = next((i for i, item in enumerate(items) if item["url"] == url), None)
        return {"items": items, "current_index": current_index}
