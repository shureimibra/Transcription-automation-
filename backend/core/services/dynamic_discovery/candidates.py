"""
Candidate media requests: representation, filtering, and ranking.

Every function here is pure. The browser-driving code in resolver.py collects
raw observations and hands them to `pick_best`, which means the actual
decision logic -- including the blob-correlation rule that is the whole point
of this feature -- is unit-testable with no Playwright installed and no
network access.
"""
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

# Request headers worth carrying to the downstream downloader. Anything else
# a browser sends (sec-ch-ua, accept-encoding, priority, ...) is noise that
# can only confuse yt-dlp or the plain HTTP path.
#
# Cookie and Authorization are included because the media URL is frequently
# session-gated -- that is precisely the case this feature exists to serve.
# They are carried, never logged: see resolver's logging, which prints the
# URL and content type only.
FORWARDABLE_HEADERS = ('referer', 'cookie', 'authorization', 'origin', 'user-agent')

_AUDIO_PREFIX = 'audio/'
_VIDEO_PREFIX = 'video/'
_OCTET_TYPES = ('application/octet-stream', 'binary/octet-stream')

# Playlist/manifest content types. Worth returning -- the existing pipeline
# handles HLS/DASH natively via FFmpeg (MediaDiscovery.classify ->
# 'stream_manifest') -- but ranked below a plain media response, since a
# manifest needs another round of fetching to become audio.
_MANIFEST_TYPES = (
    'application/vnd.apple.mpegurl', 'application/x-mpegurl',
    'application/dash+xml', 'audio/mpegurl',
)

# Hosts whose requests are aborted outright. Not a privacy feature -- these
# are simply requests that cannot be the media and cost latency. Matched as
# a domain suffix.
BLOCKED_HOST_SUFFIXES = (
    'google-analytics.com', 'googletagmanager.com', 'doubleclick.net',
    'facebook.net', 'facebook.com', 'hotjar.com', 'segment.io', 'segment.com',
    'sentry.io', 'newrelic.com', 'nr-data.net', 'mixpanel.com', 'intercom.io',
    'fullstory.com', 'clarity.ms', 'amplitude.com', 'branch.io',
    'googlesyndication.com', 'adservice.google.com', 'scorecardresearch.com',
)

# Resource types aborted before a byte is fetched. 'media' is deliberately
# NOT here -- a media request is the thing we are hunting, and it gets its
# own capture-then-abort treatment in the resolver.
# Do not abort stylesheets.  Vite uses dynamically imported CSS as a module
# preload dependency; aborting it rejects the corresponding JS import and can
# leave an otherwise valid SPA permanently on its loading screen.
BLOCKED_RESOURCE_TYPES = ('image', 'font', 'imageset')

_HOSTNAME_SPLIT = re.compile(r'^\[?([^\]]*)\]?$')


def is_blob_url(url: str) -> bool:
    """
    blob: URLs exist only inside the browser's memory. Handing one to
    Django/FFmpeg/yt-dlp cannot possibly work, so one must never escape this
    package as a result -- it is a signal that media was found, not a source
    that can be fetched.
    """
    return bool(url) and url.strip().lower().startswith('blob:')


def is_fetchable_url(url: str) -> bool:
    """True only for http/https URLs the downstream pipeline could retrieve."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def is_blocked_host(url: str, allow_private: bool = False) -> bool:
    """
    Analytics/ad hosts (latency, never media), plus a conservative block on
    private and loopback address literals.

    The private-address block is a partial SSRF mitigation, not a complete
    one: this stage executes untrusted third-party JavaScript, and page
    script can ask the browser for any URL it likes. Literal private
    addresses are cheap to refuse here. A hostname that RESOLVES to a
    private address (DNS rebinding) is NOT caught -- doing that properly
    needs resolution inside the route handler on every request, which is
    both slow and racy. Documented as a known limitation.

    allow_private lifts only the address-literal part, for a deployment whose
    media genuinely lives on an internal host (an intranet Laravel app, a
    LAN media server). Analytics suffixes stay blocked either way. Off by
    default, because the safe posture is the one you get without reading
    this docstring.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ''
    except Exception:
        return False

    host = host.lower()
    if not host:
        return False

    for suffix in BLOCKED_HOST_SUFFIXES:
        if host == suffix or host.endswith('.' + suffix):
            return True

    if allow_private:
        return False

    if host in ('localhost', 'localhost.localdomain') or host.endswith('.localhost'):
        return True

    match = _HOSTNAME_SPLIT.match(host)
    literal = match.group(1) if match else host
    try:
        address = ipaddress.ip_address(literal)
    except ValueError:
        return False
    return (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def normalize_content_type(content_type: Optional[str]) -> str:
    """'audio/mpeg; charset=utf-8' -> 'audio/mpeg'."""
    if not content_type:
        return ''
    return content_type.split(';')[0].strip().lower()


def classify_content_type(content_type: Optional[str]) -> str:
    """
    One of 'audio', 'video', 'manifest', 'octet', or '' (not media).
    """
    normalized = normalize_content_type(content_type)
    if not normalized:
        return ''
    if normalized in _MANIFEST_TYPES:
        return 'manifest'
    if normalized.startswith(_AUDIO_PREFIX):
        return 'audio'
    if normalized.startswith(_VIDEO_PREFIX):
        return 'video'
    if normalized in _OCTET_TYPES:
        return 'octet'
    return ''


def filter_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
    Reduces a browser request's headers to the forwardable subset, with
    canonical capitalization for the downstream urllib/yt-dlp calls (both
    accept any casing; consistent output just makes tests and logs legible).
    """
    if not headers:
        return {}
    canonical = {
        'referer': 'Referer', 'cookie': 'Cookie', 'authorization': 'Authorization',
        'origin': 'Origin', 'user-agent': 'User-Agent',
    }
    result = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered in FORWARDABLE_HEADERS and value:
            result[canonical[lowered]] = value
    return result


@dataclass
class ResolvedMedia:
    """
    A fetchable media source discovered by executing the page's JavaScript.

    `url` is guaranteed http(s) -- never blob:, never data:. Construction
    through `make_candidate` enforces that; nothing else in the package
    builds one.
    """
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    content_type: str = ''
    byte_size: Optional[int] = None
    via: str = ''

    def describe(self) -> str:
        """Log-safe summary: no header values, so no cookies or tokens."""
        size = f'{self.byte_size}B' if self.byte_size else 'size-unknown'
        return f'{self.url} ({self.content_type or "type-unknown"}, {size}, via={self.via})'


def make_candidate(url: str, headers: Optional[Dict[str, str]] = None,
                   content_type: Optional[str] = None,
                   byte_size: Optional[int] = None,
                   via: str = '') -> Optional[ResolvedMedia]:
    """
    Builds a candidate, or returns None if the URL could never be fetched by
    the existing pipeline. This is the single chokepoint that keeps blob:
    URLs from escaping the package.
    """
    if not is_fetchable_url(url) or is_blob_url(url):
        return None
    return ResolvedMedia(
        url=url,
        headers=filter_headers(headers),
        content_type=normalize_content_type(content_type),
        byte_size=byte_size,
        via=via,
    )


# Higher wins. A response the server explicitly typed as audio is the most
# trustworthy thing we can find; an octet-stream that merely happens to be
# large is the least. A manifest sits between: certainly media, but it needs
# another fetch before it is audio.
_KIND_RANK = {'audio': 400, 'video': 300, 'manifest': 200, 'octet': 100, '': 0}


def score(candidate: ResolvedMedia, min_octet_bytes: int = 0) -> int:
    """
    Ranking score, or a negative number for candidates that must be
    discarded entirely.
    """
    kind = classify_content_type(candidate.content_type)

    # An untyped candidate is still usable when it came from a real <audio>/
    # <video> element request -- the browser told us what it was for, even if
    # the server didn't say what it was. That is the Pattern A/B/C case.
    if not kind:
        if candidate.via.startswith('request:media') or candidate.via.startswith('dom:'):
            kind = 'audio'
        else:
            return -1

    if kind == 'octet':
        if candidate.byte_size is not None and candidate.byte_size < min_octet_bytes:
            return -1
        if candidate.byte_size is None:
            # An octet-stream with no declared length is too weak to act on:
            # it is as likely to be a wasm chunk or a protobuf config as
            # audio, and guessing wrong sends FFmpeg garbage.
            return -1

    points = _KIND_RANK[kind]

    # Prefer a candidate we caught at request time and aborted: zero bytes
    # were transferred to get it, which is exactly the outcome the
    # performance requirements ask for.
    if candidate.via.startswith('request:media'):
        points += 40

    # Among equals, the largest declared body is most likely the full
    # recording rather than a preview clip or a waveform peaks file.
    if candidate.byte_size:
        points += min(candidate.byte_size // 100_000, 50)

    return points


def pick_best(candidates: List[ResolvedMedia], min_octet_bytes: int = 0) -> Optional[ResolvedMedia]:
    """
    Highest-scoring viable candidate, or None. Ties resolve to the earliest
    observation, which is the one the player actually reached for first.
    """
    best = None
    best_score = 0
    for candidate in candidates:
        candidate_score = score(candidate, min_octet_bytes=min_octet_bytes)
        if candidate_score > best_score:
            best, best_score = candidate, candidate_score
    return best
