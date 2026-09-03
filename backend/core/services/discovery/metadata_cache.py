import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("AIPipeline")

_LOCK = threading.Lock()
_CACHE: Dict[str, tuple] = {}  # url -> (expires_at, info_json_path)

# Long enough to cover "discovery -> user reads the series-choice UI ->
# transcription starts" for a normal user, short enough that a cached info
# dump (which can embed time-limited stream URLs) is very unlikely to have
# gone stale by the time it's used.
TTL_SECONDS = 600


def store(url: str, info: Dict[str, Any]) -> None:
    """
    Caches a yt-dlp single-video full-info dict (as produced by
    YtdlpPlaylistStrategy when a URL turns out not to be a playlist) so the
    extraction step can reuse it via `yt-dlp --load-info-json` instead of
    re-resolving the exact same URL from scratch.
    """
    try:
        fd, path = tempfile.mkstemp(suffix=".json", prefix="ytdlp_info_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
    except Exception as e:
        logger.warning(f"Could not cache discovery metadata for '{url}': {e}")
        return

    with _LOCK:
        _evict_expired_locked()
        old = _CACHE.get(url)
        _CACHE[url] = (time.time() + TTL_SECONDS, path)
    if old:
        _remove_file(old[1])


def take(url: str) -> Optional[str]:
    """
    Returns and removes the cached info-json file path for url, if present
    and not expired. One-shot: once taken, it's gone, so a second concurrent
    extraction attempt for the same URL falls back to a normal fresh call
    rather than racing on the same temp file.
    """
    with _LOCK:
        entry = _CACHE.pop(url, None)
    if not entry:
        return None
    expires_at, path = entry
    if time.time() > expires_at or not os.path.exists(path):
        _remove_file(path)
        return None
    return path


def discard(path: str) -> None:
    """Call after the caller is done with a path returned by take()."""
    _remove_file(path)


def _evict_expired_locked() -> None:
    now = time.time()
    expired = [u for u, (expires_at, _) in _CACHE.items() if expires_at < now]
    for u in expired:
        _, path = _CACHE.pop(u)
        _remove_file(path)


def _remove_file(path: str) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
