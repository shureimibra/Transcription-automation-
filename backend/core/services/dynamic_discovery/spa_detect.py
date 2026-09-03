"""
The cheap gate: does this page look like a JavaScript application whose media
only exists after script execution?

This is the single most important performance component in the package. It
runs against HTML the caller has ALREADY fetched (no network request of its
own, no browser), and it is what keeps a browser from ever being launched for
a URL that the existing fast path could handle.

Pure functions only -- no I/O, no imports beyond `re`. That makes the whole
escalation decision unit-testable without Playwright installed.
"""
import re
from typing import List, Tuple

# An empty mount point is the defining signature of a client-rendered app:
# the server sent a container and nothing to put in it. Matches <div id="app">
# </div> / id="root" / id="__next" with only whitespace (or an HTML comment)
# inside. Confirmed live on translation.eaglewebcommerce.com, whose entire
# body is a loader plus `<div id="app"></div>`.
_EMPTY_MOUNT_PATTERN = re.compile(
    r'<div[^>]*\bid=["\'](?:app|root|__next|__nuxt|application)["\'][^>]*>\s*(?:<!--.*?-->\s*)*</div>',
    re.IGNORECASE | re.DOTALL,
)

# ES-module entrypoint. Every modern bundler (Vite, esbuild, Rollup) emits
# this; a server-rendered page almost never needs it for its primary content.
_MODULE_SCRIPT_PATTERN = re.compile(r'<script[^>]*\btype=["\']module["\']', re.IGNORECASE)

# Hashed bundle asset paths, by convention per bundler: Vite/Laravel
# (/build/assets/main-ABC123.js), Next.js (/_next/static/...), CRA
# (/static/js/main.abc123.js), and the bare Vite default (/assets/index-...).
_BUNDLER_ASSET_PATTERN = re.compile(
    r'(?:/build/assets/|/_next/static/|/static/js/main\.|/assets/index-|/dist/assets/)',
    re.IGNORECASE,
)

# Client-side router configuration handed to the app as inline JSON. A strong
# signal that the path in the URL is a FRONTEND route, i.e. the server returns
# the same shell whatever the path is.
_ROUTER_CONFIG_PATTERN = re.compile(
    r'\b(?:routerBase|basename|__NUXT__|__NEXT_DATA__|window\.__[A-Z_]+__\s*=)',
)

# Framework hydration markers.
_HYDRATION_PATTERN = re.compile(
    r'\b(?:data-server-rendered|data-reactroot|ng-version|data-svelte)\b', re.IGNORECASE,
)

_SCRIPT_STYLE_BLOCK = re.compile(r'<(script|style)\b.*?</\1>', re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r'<[^>]+>')

# Below this much visible text, a page is a shell rather than a document. A
# real article/lesson page with a player carries far more than this even when
# terse.
_TINY_BODY_CHAR_LIMIT = 400

# Two independent signals required. One alone is too weak: plenty of
# conventional server-rendered pages ship a module script for progressive
# enhancement, and plenty of SPAs pre-render enough text to clear the body
# threshold. Requiring two keeps false escalations rare, which is the whole
# point of this gate.
_MIN_SIGNALS = 2


def visible_text_length(html: str) -> int:
    """Rough count of rendered characters, ignoring script/style content."""
    without_code = _SCRIPT_STYLE_BLOCK.sub(' ', html or '')
    text = _TAG.sub(' ', without_code)
    return len(' '.join(text.split()))


def looks_like_js_app(html: str) -> Tuple[bool, List[str]]:
    """
    Returns (verdict, signals). `signals` names every marker that matched, so
    the escalation decision is explainable in the log rather than a bare
    boolean -- which matters when tuning this against real sites later.

    Empty/missing HTML returns False: with no evidence at all, the correct
    action is NOT to spend a browser launch. A page that couldn't even be
    fetched statically is a known blind spot (a browser might still render
    it) and is documented as such rather than silently escalated.
    """
    if not html:
        return False, []

    signals = []
    if _EMPTY_MOUNT_PATTERN.search(html):
        signals.append('empty-mount')
    if _MODULE_SCRIPT_PATTERN.search(html):
        signals.append('module-script')
    if _BUNDLER_ASSET_PATTERN.search(html):
        signals.append('bundler-asset')
    if _ROUTER_CONFIG_PATTERN.search(html):
        signals.append('router-config')
    if visible_text_length(html) < _TINY_BODY_CHAR_LIMIT:
        signals.append('tiny-body')

    # A hydration marker means the server DID render content -- so whatever
    # is missing probably isn't hidden behind script execution. Treated as
    # evidence against escalating, not merely absent evidence for it.
    if _HYDRATION_PATTERN.search(html):
        return False, signals + ['server-rendered']

    return len(signals) >= _MIN_SIGNALS, signals
