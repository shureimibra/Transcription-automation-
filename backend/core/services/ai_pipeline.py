import os
import sys
import re
import shutil
import subprocess
import logging
import tempfile
import time
import importlib.util
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin
from typing import Callable, Dict, Any, Generator, List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AIPipeline")


def _int_env(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name)
        return int(raw) if raw not in (None, '') else default
    except (TypeError, ValueError):
        return default


def _str_env(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw not in (None, '') else default


class JobCancelledError(Exception):
    """Raised when a cancel_check callback reports cancellation mid-pipeline.
    Callers (tasks.py) catch this specifically to mark the job 'cancelled'
    rather than 'failed'."""
    pass


class LoginRequiredError(Exception):
    """
    Raised (instead of the usual bare `return False`) when a download
    attempt was detectably redirected to a login/sign-in page -- e.g.
    yt-dlp's generic extractor reports "Unsupported URL" for a URL whose
    path is obviously a login page, not the media page that was actually
    requested. Confirmed live: transcriptioncertificationinstitute.org's
    practice-test page redirects unauthenticated requests to
    `/account/login?ReturnUrl=...`, which yt-dlp (correctly) can't extract
    anything from -- but the resulting job.error_message was just the
    generic "Audio extraction or download failed.", giving no hint that
    pasting site auth (see core.services.auth_helper, already supported
    end-to-end by the frontend's "Authentication helper" and
    site_auth_headers) would actually fix it. tasks.py catches this
    specifically to set a clear, actionable message instead. Deliberately
    NOT raised for every download failure -- only this one specific,
    confidently-detected signal; every other failure keeps returning
    False/logging normally, unchanged.
    """
    pass


_LOGIN_PATH_PATTERN = re.compile(r'/(?:account/)?(?:log[-_]?in|sign[-_]?in|auth)\b', re.IGNORECASE)


def _looks_like_login_redirect(requested_url: str, stderr: str) -> bool:
    """
    True if yt-dlp's "Unsupported URL: <X>" failure names a URL that (a)
    isn't the one actually requested and (b) has a path that plainly
    reads as a login/sign-in page -- the specific, narrow signal that a
    site sent an unauthenticated request through a login wall instead of
    to the real content. Deliberately conservative: a generic "Unsupported
    URL" failure for the ORIGINAL url (a page that's just genuinely
    unrecognized) does not match this, only a redirect to something new.
    """
    match = re.search(r'Unsupported URL:\s*(\S+)', stderr)
    if not match:
        return False
    failed_url = match.group(1)
    if failed_url.rstrip('/') == requested_url.rstrip('/'):
        return False
    return bool(_LOGIN_PATH_PATTERN.search(urlparse(failed_url).path))

try:
    from backend.core.services.profiling import profile_stage
except ModuleNotFoundError:
    from core.services.profiling import profile_stage

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# Wall-clock cap on the WHOLE direct-download transfer, not just each
# individual socket read. urllib's own `timeout=` only guards a single read
# call -- an origin that trickles bytes slowly enough to never go fully
# silent for that long (confirmed live: nch.com.au) can otherwise stall for
# minutes before urllib's per-read timeout ever trips, long after yt-dlp
# (which tolerates slow origins far better -- see the fallback right below
# this) should have already been given a chance. Deliberately short: plain
# urllib is the LAST resort before yt-dlp (impersonation, below, is tried
# first) -- if it hasn't made real progress in 45s, urllib is very unlikely
# to do meaningfully better, so failing fast here means yt-dlp gets started
# sooner rather than later.
DIRECT_DOWNLOAD_DEADLINE_SECONDS = 45

# Separate, longer deadline for the curl_cffi-impersonated direct download
# (see AudioExtractor._download_direct_file_impersonated) -- this is now
# the PREFERRED, typically-fastest path for a slow-but-not-tarpitted host,
# not a last resort, so it shouldn't be held to the same "fail fast, we're
# doomed anyway" threshold as plain urllib above. Confirmed live: nch.com.au
# sustains ~41.5 KB/s to an impersonated client, meaning even a modest
# ~5MB practice-audio file needs ~120s to complete -- a 45s cap would
# abandon a download that was genuinely on track to succeed (and be the
# fastest available option, well ahead of yt-dlp's ~390s for the same file).
IMPERSONATED_DOWNLOAD_DEADLINE_SECONDS = 150

# Same wall-clock-deadline problem, same fix, applied to fetching an HTML
# PAGE rather than a media file (see read_response_body_with_deadline
# below) -- confirmed live: a single embedded-scrape page fetch took 65s
# despite timeout=30, and a discovery page fetch contributed to a 232s
# total discovery time the same way. Deliberately much shorter than
# DIRECT_DOWNLOAD_DEADLINE_SECONDS above: HTML pages are a few hundred KB
# at most, nothing like a media file, so there's no legitimate reason a
# real page needs anywhere near as long.
PAGE_FETCH_DEADLINE_SECONDS = 25


def read_response_body_with_deadline(response, deadline_seconds: float = PAGE_FETCH_DEADLINE_SECONDS,
                                      cancel_check: Optional[Callable[[], bool]] = None) -> bytes:
    """
    Reads an entire response body via chunked reads, enforcing a real
    WALL-CLOCK deadline on the whole transfer -- not just urlopen's own
    `timeout=`, which only bounds each individual read and is blind to a
    connection that stays alive but trickles data slowly enough that no
    single read ever goes silent for that long. Accumulates the whole body
    in memory, so this is for small responses (HTML pages) only -- see
    AudioExtractor._download_direct_file for the disk-streaming equivalent
    used for media files, which never holds the whole download in memory.
    """
    chunks = []
    started_at = time.monotonic()
    while True:
        if time.monotonic() - started_at > deadline_seconds:
            raise TimeoutError(
                f"Page fetch exceeded the {deadline_seconds}s wall-clock deadline "
                "(origin is alive but responding too slowly)."
            )
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled during page fetch.")
        chunk = response.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b''.join(chunks)


def fetch_page_impersonated(url: str, headers: Dict[str, str], timeout: float = PAGE_FETCH_DEADLINE_SECONDS,
                             session: Optional[Any] = None) -> Optional[bytes]:
    """
    Fetches a URL using curl_cffi's browser TLS/HTTP2 fingerprint
    impersonation (see MediaDiscovery.has_impersonation()) instead of
    plain urllib -- returns None (never raises) when curl_cffi isn't
    installed or the request fails, so every caller can treat this as an
    optional first attempt and fall through to its existing plain-urllib
    path unchanged.

    This is not a theoretical hardening -- it fixes a real, confirmed
    failure: gotranscript.com's practice-transcription page (used
    throughout this project's own testing) started silently tarpitting
    plain urllib/http.client requests -- the connection stays open but
    data trickles too slowly to ever complete, so even the wall-clock
    deadline above just fails slightly faster instead of actually working
    -- while a real browser loads the exact same URL instantly. The
    difference is the TLS/HTTP2 handshake itself, which a spoofed
    User-Agent HEADER does nothing for. Verified directly: a plain
    urllib fetch of that URL hung past a 25s deadline; curl_cffi with
    impersonate='chrome' completed in 9s with a full 299KB page. yt-dlp's
    OWN generic extractor already gets this treatment (see
    `--extractor-args generic:impersonate` below) -- this extends the
    exact same fix to the plain page-fetch paths (fetch_page,
    _try_embedded_scrape), which never had it.

    session: optional curl_cffi.requests.Session -- pass the SAME session
    across multiple calls to persist cookies between them (each call
    otherwise starts a brand-new, cookie-less request, which breaks any
    site whose content depends on a session cookie set by an earlier
    response). Verified: curl_cffi's Session does retain Set-Cookie
    values across .get() calls. Defaults to a one-off stateless request
    when omitted, unchanged from before this parameter existed.
    """
    if not MediaDiscovery.has_impersonation():
        return None
    try:
        from curl_cffi import requests as curl_requests
        requester = session if session is not None else curl_requests
        response = requester.get(url, headers=headers, impersonate='chrome', timeout=timeout)
        response.raise_for_status()
        return response.content
    except Exception as e:
        logger.warning(f"curl_cffi impersonated fetch failed for '{url}' ({e}); falling back to plain urllib.")
        return None


def setup_subprocess_paths():
    extra_paths = []
    
    # 1. Add active virtual environment Scripts directory to path (contains yt-dlp.exe)
    venv_bin = os.path.dirname(sys.executable)
    if venv_bin:
        extra_paths.append(venv_bin)
        
    # 2a. AN EXPLICITLY CONFIGURED DIRECTORY WINS.
    #
    # WHY THIS EXISTS. The discovery below looks under LOCALAPPDATA, which
    # is per-USER. Run as a Windows service under SYSTEM, LOCALAPPDATA points
    # at C:\Windows\system32\config\systemprofile and a winget-installed
    # FFmpeg -- the normal way to install it -- is invisible. The poller then
    # syncs happily, downloads media, and fails every single warm with
    # "FFmpeg not found in system PATH", which reads like FFmpeg is missing
    # rather than like the account cannot see it.
    #
    # FFMPEG_DIR is how a service says where it actually is.
    configured = os.environ.get('FFMPEG_DIR', '').strip()
    if configured and os.path.isdir(configured):
        extra_paths.append(configured)

    # 2b. Add winget-installed FFmpeg bin directory to path
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        winget_pkg_dir = os.path.join(localappdata, 'Microsoft', 'WinGet', 'Packages')
        if os.path.exists(winget_pkg_dir):
            for item in os.listdir(winget_pkg_dir):
                if 'FFmpeg' in item:
                    full_pkg_path = os.path.join(winget_pkg_dir, item)
                    for root, dirs, files in os.walk(full_pkg_path):
                        if 'ffmpeg.exe' in files:
                            extra_paths.append(root)
                            break

    # Add extra paths to the front of os.environ['PATH']
    current_paths = os.environ.get('PATH', '').split(os.pathsep)
    for p in extra_paths:
        if p and p not in current_paths:
            os.environ['PATH'] = p + os.pathsep + os.environ['PATH']
            logger.info(f"Added subprocess dependency path to environment: {p}")

setup_subprocess_paths()


class MediaDiscovery:
    """
    Classifies a media URL so AudioExtractor can pick the most reliable extraction
    strategy for that source: a dedicated yt-dlp platform extractor, a raw media
    file, an HLS/DASH streaming manifest, an RSS/podcast feed, or a generic webpage.
    """

    DIRECT_AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.webm')
    STREAM_MANIFEST_EXTENSIONS = ('.m3u8', '.mpd')
    FEED_PATH_SUFFIXES = ('.rss', '.xml', '/rss', '/feed')

    # Domains with a dedicated, well-maintained yt-dlp extractor. These are routed
    # straight to yt-dlp, skipping the fragile regex-based page scrape entirely.
    KNOWN_PLATFORM_HOSTS = (
        'youtube.com', 'youtu.be',
        'tiktok.com',
        'facebook.com', 'fb.watch',
        'instagram.com',
        'twitter.com', 'x.com',
        'vimeo.com',
        'soundcloud.com',
        'brightcove.net',
        'jwplatform.com', 'jwplayer.com', 'cdn.jwplayer.com',
        'cloudflarestream.com',
    )

    CONTENT_TYPE_MANIFEST = ('application/vnd.apple.mpegurl', 'application/x-mpegurl', 'application/dash+xml')
    CONTENT_TYPE_FEED = ('application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml')

    _impersonation_available: Optional[bool] = None

    @classmethod
    def has_impersonation(cls) -> bool:
        """Whether yt-dlp's curl_cffi-based browser impersonation (helps bypass
        Cloudflare bot checks on generic sites) is installed."""
        if cls._impersonation_available is None:
            cls._impersonation_available = importlib.util.find_spec('curl_cffi') is not None
        return cls._impersonation_available

    @classmethod
    def _is_known_platform(cls, host: str) -> bool:
        return any(host == domain or host.endswith('.' + domain) for domain in cls.KNOWN_PLATFORM_HOSTS)

    @staticmethod
    def ytdlp_auth_args() -> List[str]:
        """
        Optional yt-dlp resilience/auth args, sourced entirely from environment
        variables -- never hardcoded, so this never silently touches a user's
        browser data unless they explicitly opt in.

          YTDLP_JS_RUNTIME          'node' (or any yt-dlp-supported runtime) to solve
                                     YouTube's signature ("n") challenge. Auto-enabled
                                     only if the binary is actually found on PATH.
          YTDLP_COOKIES_FILE        Path to a Netscape-format cookies.txt.
          YTDLP_COOKIES_FROM_BROWSER 'firefox' | 'chrome' | 'edge' | 'brave' | ...
                                     Reads that browser's live cookie store. Takes
                                     effect only if YTDLP_COOKIES_FILE is not set.

        Required for reliable YouTube extraction as of yt-dlp 2026.07: unauthenticated
        requests are now blocked with "Sign in to confirm you're not a bot" and most
        formats require the "n" challenge to be solved.

        Also always requests YouTube's ios/android player clients with the
        webpage fetch skipped: the "HTTP Error 429" / "Sign in to confirm
        you're not a bot" block observed in practice happens specifically on
        the *webpage* request the default web client makes -- the mobile
        client player APIs are a separate path that isn't gated the same
        way. Verified directly (2026-08-03): the same video that hard-failed
        with 429 via the default client resolved formats cleanly with
        `player_client=ios,android;player_skip=webpage`. Harmless for
        non-YouTube extractors, since --extractor-args is scoped to the
        IE_KEY it names.
        """
        args: List[str] = [
            '--extractor-args', 'youtube:player_client=ios,android;player_skip=webpage',
        ]

        js_runtime = os.environ.get('YTDLP_JS_RUNTIME', 'node')
        if js_runtime and shutil.which(js_runtime):
            args += ['--js-runtimes', js_runtime, '--remote-components', 'ejs:github']

        cookies_file = os.environ.get('YTDLP_COOKIES_FILE')
        cookies_browser = os.environ.get('YTDLP_COOKIES_FROM_BROWSER')
        if cookies_file:
            args += ['--cookies', cookies_file]
        elif cookies_browser:
            args += ['--cookies-from-browser', cookies_browser]

        return args

    @staticmethod
    def write_temp_cookies_file(cookie_header: str, domain: str) -> str:
        """
        Writes a raw Cookie header value out as a temporary Netscape
        cookies.txt file, for yt-dlp's --cookies flag. yt-dlp's own
        --add-header "Cookie:..." is explicitly unreliable for this -- it
        warns so itself ("a potential security risk; they will be scoped
        to the domain of the downloaded urls") and in practice didn't
        survive a redirect against a real authenticated site (confirmed
        directly: speech.intron.health, 2026-08-03) even though our own
        urllib-based fetches with the same cookie worked fine. --cookies
        is yt-dlp's own recommended mechanism instead. Caller is
        responsible for deleting the returned path when done.
        """
        fd, path = tempfile.mkstemp(suffix='.txt', prefix='ytdlp_cookies_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for part in cookie_header.split(';'):
                part = part.strip()
                if '=' not in part:
                    continue
                name, value = part.split('=', 1)
                # The Netscape format's "include subdomains" flag (2nd
                # column) must agree with whether the domain is dot-prefixed,
                # or Python's http.cookiejar (which yt-dlp uses) rejects the
                # whole file with a LoadError -- confirmed directly: writing
                # TRUE here with a bare hostname (no leading dot) failed
                # against speech.intron.health, 2026-08-03. domain is always
                # a bare hostname here (from urlparse().hostname), so this
                # is always a host-only cookie: FALSE.
                f.write(f"{domain}\tFALSE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}\n")
        return path

    _cookie_jar_cache = "unset"

    @classmethod
    def cookie_jar(cls):
        """
        Builds a cookie jar from YTDLP_COOKIES_FILE (Netscape cookies.txt),
        for use by the plain HTTP fetches (embedded-audio scraping,
        discovery page inspection, direct file downloads) that ytdlp_auth_args()
        doesn't reach -- those only apply to yt-dlp subprocess invocations.
        Some pages require a real login session to show their actual
        content rather than a login form (confirmed directly against
        speech.intron.health, 2026-08-03), so this closes that gap.

        Returns None if not configured -- every caller must handle that by
        falling back to an unauthenticated request, exactly as before.
        Cached after the first load since the file doesn't change mid-run
        and every discovery strategy + download step calls this.
        """
        if cls._cookie_jar_cache != "unset":
            return cls._cookie_jar_cache

        cls._cookie_jar_cache = None
        cookies_file = os.environ.get('YTDLP_COOKIES_FILE')
        if cookies_file:
            try:
                from yt_dlp.cookies import YoutubeDLCookieJar
                jar = YoutubeDLCookieJar(cookies_file)
                jar.load()
                cls._cookie_jar_cache = jar
            except Exception as e:
                logger.warning(f"Could not load cookies file '{cookies_file}': {e}")

        return cls._cookie_jar_cache

    @staticmethod
    def sniff_content_type(url: str, extra_headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Best-effort HEAD request (falling back to a small ranged GET) to read
        the Content-Type header, used only when the URL shape gives no signal.

        extra_headers: the per-job auth the user pasted for this job (see
        core.services.auth_helper); without it, cookies from
        YTDLP_COOKIES_FILE when configured.

        Sending that auth is not an optimization, it is the difference between
        classifying correctly and not: a session-gated media endpoint answers
        an anonymous request with 401/403 or a redirect to a login page, whose
        Content-Type is text/html. An extensionless authenticated URL then
        classifies as 'generic' and gets handed to the page-scraping branch
        instead of being downloaded as the audio it is. Confirmed against
        translation.eaglewebcommerce.com, whose player fetches
        /api/user/tasks/<id>/source -- no extension, audio/mpeg, cookie
        required.
        """
        for method, method_headers in (('HEAD', {}), ('GET', {'Range': 'bytes=0-1023'})):
            try:
                headers = {'User-Agent': DEFAULT_USER_AGENT, **method_headers}
                if extra_headers:
                    headers.update(extra_headers)
                req = urllib.request.Request(url, method=method, headers=headers)

                # Same precedence as _download_direct_file: an explicit Cookie
                # the user pasted wins outright over the ambient jar rather
                # than the two being merged into a conflicting pair.
                if extra_headers and extra_headers.get('Cookie'):
                    opener = urllib.request.build_opener()
                else:
                    cookie_jar = MediaDiscovery.cookie_jar()
                    opener = (
                        urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                        if cookie_jar is not None else urllib.request.build_opener()
                    )
                with opener.open(req, timeout=10) as resp:
                    content_type = resp.headers.get('Content-Type', '')
                    if content_type:
                        return content_type.split(';')[0].strip().lower()
            except Exception:
                continue
        return None

    @classmethod
    def classify(cls, url: str, extra_headers: Optional[Dict[str, str]] = None,
                 content_type_hint: Optional[str] = None) -> str:
        """Returns one of: 'known_platform', 'stream_manifest', 'direct_file', 'feed', 'generic'.

        extra_headers / content_type_hint are both optional and both only ever
        affect the extensionless case at the bottom -- every URL that classifies
        today from its shape alone classifies identically with or without them.
        See sniff_content_type for extra_headers, and the comment below for the
        hint.
        """
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        path = parsed.path.lower()

        if cls._is_known_platform(host):
            return 'known_platform'

        if path.endswith(cls.STREAM_MANIFEST_EXTENSIONS):
            return 'stream_manifest'

        if path.endswith(cls.DIRECT_AUDIO_EXTENSIONS):
            return 'direct_file'

        if path.endswith(cls.FEED_PATH_SUFFIXES) or path.rstrip('/').endswith(('rss', 'feed')):
            return 'feed'

        # No confident signal from the URL shape alone (common for CDN links with
        # no extension) -- use a type the caller has already OBSERVED if there is
        # one, else sniff the actual response, before falling back to generic.
        #
        # The hint exists for dynamic discovery, which watched the browser's own
        # authenticated response announce `Content-Type: audio/mpeg`. That is
        # strictly better evidence than a sniff can produce and it costs no
        # request at all -- which matters most exactly where it is used, since
        # re-sniffing a session-gated endpoint is the step that was misclassifying
        # these URLs as 'generic'.
        content_type = (content_type_hint or '').split(';')[0].strip().lower()
        if not content_type:
            content_type = cls.sniff_content_type(url, extra_headers=extra_headers)
        if content_type:
            if content_type in cls.CONTENT_TYPE_MANIFEST:
                return 'stream_manifest'
            if content_type.startswith('audio/'):
                return 'direct_file'
            if content_type in cls.CONTENT_TYPE_FEED:
                return 'feed'

        return 'generic'

    @staticmethod
    def parse_rss_enclosure(feed_url: str) -> Optional[str]:
        """Fetches an RSS/Atom feed and returns the most recent episode's audio enclosure URL."""
        try:
            req = urllib.request.Request(feed_url, headers={'User-Agent': DEFAULT_USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            root = ET.fromstring(data)

            # RSS 2.0: <rss><channel><item><enclosure url="..." type="audio/..."/></item>
            for enclosure in root.findall('.//item/enclosure'):
                enclosure_url = enclosure.get('url')
                if enclosure_url:
                    return enclosure_url

            # Atom: <feed><entry><link rel="enclosure" href="..."/></entry>
            atom_ns = '{http://www.w3.org/2005/Atom}'
            for entry in root.findall(f'.//{atom_ns}entry'):
                for link in entry.findall(f'{atom_ns}link'):
                    if link.get('rel') == 'enclosure' and link.get('href'):
                        return link.get('href')

            return None
        except Exception as e:
            logger.warning(f"Failed to parse RSS/Atom feed '{feed_url}': {e}")
            return None


class AudioExtractor:
    """
    Handles media download and extraction of 16kHz mono audio using FFmpeg and yt-dlp.
    """
    @staticmethod
    def extract_from_file(input_path: str, output_wav_path: str,
                           cancel_check: Optional[Callable[[], bool]] = None,
                           error_detail: Optional[Dict[str, str]] = None) -> bool:
        """
        Extract mono 16kHz WAV audio from a local media file. FFmpeg extraction
        in this pipeline consistently completes in well under a second, so
        cancel_check is only consulted before starting (not mid-run) -- not
        worth a Popen-polling refactor for a subprocess that's never actually
        the slow part.

        error_detail: optional dict the caller can pass to receive a short,
        human-readable failure reason (e.g. corrupt file, unsupported codec)
        on top of the plain True/False return -- purely additive, every
        existing caller that doesn't pass it keeps its exact current
        behavior. Added specifically so uploaded-file failures (see
        tasks.run_transcription_pipeline) can surface something more useful
        than a generic "extraction failed" to the user.
        """
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled before FFmpeg extraction started.")
        logger.info(f"Extracting audio from file: {input_path} -> {output_wav_path}")
        command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-ar', '16000',
            '-ac', '1',
            '-c:a', 'pcm_s16le',
            output_wav_path
        ]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info("Audio extraction completed successfully.")
            return True
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode(errors='ignore') if e.stderr else ''
            logger.error(f"FFmpeg error: {stderr_text}")
            if error_detail is not None:
                error_detail['detail'] = stderr_text
            return False
        except FileNotFoundError:
            logger.error("FFmpeg not found in system PATH. Please install FFmpeg.")
            if error_detail is not None:
                error_detail['detail'] = 'FFmpeg is not installed or not found on the server.'
            return False

    @staticmethod
    def _find_embedded_audio_url(html: str, base_url: str) -> Optional[str]:
        """Try to locate a direct audio source URL inside HTML, inline scripts, or JS players."""
        if not html:
            return None

        # 1. Unescape JSON escaped forward slashes (\/) and unicode escapes so JSON/JS strings match cleanly
        unescaped_html = html.replace(r'\/', '/')

        # 2. Check HTML5 audio/video tags (src attribute takes priority)
        audio_src_match = re.search(
            r"<(?:audio|video)\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", unescaped_html, flags=re.IGNORECASE,
        )
        if audio_src_match:
            return urljoin(base_url, audio_src_match.group(1))

        # 3. Check wavesurfer.load(...) or similar JS player audio initializers
        ws_match = re.search(
            r"wavesurfer\s*\.\s*load\s*\(\s*(?:JSON\.parse\s*\(\s*)?['\"]([^'\"]+)['\"]", unescaped_html, flags=re.IGNORECASE
        )
        if ws_match:
            candidate = ws_match.group(1).replace(r'\"', '"').strip('\'"')
            if candidate.startswith(('http://', 'https://', '/')):
                return urljoin(base_url, candidate)

        # 4. Prioritize full audio URLs (http:// or https://)
        for pattern in [
            r"https?://[^\"'\s<>]+\.(?:mp3|m4a|wav|aac|ogg|flac|webm)(?:\?[^\"'\s<>]*)?",
        ]:
            match = re.search(pattern, unescaped_html, flags=re.IGNORECASE)
            if match:
                return match.group(0)

        # 5. Look for custom audio file paths (relative or absolute)
        for pattern in [
            r"file\s*:\s*['\"]([^'\"]+)['\"]",
            r"['\"](\/(?:transcribe\/|user\/|static\/|audios\/|audio\/)[^'\"]+\.(?:mp3|m4a|wav|aac|ogg|flac|webm)[^'\"]*)['\"]",
            r"['\"]([^'\"]+\.(?:mp3|m4a|wav|aac|ogg|flac|webm)(?:\?[^'\"]*)?)['\"]",
        ]:
            match = re.search(pattern, unescaped_html, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    return urljoin(base_url, candidate)

        return None

    @staticmethod
    def _download_direct_file_impersonated(url: str, temp_audio: str, headers: Dict[str, str],
                                            cancel_check: Optional[Callable[[], bool]] = None,
                                            session: Optional[Any] = None) -> bool:
        """
        Streams a direct media file download using curl_cffi's browser
        TLS/HTTP2 fingerprint impersonation, writing chunks to disk as they
        arrive -- never buffers the whole file in memory, same as the
        plain-urllib path this is tried before. Returns False (never
        raises, except JobCancelledError) on any failure -- including
        curl_cffi not being installed -- so the caller falls through to
        the existing plain-urllib attempt unchanged.

        Confirmed necessary, not theoretical: nch.com.au (used throughout
        this project's own testing) serves files to an impersonated client
        at roughly 3x the effective throughput measured for the exact same
        file otherwise -- ~41.5 KB/s via curl_cffi vs ~12.6 KB/s effective
        via yt-dlp's own retry-heavy download (389s for a 4.9MB file), and
        plain urllib essentially never got going. Same class of issue as
        fetch_page_impersonated, just for raw media bytes instead of HTML.

        session: optional curl_cffi.requests.Session, same purpose as in
        fetch_page_impersonated -- pass the session that discovered this
        URL (e.g. from an embedded-page scrape) so the download request
        carries whatever cookies that discovery set, for sites where the
        media itself is also session-gated.
        """
        if not MediaDiscovery.has_impersonation():
            return False
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled before impersonated direct download started.")
        # curl_cffi's Response does NOT support the `with ... as` context-
        # manager protocol (confirmed directly against the installed
        # version -- unlike `requests`, which this library otherwise
        # mirrors) -- it must be closed explicitly instead, in a finally
        # block below.
        response = None
        try:
            from curl_cffi import requests as curl_requests
            requester = session if session is not None else curl_requests
            started_at = time.monotonic()
            response = requester.get(url, headers=headers, impersonate='chrome', stream=True,
                                      timeout=IMPERSONATED_DOWNLOAD_DEADLINE_SECONDS)
            response.raise_for_status()
            with open(temp_audio, 'wb') as out_file:
                for chunk in response.iter_content(chunk_size=65536):
                    if time.monotonic() - started_at > IMPERSONATED_DOWNLOAD_DEADLINE_SECONDS:
                        raise TimeoutError(
                            f"Impersonated direct download exceeded the {IMPERSONATED_DOWNLOAD_DEADLINE_SECONDS}s "
                            "wall-clock deadline."
                        )
                    if cancel_check and cancel_check():
                        raise JobCancelledError("Cancelled during impersonated direct download.")
                    if chunk:
                        out_file.write(chunk)
            return True
        except JobCancelledError:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            raise
        except Exception as e:
            logger.warning(f"curl_cffi impersonated direct download failed for '{url}' ({e}); falling back to plain urllib.")
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            return False
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    @staticmethod
    def _download_direct_file(url: str, temp_audio: str, extra_headers: Optional[Dict[str, str]] = None,
                               cancel_check: Optional[Callable[[], bool]] = None,
                               session: Optional[Any] = None) -> bool:
        """Downloads a raw media file URL directly via HTTP. Sends extra_headers
        (per-job auth the user explicitly pasted, see core.services.auth_helper)
        when given, else cookies from YTDLP_COOKIES_FILE when configured, in
        case the media itself (not just the page linking to it) requires an
        authenticated session.

        session: optional curl_cffi.requests.Session forwarded to the
        impersonated attempt -- see fetch_page_impersonated's docstring."""
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled before direct download started.")
        with profile_stage("direct_http_download", url=url):
            headers = {'User-Agent': DEFAULT_USER_AGENT}
            if extra_headers:
                headers.update(extra_headers)

            # Tried first -- see _download_direct_file_impersonated's
            # docstring for the confirmed real-world throughput difference.
            # Falls through to the plain urllib path below on any failure.
            if AudioExtractor._download_direct_file_impersonated(url, temp_audio, headers, cancel_check=cancel_check,
                                                                   session=session):
                return True

            try:
                req = urllib.request.Request(url, headers=headers)

                if extra_headers and extra_headers.get('Cookie'):
                    opener = urllib.request.build_opener()
                else:
                    cookie_jar = MediaDiscovery.cookie_jar()
                    opener = (
                        urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                        if cookie_jar is not None else urllib.request.build_opener()
                    )
                with opener.open(req, timeout=30) as response, open(temp_audio, 'wb') as out_file:
                    started_at = time.monotonic()
                    while True:
                        if time.monotonic() - started_at > DIRECT_DOWNLOAD_DEADLINE_SECONDS:
                            raise TimeoutError(
                                f"Direct download exceeded the {DIRECT_DOWNLOAD_DEADLINE_SECONDS}s "
                                "wall-clock deadline (origin is alive but transferring too slowly)."
                            )
                        if cancel_check and cancel_check():
                            raise JobCancelledError("Cancelled during direct download.")
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        out_file.write(chunk)
                return True
            except JobCancelledError:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                raise
            except Exception as e:
                logger.warning(f"Direct HTTP download failed: {e}")
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                return False

    @staticmethod
    def _run_subprocess_cancellable(command: List[str], cancel_check: Optional[Callable[[], bool]] = None):
        """
        Runs a subprocess like subprocess.run(..., check=True) would, but
        polls cancel_check every ~0.5s while it's alive instead of blocking
        uninterruptibly -- this is what makes a multi-minute yt-dlp download
        actually stoppable mid-flight rather than only "stops before the next
        stage starts". Raises JobCancelledError (terminating the subprocess
        first) if cancel_check reports True; otherwise behaves exactly like
        subprocess.run for callers (raises CalledProcessError on non-zero
        exit, returns a completed-process-like object on success).

        stdout/stderr are drained on dedicated threads the whole time the
        process runs, not just after it exits -- yt-dlp's progress output can
        exceed the OS pipe buffer, and polling proc.wait() without reading the
        pipes concurrently would deadlock the child once its buffer fills.
        """
        import threading

        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []

        def _drain(pipe, sink):
            try:
                for chunk in iter(lambda: pipe.read(4096), b''):
                    sink.append(chunk)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_check and cancel_check():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    stdout_thread.join(timeout=5)
                    stderr_thread.join(timeout=5)
                    raise JobCancelledError("yt-dlp download cancelled.")

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        stdout = b''.join(stdout_chunks)
        stderr = b''.join(stderr_chunks)

        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)

    @staticmethod
    def _run_ytdlp(url: str, temp_audio: str, extra_args: Optional[List[str]] = None,
                    extra_headers: Optional[Dict[str, str]] = None,
                    cancel_check: Optional[Callable[[], bool]] = None) -> bool:
        """Runs yt-dlp as a subprocess to download the best audio track for a URL."""
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled before yt-dlp download started.")
        with profile_stage("ytdlp_download", url=url):
            base_command = [
                sys.executable, '-m', 'yt_dlp',
                '--user-agent', DEFAULT_USER_AGENT,
                '--no-playlist',
                '--format', 'bestaudio/best',
            ]
            base_command.extend(MediaDiscovery.ytdlp_auth_args())

            temp_cookies_path = None
            for header_name, header_value in (extra_headers or {}).items():
                if header_name.lower() == 'cookie':
                    domain = urlparse(url).hostname or ''
                    temp_cookies_path = MediaDiscovery.write_temp_cookies_file(header_value, domain)
                    base_command.extend(['--cookies', temp_cookies_path])
                else:
                    base_command.extend(['--add-header', f'{header_name}:{header_value}'])
            if extra_args:
                base_command.extend(extra_args)

            try:
                # If discovery already resolved this exact URL (YtdlpPlaylistStrategy
                # caches full info for single-video URLs, since --flat-playlist only
                # flattens entries *inside* a playlist), reuse it via
                # --load-info-json to skip a second, redundant full extraction.
                # Deferred import: ai_pipeline <-> discovery would otherwise be a
                # circular import at module load time.
                try:
                    from backend.core.services.discovery import metadata_cache
                except ModuleNotFoundError:
                    from core.services.discovery import metadata_cache

                cached_info_path = metadata_cache.take(url)
                if cached_info_path:
                    try:
                        logger.info(f"Reusing cached discovery metadata for '{url}' (skipping redundant yt-dlp info lookup)...")
                        command = base_command + ['--load-info-json', cached_info_path, '--output', temp_audio]
                        AudioExtractor._run_subprocess_cancellable(command, cancel_check=cancel_check)
                        logger.info("yt-dlp download completed (cached metadata).")
                        return True
                    except JobCancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"yt-dlp download using cached metadata failed, retrying with a fresh lookup: {e}")
                    finally:
                        metadata_cache.discard(cached_info_path)

                command = base_command + ['--output', temp_audio, url]
                try:
                    logger.info("Executing yt-dlp download...")
                    AudioExtractor._run_subprocess_cancellable(command, cancel_check=cancel_check)
                    logger.info("yt-dlp download completed.")
                    return True
                except JobCancelledError:
                    raise
                except subprocess.CalledProcessError as e:
                    stderr = e.stderr.decode(errors='ignore') if e.stderr else str(e)
                    logger.error(f"yt-dlp download failed: {stderr}")
                    if _looks_like_login_redirect(url, stderr):
                        raise LoginRequiredError(
                            f"'{url}' redirected to a login page -- this site requires an authenticated "
                            "session to access this content. Paste your site cookies using the "
                            "Authentication helper and try again."
                        )
                    return False
                except FileNotFoundError:
                    logger.error("yt-dlp module cannot be executed. Please install yt-dlp into the active virtual environment.")
                    return False
            finally:
                if temp_cookies_path and os.path.exists(temp_cookies_path):
                    os.remove(temp_cookies_path)

    @staticmethod
    def _try_ytdlp(url: str, temp_audio: str, output_wav_path: str, extra_args: Optional[List[str]] = None,
                    extra_headers: Optional[Dict[str, str]] = None,
                    cancel_check: Optional[Callable[[], bool]] = None) -> bool:
        """Runs yt-dlp then converts the result to WAV, cleaning up the temp file either way
        (including when cancelled mid-download, so a cancelled job never leaves a
        partial download sitting on disk)."""
        try:
            success = AudioExtractor._run_ytdlp(url, temp_audio, extra_args=extra_args,
                                                 extra_headers=extra_headers, cancel_check=cancel_check)
            if success:
                success = AudioExtractor.extract_from_file(temp_audio, output_wav_path, cancel_check=cancel_check)
            return success
        finally:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)

    @staticmethod
    def _try_embedded_scrape(url: str, temp_audio: str, output_wav_path: str,
                              extra_headers: Optional[Dict[str, str]] = None,
                              cancel_check: Optional[Callable[[], bool]] = None,
                              html_sink: Optional[Dict[str, str]] = None) -> bool:
        """Scrapes the page HTML for a directly-linked audio file as a lightweight
        fallback. Sends extra_headers (per-job auth, see core.services.auth_helper)
        when given, else cookies from YTDLP_COOKIES_FILE when configured, since a
        page requiring login shows only its login form otherwise -- no embedded
        audio to find.

        html_sink: optional dict this stores the fetched page HTML into under
        the key 'html'. Purely additive -- when omitted (every pre-existing
        caller) behaviour is unchanged. It exists so a later stage can assess
        the page WITHOUT refetching it: dynamic discovery decides whether to
        launch a browser from this same HTML, and a second network round-trip
        for bytes we already had would defeat the point."""
        if cancel_check and cancel_check():
            raise JobCancelledError("Cancelled before embedded-scrape page fetch started.")
        # One curl_cffi Session shared between the page fetch below AND the
        # subsequent download of whatever audio URL it finds -- confirmed
        # that curl_cffi's Session does retain cookies (e.g. a Set-Cookie
        # session id) across calls, unlike the plain module-level
        # curl_requests.get() used everywhere else, which starts a fresh,
        # cookie-less request every time. Needed for any site that gates
        # either the page content or the media file itself behind a
        # session cookie set on an earlier response. None when
        # impersonation isn't installed -- fetch_page_impersonated/
        # _download_direct_file_impersonated both treat that the same as
        # "no session available" and fall back to their stateless paths.
        session = None
        if MediaDiscovery.has_impersonation():
            try:
                from curl_cffi import requests as curl_requests
                session = curl_requests.Session()
            except Exception:
                session = None
        with profile_stage("embedded_scrape_page_fetch", url=url):
            try:
                headers = {'User-Agent': DEFAULT_USER_AGENT}
                if extra_headers:
                    headers.update(extra_headers)

                # Tried first: some sites tarpit plain urllib/http.client
                # requests (connection stays open, data trickles too
                # slowly to ever finish) based on their TLS/HTTP2
                # handshake, not just the User-Agent header -- see
                # fetch_page_impersonated's own docstring for the
                # confirmed real-world case this fixes. Falls through to
                # the plain urllib path below on any failure (including
                # curl_cffi not being installed at all).
                impersonated = fetch_page_impersonated(url, headers, session=session)
                if impersonated is not None:
                    html = impersonated.decode('utf-8', errors='ignore')
                else:
                    req = urllib.request.Request(url, headers=headers)

                    if extra_headers and extra_headers.get('Cookie'):
                        opener = urllib.request.build_opener()
                    else:
                        cookie_jar = MediaDiscovery.cookie_jar()
                        opener = (
                            urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                            if cookie_jar is not None else urllib.request.build_opener()
                        )
                    with opener.open(req, timeout=30) as response:
                        html = read_response_body_with_deadline(
                            response, PAGE_FETCH_DEADLINE_SECONDS, cancel_check=cancel_check,
                        ).decode('utf-8', errors='ignore')
            except JobCancelledError:
                raise
            except Exception as e:
                logger.warning(f"Could not fetch page for embedded audio scraping: {e}")
                return False

        embedded_audio = AudioExtractor._find_embedded_audio_url(html, url)
        if html_sink is not None:
            html_sink['html'] = html
        if not embedded_audio:
            return False

        logger.info(f"Found embedded audio URL: {embedded_audio}")
        # yt-dlp tried FIRST here, not the raw HTTP GET below -- same fix,
        # same reason as the 'direct_file' branch of download_and_extract_url:
        # a plain/impersonated GET has no retry/resume logic, so a host that
        # throttles or tarpits inconsistently (confirmed live, this exact
        # site: gotranscript.com) can burn its own full deadline stack
        # (impersonated + plain-urllib fallback, ~195s worst case) and
        # STILL end up needing yt-dlp afterward -- observed live at 205s
        # wasted before yt-dlp even started. yt-dlp has proven reliable on
        # this exact class of host, so it goes first; the raw download
        # (with the session that discovered this URL, for sites where the
        # media itself is also session-gated) is the fallback.
        if AudioExtractor._try_ytdlp(embedded_audio, temp_audio, output_wav_path, extra_headers=extra_headers,
                                      cancel_check=cancel_check):
            return True

        logger.warning(f"yt-dlp failed for embedded audio URL; retrying via direct download: {embedded_audio}")
        if not AudioExtractor._download_direct_file(embedded_audio, temp_audio, extra_headers=extra_headers,
                                                      cancel_check=cancel_check, session=session):
            return False

        success = AudioExtractor.extract_from_file(temp_audio, output_wav_path, cancel_check=cancel_check)
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
        return success

    @staticmethod
    def _try_dynamic_discovery(url: str, output_wav_path: str,
                                page_html: Optional[str] = None,
                                extra_headers: Optional[Dict[str, str]] = None,
                                cancel_check: Optional[Callable[[], bool]] = None,
                                depth: int = 0) -> bool:
        """Last resort for JavaScript-rendered pages: run the page in a headless
        browser only long enough to see which HTTP request feeds its player, then
        hand that URL straight back to the normal download path.

        Reached only after the static scrape AND yt-dlp's generic extractor have
        both already failed, so no URL that works today ever gets here -- that is
        what keeps this off the normal transcription latency budget entirely.
        Even then, `should_attempt` must find JS-app evidence in page_html (the
        HTML the scrape above already fetched -- no new request) before a browser
        is launched.

        Returns False for every "couldn't do it" outcome, including the feature
        being switched off or Playwright not being installed, so a machine without
        the optional dependency behaves exactly as it does today. Cancellation is
        the one exception: it is re-raised as JobCancelledError, since callers must
        not mistake a cancelled job for a failed download.
        """
        try:
            from core.services import dynamic_discovery
        except Exception as e:
            logger.info(f"Dynamic discovery unavailable ({e}); skipping.")
            return False

        attempt, reason = dynamic_discovery.should_attempt(page_html)
        if not attempt:
            logger.info(f"Skipping dynamic media discovery: {reason}.")
            return False
        logger.info(f"Attempting dynamic media discovery ({reason}).")

        try:
            with profile_stage("dynamic_discovery", url=url):
                resolved = dynamic_discovery.resolve(
                    url,
                    extra_headers=extra_headers,
                    cancel_check=cancel_check,
                    user_agent=(extra_headers or {}).get('User-Agent'),
                )
        except dynamic_discovery.DiscoveryCancelled as e:
            raise JobCancelledError(str(e))
        except Exception as e:
            # Includes DiscoveryUnavailable (no browser binary installed) and a
            # browser thread that overran its deadline. Neither is a download
            # failure in itself -- the caller simply reports what it already
            # knew: nothing could resolve this URL.
            #
            # The exception TYPE is logged, not just str(e): several failures
            # here carry an empty message (asyncio's NotImplementedError on
            # Windows, for one), and "could not run: " with nothing after it
            # tells an operator nothing at all. exc_info gives the frame that
            # actually failed, which is the difference between a five-minute
            # diagnosis and an afternoon of guessing.
            logger.warning(
                "Dynamic media discovery could not run: %s: %s",
                type(e).__name__, e or '(no message)', exc_info=True,
            )
            return False

        if not resolved:
            return False

        if resolved.url == url:
            # The page resolved to itself, which would recurse straight back
            # into this same branch.
            logger.warning("Dynamic discovery returned the original page URL; ignoring.")
            return False

        logger.info(f"Dynamic discovery resolved media source: {resolved.describe()}")

        # The browser's own request headers (Referer/Origin/Cookie) are what
        # made that request succeed, so they are forwarded -- but the auth the
        # USER explicitly pasted for this job wins on conflict. Their session
        # is the authorization we are entitled to act on; the browser's is
        # derived from it.
        merged_headers = dict(resolved.headers)
        if extra_headers:
            merged_headers.update(extra_headers)

        # resolved.content_type is forwarded rather than discarded. Without it
        # the next classify() call would re-derive the type from the URL alone,
        # and a discovered media endpoint frequently has no extension to derive
        # from (/api/user/tasks/<id>/source is the confirmed real case) -- so it
        # would fall through to an unauthenticated sniff, read a login page, and
        # classify as 'generic'. That sends proven audio into the page-scraping
        # branch, which then refuses to recurse (_dynamic_depth >= 1) and the
        # whole job fails one step after discovery succeeded.
        return AudioExtractor.download_and_extract_url(
            resolved.url, output_wav_path,
            extra_headers=merged_headers or None,
            cancel_check=cancel_check,
            _dynamic_depth=depth + 1,
            content_type_hint=resolved.content_type or None,
        )

    @staticmethod
    def download_and_extract_url(url: str, output_wav_path: str,
                                  extra_headers: Optional[Dict[str, str]] = None,
                                  cancel_check: Optional[Callable[[], bool]] = None,
                                  _dynamic_depth: int = 0,
                                  content_type_hint: Optional[str] = None) -> bool:
        """
        Downloads audio from a public media URL. MediaDiscovery classifies the URL
        first, and the extraction strategy is chosen accordingly:
          - known_platform  -> dedicated yt-dlp site extractor (YouTube, TikTok, etc.)
          - stream_manifest -> FFmpeg reads the HLS/DASH manifest directly, no download step
          - feed            -> RSS/Atom feed is parsed for its audio enclosure, then re-dispatched
          - direct_file     -> yt-dlp first (most reliable on bot-fingerprint-protected
                                hosts), falling back to a plain HTTP download of the raw
                                media file if yt-dlp itself doesn't help
          - generic         -> embedded-link scrape, then yt-dlp's generic extractor as broad
                                fallback, then dynamic (headless-browser) discovery for pages
                                whose player only exists after JavaScript runs
        Every branch still falls through to yt-dlp before giving up, preserving the
        original "always try yt-dlp last" resilience for URLs that don't fit neatly.

        extra_headers: optional per-job auth (see core.services.auth_helper) the
        user explicitly pasted for this job -- forwarded to every HTTP request
        this makes, for sites that require a login session.

        cancel_check: optional zero-arg callable (see tasks.run_transcription_pipeline)
        returning True once the job has been cancelled. Consulted before each
        network/subprocess step, and polled continuously during the yt-dlp
        subprocess specifically (the one step slow enough that "stop before the
        next step" wouldn't feel immediate). Raises JobCancelledError when
        cancellation is detected -- callers must not treat that as a download
        failure.

        _dynamic_depth: internal recursion guard. Dynamic discovery re-dispatches
        the media URL it finds back through this method (the same pattern the
        'feed' branch uses for an enclosure URL); this counter stops a page that
        resolves to another page that resolves again from looping.

        content_type_hint: a Content-Type the caller has already seen on this
        exact URL's response. Only dynamic discovery passes one -- it observed
        the browser's own authenticated response -- and it only ever short-
        circuits the extensionless sniff inside classify().
        """
        logger.info(f"Downloading from public URL: {url}")

        # Eagle fast-path: For Eagle task URLs at the root pipeline depth (_dynamic_depth == 0),
        # skip the extensionless classification sniff, static HTML scrape, and failed yt-dlp
        # invocation against the SPA page, and resolve directly via the Eagle fast-path.
        if _dynamic_depth == 0:
            try:
                try:
                    from backend.core.services import dynamic_discovery
                except ModuleNotFoundError:
                    from core.services import dynamic_discovery
                if dynamic_discovery.is_eagle_task_url(url) and dynamic_discovery.is_available():
                    logger.info("Detected Eagle task URL; attempting direct Eagle fast-path discovery.")
                    try:
                        with profile_stage("eagle_fast_path_discovery", url=url):
                            eagle_media = dynamic_discovery.resolve_eagle_task(
                                url,
                                extra_headers=extra_headers,
                                cancel_check=cancel_check,
                                user_agent=(extra_headers or {}).get('User-Agent'),
                            )
                        if eagle_media:
                            logger.info(f"Eagle fast-path resolved media source: {eagle_media.describe()}")
                            merged_headers = dict(eagle_media.headers)
                            if extra_headers:
                                merged_headers.update(extra_headers)
                            return AudioExtractor.download_and_extract_url(
                                eagle_media.url, output_wav_path,
                                extra_headers=merged_headers or None,
                                cancel_check=cancel_check,
                                _dynamic_depth=_dynamic_depth + 1,
                                content_type_hint=eagle_media.content_type or 'audio/mpeg',
                            )
                        logger.warning("Eagle fast-path returned no media source; falling back to standard pipeline.")
                    except dynamic_discovery.DiscoveryCancelled as e:
                        raise JobCancelledError(str(e))
                    except Exception as exc:
                        logger.warning(
                            "Eagle fast-path encountered error (%s: %s); falling back to standard pipeline.",
                            type(exc).__name__, exc or '(no message)', exc_info=True,
                        )
            except ImportError:
                pass

        temp_audio = output_wav_path + ".temp"
        kind = MediaDiscovery.classify(url, extra_headers=extra_headers,
                                       content_type_hint=content_type_hint)
        logger.info(f"Media Discovery classified URL as: '{kind}'")

        if kind == 'stream_manifest':
            logger.info("Extracting directly from streaming manifest via FFmpeg...")
            if AudioExtractor.extract_from_file(url, output_wav_path, cancel_check=cancel_check):
                return True
            logger.warning("Direct manifest extraction failed. Falling back to yt-dlp.")
            return AudioExtractor._try_ytdlp(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                              cancel_check=cancel_check)

        if kind == 'feed':
            enclosure_url = MediaDiscovery.parse_rss_enclosure(url)
            if enclosure_url:
                logger.info(f"Resolved feed to episode audio URL: {enclosure_url}")
                return AudioExtractor.download_and_extract_url(enclosure_url, output_wav_path, extra_headers=extra_headers,
                                                                 cancel_check=cancel_check)
            logger.warning("No audio enclosure found in feed. Falling back to yt-dlp.")
            return AudioExtractor._try_ytdlp(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                              cancel_check=cancel_check)

        if kind == 'direct_file':
            logger.info("Executing yt-dlp download...")
            extra_args = ['--extractor-args', 'generic:impersonate'] if MediaDiscovery.has_impersonation() else None
            if AudioExtractor._try_ytdlp(url, temp_audio, output_wav_path, extra_args=extra_args, extra_headers=extra_headers,
                                          cancel_check=cancel_check):
                return True
            logger.warning("yt-dlp failed for direct file URL; retrying via plain HTTP download.")
            if AudioExtractor._download_direct_file(url, temp_audio, extra_headers=extra_headers, cancel_check=cancel_check):
                success = AudioExtractor.extract_from_file(temp_audio, output_wav_path, cancel_check=cancel_check)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                if success:
                    return True
            logger.warning("Direct download retry failed too. Attempting embedded-link scrape as a last resort.")
            return AudioExtractor._try_embedded_scrape(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                                         cancel_check=cancel_check)

        if kind == 'known_platform':
            if AudioExtractor._try_ytdlp(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                          cancel_check=cancel_check):
                return True
            logger.warning("Platform extractor failed. Attempting embedded-link scrape as last resort.")
            return AudioExtractor._try_embedded_scrape(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                                         cancel_check=cancel_check)

        # kind == 'generic'
        # html_sink captures the page HTML the scrape fetches so the dynamic
        # stage below can judge whether this is a JS app without fetching it
        # again. Empty dict, never None, so a scrape that fails before the
        # fetch still leaves a well-defined (absent) value.
        page_source: Dict[str, str] = {}
        if AudioExtractor._try_embedded_scrape(url, temp_audio, output_wav_path, extra_headers=extra_headers,
                                                cancel_check=cancel_check, html_sink=page_source):
            return True
        logger.info("No direct embedded audio link found. Falling back to yt-dlp's generic extractor.")
        extra_args = ['--extractor-args', 'generic:impersonate'] if MediaDiscovery.has_impersonation() else None
        if AudioExtractor._try_ytdlp(url, temp_audio, output_wav_path, extra_args=extra_args, extra_headers=extra_headers,
                                      cancel_check=cancel_check):
            return True

        # Both cheap strategies are exhausted. Only now is a browser considered
        # -- and only for a page that looks like a JavaScript application.
        if _dynamic_depth >= 1:
            logger.info("Dynamic discovery already ran for this job; not recursing.")
            return False
        return AudioExtractor._try_dynamic_discovery(
            url, output_wav_path, page_html=page_source.get('html'),
            extra_headers=extra_headers, cancel_check=cancel_check, depth=_dynamic_depth,
        )

class WhisperTranscriber:
    """
    Coordinates Whisper model execution, language detection/selection, and audio chunking.
    """
    # --- Performance tunables -------------------------------------- #
    #
    # All previously implicit. The defaults below are chosen for a CPU box
    # and are overridable, because the right values depend on the machine
    # and on whether a job is latency-sensitive or accuracy-sensitive.
    #
    # WHISPER_CPU_THREADS: 0 means "let ctranslate2 decide", and MEASUREMENT
    # SAYS LEAVE IT THERE. On an 8-core box, forcing 8 threads was SLOWER
    # than the default (46.9s vs 37.5s on the same 5.9-minute file) and 4
    # threads was slower still -- ASR here is memory-bandwidth bound, not
    # core bound, so extra threads buy contention rather than throughput.
    # Exposed anyway because that balance is machine-specific; just measure
    # before changing it.
    #
    # WHISPER_BEAM_SIZE: 5 is accurate; greedy (1) is ~38% faster (23.4s vs
    # 37.5s). Kept at 5, because the two are NOT equivalent: word-level
    # similarity between them was 0.942 over 33 differing spans, and at
    # least one was a dropped word rather than a punctuation choice. For a
    # strict-verbatim legal transcript a dropped word is a defect, so the
    # speed is not worth taking by default. Lower it per-job if a particular
    # workload can afford it.
    DEFAULT_CPU_THREADS = 0
    DEFAULT_BEAM_SIZE = 5

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: Optional[int] = None,
        beam_size: Optional[int] = None,
    ):
        self.model_size = model_size or _str_env('WHISPER_MODEL_SIZE', 'base')
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = (
            cpu_threads if cpu_threads is not None
            else _int_env('WHISPER_CPU_THREADS', self.DEFAULT_CPU_THREADS)
        )
        self.beam_size = (
            beam_size if beam_size is not None
            else _int_env('WHISPER_BEAM_SIZE', self.DEFAULT_BEAM_SIZE)
        )
        self.model = None
        # Populated as soon as transcribe() starts running (language detection/selection
        # happens eagerly, before any segment is yielded) so callers can persist it.
        self.last_info = None

    def load_model(self):
        """
        Loads the faster-whisper model into memory/VRAM if not already loaded.

        Wrapped in its own profile_stage (separate from the "transcription"
        stage in tasks.py) because this cost is a one-time-per-process
        cold-start, not per-file work: `from faster_whisper import
        WhisperModel` is the first time this process ever touches
        faster_whisper's native ctranslate2 dependency, and that import
        alone (before ANY log line -- it happens before the "Loading
        WhisperModel..." message below) can silently cost tens of seconds
        on a cold process. Logging a line before the import too, so that
        cost shows up in real time instead of only as an unexplained gap
        between whichever log lines happen to sit before/after it.
        """
        if self.model is None:
            try:
                logger.info(f"Loading faster_whisper module (first use this process)...")
                with profile_stage("whisper_model_load", model_size=self.model_size, device=self.device):
                    from faster_whisper import WhisperModel
                    logger.info(f"Loading WhisperModel '{self.model_size}' on '{self.device}' ({self.compute_type})...")
                    self.model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                        cpu_threads=self.cpu_threads,
                    )
                    logger.info(
                        "Whisper: model=%s threads=%d beam=%d",
                        self.model_size, self.cpu_threads, self.beam_size,
                    )
                logger.info("Whisper model loaded successfully.")
            except ImportError as e:
                logger.error("faster-whisper package is required for real transcription.")
                raise RuntimeError("faster-whisper package is required for real transcription.") from e

    def transcribe(self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None) -> Generator[Dict[str, Any], None, None]:
        """
        Transcribes the audio file and yields segments as they are processed.

        If `language` is None, Whisper auto-detects the spoken language from the
        first 30s of audio. If `language` is a Whisper-supported code (see
        core.services.languages.SUPPORTED_LANGUAGES), auto-detection is skipped
        entirely and that language is used directly -- this both saves the
        detection pass and improves accuracy, since Whisper no longer has to
        guess against related/similar-sounding languages.
        """
        self.load_model()

        # Core Faster-Whisper Pipeline
        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio file not found: {audio_path}")

        segments, info = self.model.transcribe(
            audio_path,
            beam_size=self.beam_size,
            vad_filter=enable_vad,
            vad_parameters=dict(min_speech_duration_ms=250),
            language=language
        )
        self.last_info = info

        logger.info(f"Language in use: '{info.language}' with probability {info.language_probability:.2f}")

        for segment in segments:
            yield {
                "text": segment.text.strip(),
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "language": info.language,
                "confidence": round(segment.avg_logprob, 4)
            }

# Quick test routine
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ai_pipeline.py <input_audio_or_video_file_or_url>")
        sys.exit(1)
        
    source = sys.argv[1]
    out_wav = "extracted_audio.wav"
    
    if source.startswith("http://") or source.startswith("https://"):
        success = AudioExtractor.download_and_extract_url(source, out_wav)
    else:
        success = AudioExtractor.extract_from_file(source, out_wav)
        
    if success:
        transcriber = WhisperTranscriber(model_size="tiny")
        for chunk in transcriber.transcribe(out_wav):
            print(f"[{chunk['start']:.2f}s - {chunk['end']:.2f}s] {chunk['text']}")
        
        if os.path.exists(out_wav):
            os.remove(out_wav)
    else:
        print("Failed to process audio extraction.")
