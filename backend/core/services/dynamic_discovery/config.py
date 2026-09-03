"""
Configuration for dynamic media discovery.

Same resolution order as core.services.ai_assist.providers.config -- Django
setting, then environment variable, then hardcoded default, with an empty
string treated exactly like an unset value. Duplicated here rather than
imported from ai_assist on purpose: this package sits in the download path
and must not grow a dependency on the AI Assist feature, which is entirely
unrelated to it and optional in a different way.

Every timeout below exists because dynamic discovery is a LAST-RESORT stage.
It only ever runs after the cheap static strategies have already failed, so
its budget is bounded aggressively: the goal is to find the media URL fast
and hand it straight back to the existing download pipeline, never to render
a page thoroughly.
"""
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("AIPipeline")


# Feature switch. Even when true, discovery still self-disables when the
# optional Playwright dependency or its browser binary is missing -- the
# same "degrade silently, never break transcription" pattern diarization
# uses when HF_TOKEN isn't configured.
DEFAULT_ENABLED = True

# Total wall-clock budget for one resolve() call, browser context creation
# and teardown included. A JS app that hasn't revealed a media request
# within this window is not going to.
DEFAULT_TOTAL_DEADLINE_SECONDS = 25.0

# Cap on page.goto() alone. Navigation uses wait_until='commit' (see
# resolver), so this bounds "server responded with headers", not "page
# finished loading" -- deliberately NOT networkidle, which on an analytics-
# heavy app can never arrive.
DEFAULT_NAV_TIMEOUT_SECONDS = 12.0

# How long to keep polling for a media request after navigation commits.
# Bounded separately from the total deadline so a fast-failing page gives up
# early instead of sitting out the full budget.
DEFAULT_SETTLE_SECONDS = 8.0

# A response typed application/octet-stream only counts as media if it is at
# least this large. Guards against treating a small binary blob (a font
# subset, a wasm chunk, a protobuf config) as the audio file.
DEFAULT_MIN_OCTET_STREAM_BYTES = 65536

# Idle period after which the shared Chromium process is released. The
# process is reused across jobs (launching it costs ~0.3-1s), but holding
# ~200MB resident forever on a machine that may only hit a JS site once a
# week is worse than paying that launch again.
DEFAULT_BROWSER_IDLE_SHUTDOWN_SECONDS = 300.0

# Skips the cheap SPA heuristic and always attempts dynamic discovery when
# reached. For testing and for operators who know their sources are all JS
# apps. Off by default: launching a browser for a page that plainly has no
# JS-rendered player is exactly the wasted latency this stage is designed to
# avoid.
DEFAULT_FORCE = False

# Extra Chromium command-line flags, space-separated. Exists because
# containerised deployments commonly need --no-sandbox, which is NOT a
# default here: it disables a real security boundary, and this stage
# executes untrusted third-party JavaScript.
DEFAULT_BROWSER_ARGS = ''

# Whether requests to private/loopback address literals are allowed during
# discovery. Off by default as a partial SSRF mitigation (this stage runs
# untrusted page script). On for a deployment whose media legitimately lives
# on an internal host, and for the package's own local-fixture tests.
DEFAULT_ALLOW_PRIVATE_HOSTS = False

# Which Chromium build to launch. Empty means "decide automatically": try
# Playwright's default for headless (the small chromium-headless-shell
# download), then fall back to channel='chromium' (the full
# Chrome-for-Testing build in its own new headless mode). That fallback
# matters because `playwright install chromium` fetches those as two separate
# downloads and one can fail while the other succeeds.
#
# Set explicitly to pin one: 'chromium' for the full build, or a real branded
# channel ('chrome', 'msedge') for a system browser.
DEFAULT_BROWSER_CHANNEL = ''


def config(name: str, default: Any = None) -> Any:
    """
    Django setting -> environment variable -> default.

    An empty string is treated as absent, so a blank line in .env behaves
    like an omitted one rather than overriding a working default with ''.
    """
    try:
        from django.conf import settings
        if hasattr(settings, name):
            value = getattr(settings, name)
            if value != '' and value is not None:
                return value
    except Exception:
        # Importable outside a configured Django process (tests, scripts).
        pass

    value = os.environ.get(name)
    if value is not None and value != '':
        return value
    return default


def float_config(name: str, default: float) -> float:
    raw = config(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            '[DYNAMIC DISCOVERY] %s=%r is not a number; using %s.', name, raw, default
        )
        return default


def int_config(name: str, default: int) -> int:
    raw = config(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            '[DYNAMIC DISCOVERY] %s=%r is not an integer; using %s.', name, raw, default
        )
        return default


def bool_config(name: str, default: bool) -> bool:
    raw = config(name, None)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def is_enabled() -> bool:
    return bool_config('DYNAMIC_DISCOVERY_ENABLED', DEFAULT_ENABLED)


def force_attempt() -> bool:
    return bool_config('DYNAMIC_DISCOVERY_FORCE', DEFAULT_FORCE)


def total_deadline_seconds() -> float:
    return float_config('DYNAMIC_DISCOVERY_TOTAL_DEADLINE_SECONDS', DEFAULT_TOTAL_DEADLINE_SECONDS)


def nav_timeout_seconds() -> float:
    return float_config('DYNAMIC_DISCOVERY_NAV_TIMEOUT_SECONDS', DEFAULT_NAV_TIMEOUT_SECONDS)


def settle_seconds() -> float:
    return float_config('DYNAMIC_DISCOVERY_SETTLE_SECONDS', DEFAULT_SETTLE_SECONDS)


def min_octet_stream_bytes() -> int:
    return int_config('DYNAMIC_DISCOVERY_MIN_OCTET_STREAM_BYTES', DEFAULT_MIN_OCTET_STREAM_BYTES)


def browser_idle_shutdown_seconds() -> float:
    return float_config(
        'DYNAMIC_DISCOVERY_BROWSER_IDLE_SHUTDOWN_SECONDS', DEFAULT_BROWSER_IDLE_SHUTDOWN_SECONDS
    )


def browser_args() -> list:
    raw = config('DYNAMIC_DISCOVERY_BROWSER_ARGS', DEFAULT_BROWSER_ARGS) or ''
    return [arg for arg in str(raw).split() if arg]


def allow_private_hosts() -> bool:
    return bool_config('DYNAMIC_DISCOVERY_ALLOW_PRIVATE_HOSTS', DEFAULT_ALLOW_PRIVATE_HOSTS)


def browser_channel() -> str:
    return str(config('DYNAMIC_DISCOVERY_BROWSER_CHANNEL', DEFAULT_BROWSER_CHANNEL) or '').strip()
