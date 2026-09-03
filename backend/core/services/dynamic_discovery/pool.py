"""
The shared browser process.

Two requirements pull in opposite directions here:

  * "Reuse browser infrastructure rather than launching a brand-new Chromium
    process for every request" -- a cold launch costs roughly 0.3-1s, which
    is a large fraction of this stage's entire budget.
  * "Keep browser contexts isolated between users/jobs" -- one job must never
    see another job's cookies or storage.

Both are satisfiable at once, because those are different objects: the
BROWSER PROCESS is shared and long-lived, while each job gets its own
BrowserContext, which is Playwright's isolation boundary (separate cookie
jar, storage, and cache) and costs milliseconds to create and destroy.

The wrinkle is threading. Playwright's sync API is thread-affine: the driver
and every object descended from it may only be touched by the thread that
created it. This application runs each job on its own short-lived
threading.Thread (see tasks.run_transcription_pipeline), so a thread-local
browser would be launched and thrown away for every single job -- no reuse at
all -- and a plain module-level singleton would be an outright API violation.

So this module owns ONE dedicated daemon thread. It is the only thread that
ever speaks to Playwright; job threads hand it callables and wait on a
Future. That serializes dynamic discovery, which is acceptable and arguably
desirable: this is a rare last-resort fallback, and one browser at a time
bounds the memory a burst of difficult URLs can cost.
"""
import asyncio
import logging
import queue
import sys
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Optional

from . import config
from .errors import DiscoveryUnavailable, DynamicDiscoveryError

logger = logging.getLogger("AIPipeline")

# Flags chosen purely to cut startup cost and irrelevant work. Notably NOT
# here: --no-sandbox. It disables a real security boundary and this stage
# executes untrusted third-party JavaScript; containerised deployments that
# genuinely need it can add it via DYNAMIC_DISCOVERY_BROWSER_ARGS.
_LAUNCH_ARGS = [
    '--disable-dev-shm-usage',
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-sync',
    '--disable-translate',
    '--disable-features=Translate,MediaRouter',
    '--no-first-run',
    '--no-default-browser-check',
    '--metrics-recording-only',
    '--mute-audio',
    # Images are also aborted at the route layer; this stops the renderer
    # even allocating for them.
    '--blink-settings=imagesEnabled=false',
    # Load-bearing, not a convenience: many players only issue their media
    # request once playback starts. Without this, a headless page can sit
    # forever having fetched nothing, and the resolver's attempt to call
    # play() on the element would be rejected by the autoplay policy.
    '--autoplay-policy=no-user-gesture-required',
]


def playwright_available() -> bool:
    """
    Whether the optional dependency is importable. Does not prove a browser
    binary is installed -- that only shows up at launch, which raises
    DiscoveryUnavailable and is handled the same way (feature off, download
    continues down its other paths).
    """
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


class _BrowserPool:
    """Owns the dedicated Playwright thread and the shared browser process."""

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._playwright = None
        self._browser = None
        # Which Chromium build actually launched here. None until the first
        # successful launch; '' means "Playwright's default for headless",
        # 'chromium' means the full Chrome-for-Testing build. Remembered so
        # the fallback below is paid for at most once per process.
        self._channel: Optional[str] = None

    # -- called from job threads -------------------------------------------

    def submit(self, work: Callable[[Any], Any], timeout: float) -> Any:
        """
        Runs `work(browser)` on the owner thread and returns its result.

        Raises whatever `work` raised, DiscoveryUnavailable when Playwright
        or a browser binary is missing, or DynamicDiscoveryError if the owner
        thread doesn't answer within `timeout`.
        """
        if not playwright_available():
            raise DiscoveryUnavailable(
                'Playwright is not installed; dynamic discovery is unavailable.'
            )

        self._ensure_thread()
        future: Future = Future()
        self._queue.put((work, future))
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            # The owner thread is still inside this job. It enforces its own
            # internal deadline (see resolver), so it will finish shortly and
            # the next submit simply queues behind it. Nothing to kill from
            # here: touching Playwright objects off-thread is exactly what
            # this design exists to prevent.
            raise DynamicDiscoveryError(
                f'Dynamic discovery did not complete within {timeout:.0f}s.'
            )

    # -- owner thread ------------------------------------------------------

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name='dynamic-discovery-browser', daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=config.browser_idle_shutdown_seconds())
            except queue.Empty:
                # Nothing for a while: hand the ~200MB back. The thread stays
                # alive (it costs nothing) so the next job skips thread
                # startup and only pays for the browser launch.
                self._release_browser('idle timeout')
                continue

            work, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                import os
                old_val = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
                os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
                try:
                    future.set_result(work(self._ensure_browser()))
                finally:
                    if old_val is None:
                        os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
                    else:
                        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = old_val
            except BaseException as exc:  # noqa: BLE001 -- relayed to the caller
                future.set_exception(exc)
                self._release_browser_if_broken()

    def _ensure_playwright(self):
        if self._playwright is not None:
            return self._playwright
        try:
            # pyrefly: ignore [missing-import]
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise DiscoveryUnavailable(f'Playwright import failed: {exc}') from exc
        # Daphne configures WindowsSelectorEventLoopPolicy for its own
        # compatibility.  That loop deliberately has no subprocess support,
        # while Playwright starts its Node driver in a subprocess.  Switch the
        # *policy only while Playwright creates its private loop*, then restore
        # Daphne's policy immediately; the already-created Playwright loop is
        # unaffected by the restore.
        previous_policy = None
        if sys.platform == 'win32' and isinstance(
            asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy,
        ):
            previous_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            self._playwright = sync_playwright().start()
        except BaseException as exc:
            # start() spawns the Node driver as a subprocess and sets up an
            # event loop for it. Both can fail for environmental reasons that
            # have nothing to do with the browser -- and some of those raise
            # with an EMPTY message (NotImplementedError from asyncio
            # subprocess support on Windows being the classic one), so the
            # exception TYPE is the only diagnostic. Always include it.
            raise DiscoveryUnavailable(
                f'Playwright driver failed to start: {type(exc).__name__}: {exc or "(no message)"}'
            ) from exc
        finally:
            if previous_policy is not None:
                asyncio.set_event_loop_policy(previous_policy)
        return self._playwright

    def _launch_channels(self) -> list:
        """
        Chromium builds to try, in order.

        `playwright install chromium` fetches TWO separate downloads: the full
        Chrome-for-Testing build, and a much smaller `chromium-headless-shell`.
        Since Playwright 1.49, `chromium.launch(headless=True)` resolves to the
        SHELL by default -- so if only one of the two downloads succeeded, the
        launch fails with "Executable doesn't exist" even though a perfectly
        good browser is sitting on disk.

        `channel='chromium'` is Playwright's documented way to select the full
        build and run it in Chromium's own new headless mode. It needs no extra
        download and no separate dependency.

        ORDER: full build first, default shell second.
        
        The original design preferred the shell (smaller, faster to start) and
        fell back to the full build. That was inverted deliberately, because on
        the machines this runs on the shell is the download that is MISSING:
        a direct probe here shows `channel=default` failing with "Executable
        doesn't exist" while `channel='chromium'` launches fine. Trying the
        shell first therefore paid for a guaranteed failed launch -- which is
        slow -- before every single discovery.
        
        The fallback is retained, so a machine with only the shell installed
        still works; only the preference changed. An explicit
        DYNAMIC_DISCOVERY_BROWSER_CHANNEL overrides the whole dance -- including
        for a real branded channel like 'chrome' or 'msedge', which is why this
        is a free-text setting rather than a boolean.
        """
        configured = config.browser_channel()
        if configured:
            return [configured]
        if self._channel is not None:
            return [self._channel]
        return ['chromium', '']

    def _ensure_browser(self):
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._browser
            except Exception:
                pass
            self._release_browser('stale handle')

        playwright = self._ensure_playwright()
        args = _LAUNCH_ARGS + config.browser_args()
        channels = self._launch_channels()
        last_error: Optional[Exception] = None

        for channel in channels:
            options: dict = {'headless': True, 'args': args}
            if channel:
                options['channel'] = channel
            try:
                self._browser = playwright.chromium.launch(**options)
            except Exception as exc:
                last_error = exc
                logger.info(
                    '[DYNAMIC DISCOVERY] Chromium launch failed for channel %r: %s',
                    channel or 'default', exc,
                )
                continue
            self._channel = channel
            logger.info(
                '[DYNAMIC DISCOVERY] Launched shared headless Chromium (channel=%s).',
                channel or 'default',
            )
            return self._browser

        # Every candidate failed. Overwhelmingly the "you never ran
        # `playwright install chromium`" case. Reported as unavailable, not as
        # a download failure.
        raise DiscoveryUnavailable(
            f'Could not launch headless Chromium: {last_error}'
        ) from last_error

    def _release_browser_if_broken(self) -> None:
        if self._browser is None:
            return
        try:
            if self._browser.is_connected():
                return
        except Exception:
            pass
        self._release_browser('unusable after error')

    def _release_browser(self, reason: str) -> None:
        """Owner-thread only."""
        if self._browser is None and self._playwright is None:
            return
        for closer, label in ((self._browser, 'browser'), (self._playwright, 'driver')):
            if closer is None:
                continue
            try:
                closer.close() if label == 'browser' else closer.stop()
            except Exception as exc:
                logger.warning('[DYNAMIC DISCOVERY] Error releasing %s: %s', label, exc)
        if self._browser is not None:
            logger.info('[DYNAMIC DISCOVERY] Released headless Chromium (%s).', reason)
        self._browser = None
        self._playwright = None


POOL = _BrowserPool()
