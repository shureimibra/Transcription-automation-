"""
Staged verification of the authenticated-SPA handoff, boundary by boundary.

The point is NOT "can Playwright open the page". It is to prove each link in
the chain separately, so a failure names its own stage instead of collapsing
into "dynamic discovery failed":

    A browser launch          F authenticated source request
    B authentication          G media response/body
    C SPA load                H FFmpeg input
    D task API                I FFmpeg extraction
    E source discovery        J handoff through the real product entry point

Phase 1 traces the page with the product's OWN route rules, so the trace
cannot drift from what discovery actually does, and reports the second at
which each milestone happened -- that measurement is how
DYNAMIC_DISCOVERY_SETTLE_SECONDS gets set from evidence instead of guessed.
Phases 2-4 then run the real resolver, the real download/extract path, and
finally the real AudioExtractor._try_dynamic_discovery, in that order.

Usage (PowerShell), from backend/:

    $env:EAGLE_COOKIE = '<session cookie>'
    $env:DYNAMIC_DISCOVERY_ALLOW_PRIVATE_HOSTS = 'false'
    ..\.venv\Scripts\python.exe scripts\verify_eagle_handoff.py
    ..\.venv\Scripts\python.exe scripts\verify_eagle_handoff.py https://host/tasks/4001

The cookie is read from the environment and never printed, logged, or written
to disk by this script. Treat any cookie that has appeared in a terminal
transcript as compromised and refresh it.

Exit code 0 only if every stage that ran passed.
"""
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transcription_proj.settings')

import django
django.setup()

from core.services import dynamic_discovery
from core.services.ai_pipeline import AudioExtractor, MediaDiscovery
from core.services.dynamic_discovery import config as dd_config
from core.services.dynamic_discovery.candidates import (
    BLOCKED_RESOURCE_TYPES, classify_content_type, is_blob_url,
)
from core.services.dynamic_discovery.pool import POOL
from core.services.dynamic_discovery.resolver import _cookies_for

DEFAULT_URL = 'https://translation.eaglewebcommerce.com/tasks/3927'
URL = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

# Deliberately longer than the product's settle window. Phase 1 is a
# MEASUREMENT: we need to see when the source request lands, including if that
# is later than the product currently waits. Phases 2-4 use the real config.
TRACE_SECONDS = float(os.environ.get('EAGLE_TRACE_SECONDS', '35'))

_failures = []


def stage(letter: str, name: str, ok: bool, detail: str = '') -> bool:
    mark = 'PASS' if ok else 'FAIL'
    print(f'  [{mark}] {letter}. {name}{" -- " + detail if detail else ""}')
    if not ok:
        _failures.append(f'{letter}. {name}' + (f' -- {detail}' if detail else ''))
    return ok


def _safe_unlink(p: Path) -> None:
    for _ in range(10):
        try:
            if p.exists():
                p.unlink()
            return
        except Exception:
            time.sleep(0.5)


def cookie_header() -> str:
    value = os.environ.get('EAGLE_COOKIE') or ''
    if not value.strip():
        print('ABORT: set EAGLE_COOKIE to the authenticated session cookie first.')
        print('       (PowerShell:  $env:EAGLE_COOKIE = \'name=value; other=value\')')
        raise SystemExit(3)
    return value


def trace(browser, url: str, cookie: str) -> dict:
    """
    Phase 1. Watches one authenticated page load and records when each
    milestone happens. Uses the product's own BLOCKED_RESOURCE_TYPES so this
    trace cannot disagree with what discovery really does -- notably that
    stylesheets are allowed, because Vite makes lazily-imported CSS a required
    dependency of the JS import and aborting it strands the app on its
    loading screen.
    """
    host = urlparse(url).hostname or ''
    seen = {
        'started': time.monotonic(),
        'auth_refused': [],      # (status, path) for 401/403
        'login_redirect': None,
        'app_scripts': 0,
        'task_api': None,        # (status, seconds, path)
        'media': [],             # (seconds, content_type, url)
        'console_errors': [],
        'final_url': '',
        'body_text': '',
    }

    def elapsed() -> float:
        return time.monotonic() - seen['started']

    context = browser.new_context(viewport={'width': 1280, 'height': 720})
    try:
        context.add_cookies(_cookies_for(url, cookie))
        page = context.new_page()

        page.on('console', lambda m: (
            seen['console_errors'].append(m.text[:200]) if m.type == 'error' else None
        ))
        page.on('pageerror', lambda e: seen['console_errors'].append(f'pageerror: {str(e)[:200]}'))

        def on_route(route):
            request = route.request
            try:
                if request.resource_type in BLOCKED_RESOURCE_TYPES:
                    route.abort()
                    return
                if request.resource_type == 'media':
                    # Same capture-then-abort as the product: zero audio bytes.
                    seen['media'].append((elapsed(), 'resource_type=media', request.url))
                    route.abort()
                    return
                route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        page.route('**/*', on_route)

        def on_response(response):
            try:
                path = urlparse(response.url).path
                status = response.status
                resource = response.request.resource_type
                headers = {str(k).lower(): v for k, v in (response.headers or {}).items()}
                content_type = (headers.get('content-type') or '').split(';')[0].strip().lower()

                same_host = (urlparse(response.url).hostname or '') == host
                # Only the TARGET host's refusals say anything about our
                # session. A third-party widget answering 403 is its own
                # problem and must not read as "authentication failed".
                if status in (401, 403) and same_host:
                    seen['auth_refused'].append((status, path))
                if status in (301, 302, 303, 307, 308) and same_host:
                    location = headers.get('location') or ''
                    if 'login' in location.lower() or 'signin' in location.lower():
                        seen['login_redirect'] = location
                if resource == 'script' and (urlparse(response.url).hostname or '') == host:
                    seen['app_scripts'] += 1
                if '/api/' in path and 'task' in path and seen['task_api'] is None:
                    seen['task_api'] = (status, elapsed(), path)
                if classify_content_type(content_type) and not is_blob_url(response.url):
                    seen['media'].append((elapsed(), content_type, response.url))
            except Exception:
                pass

        page.on('response', on_response)

        page.goto(url, wait_until='commit', timeout=15_000)

        # Poll rather than one long sleep, so we can stop as soon as real
        # media has been seen -- no reason to sit out the full window once the
        # answer is in hand. Deliberately does NOT stop on octet-stream: a
        # wasm chunk or protobuf config arrives early and untyped, and cutting
        # the window short on one would report "no audio found" for a page
        # that was about to request it.
        deadline = time.monotonic() + TRACE_SECONDS
        while time.monotonic() < deadline:
            if any(entry[1].startswith(('audio/', 'video/')) for entry in seen['media']):
                break
            page.wait_for_timeout(200)

        try:
            seen['final_url'] = page.url
            seen['body_text'] = (page.locator('body').inner_text() or '')[:200].replace('\n', ' | ')
        except Exception:
            pass
        return seen
    finally:
        context.close()


def main() -> int:
    cookie = cookie_header()
    print(f'URL: {URL}')
    print(f'Cookie: present ({len(cookie.split(";"))} pair(s)); value never printed')
    print(f'Config: settle={dd_config.settle_seconds()}s '
          f'nav={dd_config.nav_timeout_seconds()}s '
          f'deadline={dd_config.total_deadline_seconds()}s '
          f'trace_window={TRACE_SECONDS}s')

    # ---- A: browser launch ------------------------------------------------
    print('\nPhase 1 -- authenticated runtime trace')
    if not dynamic_discovery.is_available():
        stage('A', 'browser launch', False, 'dynamic discovery unavailable (playwright/config)')
        return 1
    try:
        version = POOL.submit(lambda browser: browser.version, timeout=180)
    except Exception as exc:
        stage('A', 'browser launch', False, f'{type(exc).__name__}: {exc or "(no message)"}')
        return 1
    stage('A', 'browser launch', True, f'chromium {version}')

    try:
        seen = POOL.submit(lambda browser: trace(browser, URL, cookie),
                           timeout=TRACE_SECONDS + 60)
    except Exception as exc:
        stage('C', 'SPA load', False, f'trace raised {type(exc).__name__}: {exc or "(no message)"}')
        return 1

    # ---- B: authentication ------------------------------------------------
    stage('B', 'authentication', not seen['auth_refused'] and not seen['login_redirect'],
          f'refused={seen["auth_refused"]} login_redirect={seen["login_redirect"]}'
          if (seen['auth_refused'] or seen['login_redirect']) else 'no 401/403, no login redirect')

    # ---- C: SPA load ------------------------------------------------------
    stage('C', 'SPA load', seen['app_scripts'] > 0,
          f'{seen["app_scripts"]} first-party script response(s); body="{seen["body_text"]}"')
    if seen['console_errors']:
        for message in seen['console_errors'][:5]:
            print(f'         console: {message}')

    # ---- D: task API ------------------------------------------------------
    if seen['task_api']:
        status, at, path = seen['task_api']
        stage('D', 'task API reached', 200 <= status < 300, f'{status} {path} at {at:.2f}s')
    else:
        stage('D', 'task API reached', False, 'no /api/**task** response observed')

    # ---- E: source discovery ---------------------------------------------
    audio = [entry for entry in seen['media'] if entry[1].startswith('audio/')]
    other = [entry for entry in seen['media'] if not entry[1].startswith('audio/')]
    if audio:
        at, content_type, media_url = audio[0]
        stage('E', 'source discovered', True, f'{content_type} at {at:.2f}s -> {media_url}')
        if at > dd_config.settle_seconds():
            print(f'         NOTE: appeared at {at:.2f}s but the product settle window is '
                  f'{dd_config.settle_seconds()}s. Raise '
                  f'DYNAMIC_DISCOVERY_SETTLE_SECONDS to at least {at + 3:.0f}.')
    else:
        stage('E', 'source discovered', False,
              f'no audio/* response in {TRACE_SECONDS}s; other media-ish: {other[:3]}')

    # Only E actually blocks the handoff. B/C/D are diagnostics that explain
    # WHY E failed -- if the source was found anyway, keep going and prove the
    # rest of the chain rather than stopping on a noisy earlier signal.
    if not audio:
        print('\nStopping before the handoff: no media source to hand off.')
        report()
        return 1

    # ---- Phase 2: the real resolver --------------------------------------
    print('\nPhase 2 -- product resolver (dynamic_discovery.resolve)')
    started = time.monotonic()
    try:
        resolved = dynamic_discovery.resolve(URL, extra_headers={'Cookie': cookie},
                                             user_agent=None)
    except Exception as exc:
        stage('E', 'resolver agrees with trace', False,
              f'{type(exc).__name__}: {exc or "(no message)"}')
        report()
        return 1
    if resolved is None:
        stage('E', 'resolver agrees with trace', False,
              f'resolver returned None after {time.monotonic() - started:.2f}s '
              f'(trace saw the source, so this is the settle window -- see NOTE above)')
        report()
        return 1
    stage('E', 'resolver agrees with trace', True,
          f'{resolved.describe()} in {time.monotonic() - started:.2f}s')
    stage('E', 'resolved URL is fetchable (not blob:)',
          resolved.url.startswith(('http://', 'https://')), resolved.url)

    # ---- Phase 3: classification + FFmpeg --------------------------------
    print('\nPhase 3 -- handoff into the existing download/FFmpeg path')
    merged = dict(resolved.headers)
    merged['Cookie'] = cookie

    kind = MediaDiscovery.classify(resolved.url, extra_headers=merged,
                                   content_type_hint=resolved.content_type or None)
    stage('F', 'discovered URL classifies as media', kind in ('direct_file', 'stream_manifest'),
          f"classify -> '{kind}' (content_type_hint={resolved.content_type or 'none'})")

    wav = Path(__file__).resolve().parent.parent / 'eagle-handoff-check.wav'
    extracted = False
    try:
        started = time.monotonic()
        ok = AudioExtractor.download_and_extract_url(
            resolved.url, str(wav),
            extra_headers=merged,
            _dynamic_depth=1,
            content_type_hint=resolved.content_type or None,
        )
        took = time.monotonic() - started
        size = wav.stat().st_size if wav.exists() else 0
        stage('G', 'authenticated media retrieved', ok, f'download+extract returned {ok} in {took:.2f}s')
        stage('H', 'FFmpeg received an input', size > 0 or ok, f'wav bytes={size}')
        # 16kHz mono 16-bit PCM is 32000 bytes/second; anything under a second
        # of audio means FFmpeg produced a header and little else.
        extracted = size > 32000
        stage('I', 'FFmpeg extraction produced real audio', extracted,
              f'{size} bytes (~{size / 32000:.1f}s at 16kHz mono s16)')
    finally:
        _safe_unlink(wav)

    if not extracted:
        print('\nStopping before the end-to-end run: FFmpeg produced no usable audio.')
        report()
        return 1

    # ---- Phase 4: the real product entry point ---------------------------
    print('\nPhase 4 -- real entry point (AudioExtractor._try_dynamic_discovery)')
    wav2 = Path(__file__).resolve().parent.parent / 'eagle-endtoend-check.wav'
    try:
        started = time.monotonic()
        end_to_end = AudioExtractor._try_dynamic_discovery(
            URL, str(wav2),
            page_html='<div id="app"></div><script type="module" src="/assets/index-abc.js"></script>',
            extra_headers={'Cookie': cookie},
        )
        size = wav2.stat().st_size if wav2.exists() else 0
        stage('J', 'generic dynamic discovery handoff', end_to_end and size > 32000,
              f'returned {end_to_end}, wav bytes={size}, {time.monotonic() - started:.2f}s')
    finally:
        _safe_unlink(wav2)

    # ---- Phase 5: the new Eagle-specific fast-path -----------------------
    print('\nPhase 5 -- Eagle fast-path (resolve_eagle_task & download_and_extract_url)')
    stage('K', 'Eagle URL recognition', dynamic_discovery.is_eagle_task_url(URL),
          f'parse_eagle_task_id -> {dynamic_discovery.parse_eagle_task_id(URL)}')

    started = time.monotonic()
    eagle_res = dynamic_discovery.resolve_eagle_task(URL, extra_headers={'Cookie': cookie})
    fast_took = time.monotonic() - started
    stage('L', 'Eagle fast-path resolver', eagle_res is not None,
          f'{eagle_res.describe() if eagle_res else "None"} in {fast_took:.2f}s')

    if eagle_res:
        stage('M', 'Eagle fast-path MIME type', eagle_res.content_type == 'audio/mpeg',
              f'content_type={eagle_res.content_type}')

    # Test full direct extraction at depth 0 using the fast path
    wav3 = Path(__file__).resolve().parent.parent / 'eagle-fastpath-check.wav'
    try:
        started = time.monotonic()
        fast_ok = AudioExtractor.download_and_extract_url(
            URL, str(wav3), extra_headers={'Cookie': cookie}
        )
        fast_total_took = time.monotonic() - started
        size3 = wav3.stat().st_size if wav3.exists() else 0
        stage('N', 'Eagle direct pipeline extraction', fast_ok and size3 > 32000,
              f'returned {fast_ok}, wav bytes={size3} in {fast_total_took:.2f}s')
    finally:
        _safe_unlink(wav3)

    report()
    return 1 if _failures else 0


def report() -> None:
    print()
    if _failures:
        print('FAILED BOUNDARIES:')
        for failure in _failures:
            print(f'  - {failure}')
    else:
        print('ALL BOUNDARIES PASSED -- discovery resolves, the authenticated source '
              'downloads, and FFmpeg produced a 16kHz WAV the existing pipeline can '
              'hand straight to faster-whisper.')


if __name__ == '__main__':
    sys.exit(main())
