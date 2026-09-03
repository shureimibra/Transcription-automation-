"""
Cheapest possible proof that dynamic discovery's browser layer works on this
machine, plus the environment facts needed to explain it if it doesn't.

No network, no Django server, no transcription pipeline: import Playwright,
report what's on disk and what asyncio looks like, launch through the real
pool, render a trivial data: URL, print the version, close.

Run from backend/:
    ../.venv/Scripts/python.exe scripts/dd_healthcheck.py

Exit code 0 means the browser layer is usable. On failure it prints the
exception TYPE and a full traceback -- several failures here carry an empty
message, so the type is the only diagnostic that identifies them.
"""
import asyncio
import os
import platform
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def report_environment() -> None:
    """
    The facts that distinguish "no browser" from "this process can't spawn one".

    The asyncio lines matter more than they look. Playwright's sync API runs
    the Node driver as a SUBPROCESS on an event loop it creates itself via
    asyncio.new_event_loop(), which obeys the global policy. On Windows, a
    SelectorEventLoop does not implement subprocess support and raises a
    NotImplementedError with NO message -- indistinguishable from "something
    broke" unless you know which loop you got.
    """
    print(f'      platform: {sys.platform} / {platform.platform()}')
    print(f'      python: {sys.version.split()[0]}')
    print(f'      thread: {threading.current_thread().name} '
          f'(main={threading.current_thread() is threading.main_thread()})')
    policy = asyncio.get_event_loop_policy()
    print(f'      asyncio policy: {type(policy).__module__}.{type(policy).__name__}')
    loop = asyncio.new_event_loop()
    try:
        print(f'      asyncio new_event_loop -> {type(loop).__name__}')
        print(f'      subprocess support: {"yes" if hasattr(loop, "subprocess_exec") else "no"}')
    finally:
        loop.close()

    browsers_dir = os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or os.path.join(
        os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ms-playwright'
    )
    print(f'      browsers dir: {browsers_dir}')
    try:
        for entry in sorted(os.listdir(browsers_dir)):
            print(f'        - {entry}')
    except Exception as exc:
        print(f'        <unreadable: {exc}>')


def setup_django() -> None:
    """
    Load Django settings if they load at all.

    Not decoration: config() prefers a Django setting over the environment, so
    a value set in settings.py is invisible to a bare script. Importing the
    settings module also runs whatever it does at import time -- which is
    exactly the environment the Daphne process has and a bare script does not,
    and "passes standalone but fails under Daphne" is the difference this
    script exists to catch. Failure here is non-fatal; the browser layer does
    not need Django.
    """
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transcription_proj.settings')
    try:
        import django
        django.setup()
        print('ok    django settings loaded')
    except Exception as exc:
        print(f'warn  django not loaded ({type(exc).__name__}: {exc}); '
              f'continuing with env-only config')


def main() -> int:
    setup_django()

    # 1. Import Playwright.
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        import playwright as _pw
    except Exception:
        print('FAIL  import playwright')
        traceback.print_exc()
        return 1
    print(f'ok    import playwright {getattr(_pw, "__version__", "?")}')

    # 2. Environment + installed browser builds.
    report_environment()

    # 3. Which executable a default headless launch would resolve to. Since
    #    Playwright 1.49 that is the separate chromium-headless-shell download,
    #    which can be absent while the full build is present.
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            print(f'      default headless executable: {path}')
            print(f'      ...exists: {os.path.exists(path)}')
    except Exception as exc:
        print(f'      could not resolve default executable: {type(exc).__name__}: {exc}')

    # 4-6. Launch through the REAL pool, so this exercises the same code path
    #      the pipeline uses -- channel fallback included -- rather than a
    #      parallel implementation that could pass while the product fails.
    from core.services.dynamic_discovery.pool import POOL

    def probe(browser):
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto('data:text/html,<h1>OK</h1>', wait_until='commit')
            return browser.version, page.text_content('h1')
        finally:
            context.close()

    try:
        version, heading = POOL.submit(probe, timeout=180)
    except Exception as exc:
        print(f'FAIL  launch/render: {type(exc).__name__}: {exc or "(no message)"}')
        traceback.print_exc()
        cause = exc.__cause__
        while cause is not None:
            print(f'      caused by: {type(cause).__name__}: {cause or "(no message)"}')
            cause = cause.__cause__
        return 1

    print(f'ok    launched chromium version={version}')
    print(f'ok    rendered data: URL, <h1> = {heading!r}')
    if heading != 'OK':
        print('FAIL  page did not render the expected content')
        return 1

    # Release the browser rather than waiting out the idle timeout.
    try:
        POOL.submit(lambda browser: browser.close(), timeout=60)
        print('ok    browser closed')
    except Exception as exc:
        print(f'warn  close: {type(exc).__name__}: {exc}')

    print('\nHEALTHCHECK PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
