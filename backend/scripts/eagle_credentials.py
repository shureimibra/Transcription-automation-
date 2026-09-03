"""
The one place the Eagle Web session credential is read.

WHY THIS EXISTS: eight benchmark and probe scripts each carried a live
`translatepro_session` cookie, an `XSRF-TOKEN` and a `cf_clearance` value
pasted in as string literals. None of them were tracked by git, but none of
them were ignored either -- a single `git add -A` would have committed live
session credentials for a client's production system to the repository.

So the literal moves out of source entirely and into the environment, which
is where every other credential in this project already lives (see
GEMINI_API_KEY in .env.example). The scripts keep working; what changes is
that the secret is no longer a thing a commit can capture.

Usage:

    from scripts.eagle_credentials import eagle_cookie, eagle_headers

    headers = eagle_headers(referer=task_url)

A missing value raises rather than silently sending an unauthenticated
request, because the failure mode of the latter is a login-redirect HTML
page being parsed as if it were media -- which wastes a download and
produces a confusing error far from its cause.
"""
import os

ENV_VAR = 'EAGLE_COOKIE'

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


class EagleCredentialsMissing(RuntimeError):
    """Raised when EAGLE_COOKIE is not set."""


def eagle_cookie(required: bool = True) -> str:
    """
    The raw Cookie header value for translation.eaglewebcommerce.com.

    Read from the environment every call rather than cached at import, so a
    rotated session takes effect without restarting a long-running shell.
    """
    value = (os.environ.get(ENV_VAR) or '').strip()
    if not value and required:
        raise EagleCredentialsMissing(
            f'{ENV_VAR} is not set. These scripts need a live Eagle Web '
            f'session.\n\n'
            f'  1. Sign in to translation.eaglewebcommerce.com in a browser.\n'
            f'  2. Copy the full Cookie request header from DevTools.\n'
            f'  3. Put it in backend/.env as {ENV_VAR}=..., or export it.\n\n'
            f'Never paste it into a source file: this directory is not '
            f'gitignored, and a session cookie in a commit is a credential '
            f'leak that outlives the session.'
        )
    return value


def eagle_headers(referer: str = '', accept: str = '*/*', **extra) -> dict:
    """The standard header set these scripts send. `extra` overrides."""
    headers = {
        'Cookie': eagle_cookie(),
        'User-Agent': DEFAULT_USER_AGENT,
        'Accept': accept,
    }
    if referer:
        headers['Referer'] = referer
    headers.update(extra)
    return headers
