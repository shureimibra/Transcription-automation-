"""
HTTP clients for the two Eagle products.

EVERY CONTRACT HERE WAS OBSERVED, NOT ASSUMED. The endpoints, field names,
status vocabulary and payload shapes below come from driving a real account
read-only during the Phase 4 research pass. Where something is still
unverified it is marked and it raises rather than guessing.

NO DJANGO IN THIS FILE. It takes a cookie header as a string and returns
plain dataclasses, so it can be exercised without a database and substituted
in tests. The Django-side wiring -- reading the encrypted credential,
recording health -- lives in apps/workforce/eagle_impl.py.

THE TWO PRODUCTS ARE DIFFERENT APPLICATIONS. Transcription is server-rendered
Laravel with a DataTables JSON endpoint and a public media path; translation
is a Vue SPA on a REST API whose media requires the session. They share a
domain and nothing else, so they get separate clients rather than one class
full of `if product ==`.
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger('eagle')

TRANSCRIBE_HOST = 'https://transcribe.eaglewebcommerce.com'
TRANSLATE_HOST = 'https://translation.eaglewebcommerce.com'

#: The main Eagle site -- NOT a product. Translation's own /api/auth/login
#: rejects this account with "Please sign in through the main Eagle
#: system"; the real, browser-confirmed flow authenticates HERE first, then
#: bridges across. See TranslationClient.login() for the full sequence.
EAGLE_ROOT_HOST = 'https://eaglewebcommerce.com'

#: cf_clearance is bound to the user-agent that earned it. A mismatch gets a
#: Cloudflare challenge instead of JSON, so this must stay stable and must
#: match whatever browser the operator captured the cookie from.
# MATCH THE BROWSER THE COOKIE CAME FROM.
#
# Cloudflare binds `cf_clearance` to the IP *and* the user agent that earned
# it. Presenting somebody's clearance token with a different user agent is
# exactly what the binding exists to catch, so a hardcoded version string
# that drifts away from the operator's real browser turns every poll into a
# request that looks like token theft.
#
# Set EAGLE_USER_AGENT to the exact string from the browser you copied the
# cookie out of (devtools -> Network -> any request -> User-Agent).
USER_AGENT = os.environ.get('EAGLE_USER_AGENT') or (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
)

DEFAULT_TIMEOUT = 30


#: Eagle names languages for humans -- "English (USA)", "French" -- while our
#: Task stores a CODE in a 10-character field that feeds the ASR's language
#: hint. Widening the column would be the wrong fix: it would put a display
#: name where the pipeline expects `en`, and the hint would silently stop
#: working. So the display name is mapped here and kept verbatim in `raw`.
#:
#: An unrecognised language maps to '' -- auto-detect -- because guessing
#: wrongly is worse than letting the model decide.
_LANGUAGE_CODES = {
    'english': 'en', 'french': 'fr', 'spanish': 'es', 'german': 'de',
    'italian': 'it', 'portuguese': 'pt', 'dutch': 'nl', 'russian': 'ru',
    'arabic': 'ar', 'chinese': 'zh', 'mandarin': 'zh', 'japanese': 'ja',
    'korean': 'ko', 'hindi': 'hi', 'swahili': 'sw', 'turkish': 'tr',
    'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
    'finnish': 'fi', 'greek': 'el', 'hebrew': 'he', 'thai': 'th',
    'vietnamese': 'vi', 'indonesian': 'id', 'ukrainian': 'uk',
    'romanian': 'ro', 'czech': 'cs', 'hungarian': 'hu',
}


def language_code(display: str) -> str:
    """`"English (USA)"` -> `"en"`. Unknown -> `''` (auto-detect)."""
    if not display:
        return ''
    base = re.split(r'[(\-/,]', str(display))[0].strip().lower()
    return _LANGUAGE_CODES.get(base, '')


#: Eagle's translation product identifies languages by opaque numeric id and
#: exposes NO endpoint that names them -- /api/languages, /api/categories and
#: four other guesses all 404.
#:
#: THIS MAP IS INFERRED, NOT VERIFIED. Every task observed is `1 -> 3`, and
#: the one accepted submission on record was English -> French. That is good
#: evidence and it is still an inference, so it lives here as configuration
#: rather than being scattered through the code as a fact.
#:
#: An unmapped id yields '' and the pipeline will NOT guess a target
#: language: translating into the wrong one is a guaranteed rejection and a
#: wasted purchase, so the submission is blocked instead.
EAGLE_LANGUAGE_IDS = {
    1: 'en',
    3: 'fr',
}


class EagleHTTPError(Exception):
    """Transport or status failure. Carries no cookie material."""

    def __init__(self, detail: str, status: int = 0):
        self.detail = detail
        self.status = status
        super().__init__(detail)


class EagleSessionExpired(EagleHTTPError):
    """
    The platform no longer recognises the session.

    Its own category because the response shapes are misleading: translation
    answers `HTTP 200 {"user": null}` when logged out, and transcription
    redirects to the marketing site. A naive "did it 200?" check reads both
    as success -- which is exactly the mistake the first research probe made.
    """


class EagleLoginRejected(EagleHTTPError):
    """
    A real username/password login attempt did not produce a session.

    Its own category for the same reason EagleSessionExpired has one: the
    caller (apps/workforce/eagle_impl.py) needs to tell "we tried to log in
    and Eagle said no" apart from an ordinary transport failure, so it can
    record the failure against the STORED CREDENTIAL (for the cooldown)
    rather than merely against the session.
    """


@dataclass
class EagleAccount:
    label: str = ''
    minutes_balance: Optional[int] = None
    raw: Dict = field(default_factory=dict)


@dataclass
class RawTask:
    """One task as the platform describes it, before our normalisation."""
    external_id: str
    product: str
    status: str = ''
    title: str = ''
    language: str = ''
    target_language: str = ''
    duration_seconds: Optional[float] = None
    minutes_required: Optional[int] = None
    payout: Optional[float] = None
    media_url: str = ''
    media_needs_session: bool = True
    page_url: str = ''
    claimed: bool = False
    deadline_at: str = ''
    #: The platform's project / category label -- "Law class", "Speech",
    #: "Business Translation". It DECIDES THE TRANSCRIPTION STANDARD, so it
    #: is promoted to a field of its own rather than left in `raw`.
    project: str = ''
    raw: Dict = field(default_factory=dict)


#: Where the project label might live. Eagle's rows are not documented and
#: the two products do not share a schema, so this tries the plausible names
#: rather than betting on one.
#: 'type', 'task_type' and 'job_type' ARE DELIBERATELY ABSENT, having been
#: here first. Live rows carry BOTH `project='Lectures'` and
#: `type='Speaker Identification'`, which are not two names for one thing:
#: the first is the subject and decides the verbatim standard, the second is
#: what kind of work the task is. Treating them as interchangeable means
#: that a row missing `project` would yield 'Speaker Identification' as its
#: project label, match nothing in PROJECT_STANDARDS, and fall back to the
#: default -- while the code looked like it had found an answer.
#:
#: A missing label is better than a confident wrong one: it is reported, and
#: the interface marks the standard `assumed`.
_PROJECT_KEYS = (
    'project', 'project_name', 'category', 'category_name',
    'niche', 'subject',
)

#: Keys we have already reported, so the log below fires once per shape
#: rather than once per task.
_SEEN_ROW_SHAPES = set()


def project_from_row(row: Dict, product: str = '') -> str:
    """
    The project label, from whichever key this platform happens to use.

    WHY THIS IS DEFENSIVE RATHER THAN A SINGLE `row['project']`. The label
    decides whether a transcript is edited or strict verbatim, and getting
    that wrong produces a document that is wrong in every line rather than
    in a few. But no transcription task existed on the platform when this
    was written, so the key name could not be confirmed -- and guessing one
    name would fail silently, which is the worst available outcome.

    So: try the plausible keys, accept a nested `{"name": ...}` shape, and
    LOG THE WHOLE KEY LIST the first time an unfamiliar row arrives. The
    next real task tells us the answer in the log rather than in a
    rejection.
    """
    # WHEN SEVERAL KEYS DISAGREE, SAY SO INSTEAD OF PICKING QUIETLY.
    #
    # This walks the candidates in order and takes the first, which is only
    # right if the others say the same thing. The key list above is an
    # educated guess at an undocumented schema, so "several of these exist
    # and they differ" is a real possibility and one nothing would otherwise
    # report.
    #
    # It matters because this label chooses between edited and strict
    # verbatim, which are opposites: the wrong one produces a document that
    # is wrong in every line rather than in a few places. Collect all of
    # them, return the first as before, and log the disagreement once per
    # shape so a human can settle which field the platform actually means.
    labels = []
    numeric = None
    for key in _PROJECT_KEYS:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get('name') or value.get('title')
        if isinstance(value, str) and value.strip():
            labels.append((key, value.strip()))
        # A BARE ID IS NOT A LABEL, and must not be treated as one.
        # `project: 3` would sail through a str() and then match nothing in
        # PROJECT_STANDARDS, so the standard would silently fall back to the
        # default while the code looked like it had found the answer.
        # Remember it for the log instead: an id means there is a lookup
        # table somewhere that we still need.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = numeric or (key, value)

    if labels:
        distinct = {text for _, text in labels}
        if len(distinct) > 1:
            shape = (product, 'conflict', tuple(sorted(distinct)))
            if shape not in _SEEN_ROW_SHAPES:
                _SEEN_ROW_SHAPES.add(shape)
                logger.warning(
                    '[EAGLE] %s rows carry MORE THAN ONE project label and they '
                    'disagree: %s. Using %r because %r is tried first. This '
                    'label picks edited vs strict verbatim, so confirm which '
                    'field the platform actually shows on the task page.',
                    product,
                    ', '.join(f'{k}={v!r}' for k, v in labels),
                    labels[0][1], labels[0][0],
                )
        return labels[0][1]

    if numeric:
        shape = (product, 'numeric', numeric[0])
        if shape not in _SEEN_ROW_SHAPES:
            _SEEN_ROW_SHAPES.add(shape)
            logger.warning(
                '[EAGLE] %s rows carry %s=%r -- an id, not a name. The project '
                'label needs a lookup before the transcription standard can be '
                'resolved from it.',
                product or 'task', numeric[0], numeric[1],
            )
        return ''

    # Nothing matched. Say so once, with the keys that WERE there, so the
    # list above can be corrected from evidence instead of from guesswork.
    shape = (product, tuple(sorted(row.keys())))
    if row and shape not in _SEEN_ROW_SHAPES:
        _SEEN_ROW_SHAPES.add(shape)
        logger.warning(
            '[EAGLE] %s rows carry no recognised project label; keys are: %s. '
            'The transcription standard will fall back to the default until '
            'one of these is added to _PROJECT_KEYS.',
            product or 'task', ', '.join(sorted(row.keys())),
        )
    return ''


def _request(url: str, cookie: str, method: str = 'GET', body: Optional[bytes] = None,
             headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT,
             allow_redirects: bool = True):
    """
    One outbound call. Returns (status, final_url, set_cookies, body_bytes).

    Uses urllib rather than requests to keep this importable in any context;
    the payloads are small JSON and the media path is handled separately by
    the streaming download below.

    THE THIRD ELEMENT IS THE Set-Cookie LIST, not a header dict. It used to
    be `dict(response.headers)`, which is wrong twice over: nothing ever
    read it, and a dict silently keeps only the LAST Set-Cookie when a
    response rotates several at once. Laravel reissues its session cookie
    periodically, and a client that never reads the new value keeps
    presenting the old one until the server stops recognising it -- a
    session that dies for no visible reason.
    """
    hdrs = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/html;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if cookie:
        hdrs['Cookie'] = cookie
    if headers:
        hdrs.update(headers)

    request = urllib.request.Request(url, data=body, headers=hdrs, method=method)

    opener_args = []
    if not allow_redirects:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        opener_args.append(_NoRedirect())
    opener = urllib.request.build_opener(*opener_args)

    def _cookies(message):
        # get_all preserves duplicates; a plain dict() would not.
        if message is None:
            return []
        try:
            return list(message.get_all('Set-Cookie') or [])
        except AttributeError:
            return []

    try:
        with opener.open(request, timeout=timeout) as response:
            return (response.status, response.geturl(),
                    _cookies(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, url, _cookies(exc.headers), exc.read()
    except Exception as exc:
        raise EagleHTTPError(f'{type(exc).__name__}: {exc}') from exc


def _request_first_hop(url: str, cookie: str, method: str = 'GET',
                        body: Optional[bytes] = None,
                        headers: Optional[Dict[str, str]] = None,
                        timeout: int = DEFAULT_TIMEOUT):
    """
    Like `_request()`, but never follows a redirect and returns the FIRST
    hop's own `Location` instead of collapsing the chain into a final URL.

    WHY THIS EXISTS SEPARATELY, RATHER THAN `_request(..., allow_redirects=
    False)`. That flag already suppresses following the redirect (confirmed
    against the installed CPython `urllib.request.HTTPRedirectHandler`
    source: a 302 with redirects disabled surfaces as `urllib.error.
    HTTPError`, which `_request()` already catches) -- but `_request()`'s
    4-tuple return (`status, final_url, set_cookies, body`) has nowhere to
    put the `Location` header at all. In that branch `final_url` is just
    the ORIGINAL request URL again, not the redirect target -- useless for
    telling the two shapes below apart. Changing `_request()`'s signature
    to add it would touch every one of this file's dozen call sites for
    one caller's need. This is that one caller's own narrow tool instead.

    Returns (status, location_header_or_empty_string, set_cookies, body).
    `location` is the RAW header value (may carry a query string) --
    callers that only need the path should extract it themselves, the way
    TranscriptionClient.login() does, so nothing here decides what is safe
    to log on a caller's behalf.
    """
    hdrs = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/html;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if cookie:
        hdrs['Cookie'] = cookie
    if headers:
        hdrs.update(headers)

    request = urllib.request.Request(url, data=body, headers=hdrs, method=method)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener(_NoRedirect())

    def _cookies(message):
        if message is None:
            return []
        try:
            return list(message.get_all('Set-Cookie') or [])
        except AttributeError:
            return []

    try:
        with opener.open(request, timeout=timeout) as response:
            return (response.status, response.headers.get('Location') or '',
                    _cookies(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.headers.get('Location') or '',
                _cookies(exc.headers), exc.read())
    except Exception as exc:
        raise EagleHTTPError(f'{type(exc).__name__}: {exc}') from exc


def merge_set_cookies(cookie_header: str, set_cookies) -> str:
    """
    Fold any reissued cookies back into the header we send next time.

    WHY THIS MATTERS MORE THAN IT LOOKS. The session cookie is the whole
    credential. When Laravel rotates it -- which it does on its own
    schedule, not only at login -- a client that keeps sending the previous
    value is presenting a session the server has moved on from. It works
    until it abruptly does not, with nothing in between to warn anyone, and
    the operator sees "session expired" on a session they never left.

    Only the NAMES already present are updated, plus any new ones. Cookie
    attributes (Path, Expires, HttpOnly, SameSite) are dropped: we are
    rebuilding a request header, not a cookie jar, and a stray attribute in
    a Cookie header is malformed.
    """
    if not set_cookies:
        return cookie_header

    jar = {}
    for part in (cookie_header or '').split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        name, _, value = part.partition('=')
        jar[name.strip()] = value.strip()

    changed = False
    for raw in set_cookies:
        first = (raw or '').split(';', 1)[0].strip()
        if '=' not in first:
            continue
        name, _, value = first.partition('=')
        name, value = name.strip(), value.strip()
        if not name:
            continue
        # A deletion looks like `name=; Expires=<past>`; treat an empty
        # value as "leave what we have" rather than blanking the session.
        if not value:
            continue
        if jar.get(name) != value:
            jar[name] = value
            changed = True

    if not changed:
        return cookie_header
    return '; '.join(f'{k}={v}' for k, v in jar.items())


#: Best-effort scrape of Laravel's flashed validation error off a re-rendered
#: login page. NEVER the sole signal for success/failure -- the landing path
#: is (see TranscriptionClient.login) -- this only makes a EagleLoginRejected
#: say something more useful than "redirected back to /login" when Laravel
#: does render one of these two common containers. Finding nothing is not an
#: error; it just falls back to a generic message.
def _scrape_login_error(html: str) -> str:
    for pattern in (
        r'<div[^>]*class="[^"]*invalid-feedback[^"]*"[^>]*>\s*([^<]+)',
        r'<div[^>]*class="[^"]*alert-danger[^"]*"[^>]*>\s*([^<]+)',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1).strip()
    return ''


# ===================================================================== #
# Translation -- Vue SPA on a REST API
# ===================================================================== #

def xsrf_header(cookie_header: str) -> dict:
    """
    Laravel's CSRF echo, as a header.

    HOW LARAVEL'S PROTECTION WORKS. It sets an `XSRF-TOKEN` cookie and
    expects any state-changing request to send that same value back in an
    `X-XSRF-TOKEN` header. Sending the cookie alone is not enough -- a
    cookie travels automatically, which is precisely what CSRF exploits, so
    the header is the part that proves the caller could read it.

    THE VALUE IS URL-ENCODED IN THE COOKIE and must be decoded for the
    header. Laravel writes `...%3D%3D`; sending that verbatim fails exactly
    the same way as sending nothing.

    Without this, every read worked and every write returned 419 "CSRF token
    mismatch" -- which is why this went unnoticed until the first live
    claim. Polling, listing and downloading are all GETs.
    """
    for part in (cookie_header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name.strip() == 'XSRF-TOKEN' and value:
            return {'X-XSRF-TOKEN': urllib.parse.unquote(value.strip())}
    return {}


class TranslationClient:
    product = 'translation'

    def __init__(self, cookie_header: str, host: str = TRANSLATE_HOST):
        self.cookie = cookie_header or ''
        # True once a response reissued a cookie, so the caller can
        # persist the new value rather than let it die with the
        # process.
        self.cookie_rotated = False
        self.host = host.rstrip('/')


    def _absorb(self, set_cookies) -> None:
        """
        Fold a reissued cookie into the one we send next time.

        The session cookie IS the credential. Laravel rotates it on its own
        schedule, not only at login, and a client that keeps replaying the
        previous value is presenting a session the server has moved past.
        It works until it abruptly does not, which is what "session expired
        on a session I never left" looks like from the outside.
        """
        merged = merge_set_cookies(self.cookie, set_cookies)
        if merged != self.cookie:
            self.cookie = merged
            self.cookie_rotated = True
            logger.info('[EAGLE] %s reissued a session cookie; adopted it',
                        self.product)

    def _req(self, url: str, *args, **kwargs):
        """`_request` bound to this client's cookie, absorbing rotations."""
        status, final_url, cookies, raw = _request(url, self.cookie, *args, **kwargs)
        self._absorb(cookies)
        return status, final_url, cookies, raw

    def _json(self, path: str, method: str = 'GET', payload: Optional[dict] = None) -> dict:
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        headers = {'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if method.upper() not in ('GET', 'HEAD'):
            # Laravel refuses any write without the token echoed back in a
            # header. Harmless on a read, so it is applied by method rather
            # than remembered at each call site.
            headers.update(xsrf_header(self.cookie))
        status, _, _, raw = self._req(f'{self.host}{path}',
                                     method=method, body=body, headers=headers)
        if status == 401:
            raise EagleSessionExpired('Translation session rejected (401).', status)
        if status >= 400:
            raise EagleHTTPError(f'{path} returned {status}: {raw[:200]!r}', status)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception as exc:
            raise EagleHTTPError(f'{path} returned non-JSON: {exc}', status) from exc

    def whoami(self) -> EagleAccount:
        """
        Identity and, crucially, the minutes balance.

        THE LOGGED-OUT SHAPE IS A 200. `/api/auth/user` answers
        `{"user": null}` with HTTP 200 when the session has lapsed, so this
        checks the payload, not the status code.
        """
        data = self._json('/api/auth/user')
        user = (data or {}).get('user')
        if not user:
            raise EagleSessionExpired(
                'Translation session is not authenticated (user: null).', 200)
        return EagleAccount(
            label=str(user.get('name') or ''),
            minutes_balance=user.get('minutes_balance'),
            raw={k: user.get(k) for k in ('id', 'role', 'minutes_balance', 'wallet_balance')},
        )

    def login(self, email: str, password: str) -> None:
        """
        Authenticate via the main Eagle site's SSO bridge, replacing
        whatever cookie this client is currently holding.

        WHY NOT A DIRECT POST TO THIS HOST'S OWN /api/auth/login -- that
        was the original, reasonable-looking implementation, read
        verbatim out of the SPA bundle's own login() action. It is wrong
        for this account: production confirmed it is rejected with
        "Please sign in through the main Eagle system". A real browser
        capture (DevTools, Network tab, Preserve log, through the actual
        login) proved the legitimate sequence instead:

            GET  https://eaglewebcommerce.com/login            (root, CSRF)
            POST https://eaglewebcommerce.com/login             (root credentials)
            GET  https://eaglewebcommerce.com/user/service/translation
                 -> 302 Location: https://translation.eaglewebcommerce.com/?accessToken=...
            GET  <that Location>                                (consumes the token)
                 -> sets THIS host's own session cookie
            GET  /api/auth/user                                 (must independently confirm)

        Nothing in this SPA's own bundle references `/user/service/translation`
        at all -- it is a ROOT Eagle route, not a Translation one, which is
        exactly why the earlier bundle-only reverse-engineering never found
        it.

        THE ROOT SESSION IS LOCAL AND NEVER TOUCHES self.cookie. `root_cookie`
        exists only for the three root-domain requests below and is folded
        via `merge_set_cookies` (not overwritten) after each one, so a
        Set-Cookie on ANY of the three -- not just the last -- survives, the
        same discipline TranscriptionClient.login() already applies to its
        own single host. It is discarded when this method returns; only a
        confirmed-working Translation cookie is ever persisted by the
        caller (EagleService.record_login_success), and that persistence
        already happens inside the SAME advisory-locked critical section
        this whole method runs in -- see EagleService._locked.

        THE BRIDGE'S REDIRECT IS VALIDATED BEFORE BEING FOLLOWED, ON PURPOSE.
        Blindly following any Location header this response happened to
        carry would mean a compromised or misbehaving root endpoint could
        redirect this server to fetch an arbitrary host with credentials-
        adjacent context. The origin is checked with a real (scheme, host)
        comparison, not a string-prefix check -- a prefix check would let
        "https://translation.eaglewebcommerce.com.evil.example/" through,
        since it starts with the right string without being the right host.

        THE accessToken VALUE IS NEVER LOGGED, STORED, OR PUT IN AN
        EXCEPTION. Only its PRESENCE is checked. The one place its value is
        used is the single GET that consumes it, and even there only the
        landing request's PATH (never its query string) is ever logged.

        A 200 ON THE TOKEN-LANDING REQUEST IS NOT SUCCESS BY ITSELF. That
        request returns the SPA shell (HTML), not a JSON body with a `user`
        field the way the old direct-login response had -- there is nothing
        in a 200 alone to trust. This method makes its own independent
        `GET /api/auth/user` check before returning, rather than leaving
        that entirely to whichever caller happens to verify afterwards (not
        every _guard() retry does -- only check_session's does, today).
        """
        root_cookie = ''

        # -- 1: root GET /login -- fresh CSRF token + root session cookies -- #
        status, _, set_cookies, raw = _request_first_hop(f'{EAGLE_ROOT_HOST}/login', root_cookie)
        root_cookie = merge_set_cookies(root_cookie, set_cookies)
        if status >= 400:
            raise EagleLoginRejected(
                f'Could not load the main Eagle login page ({status}).', status)
        html = raw.decode('utf-8', 'replace')
        match = re.search(r'name="_token"\s+value="([^"]+)"', html)
        if not match:
            raise EagleLoginRejected('No CSRF token on the main Eagle login page.', status)
        token = match.group(1)

        # -- 2: root POST /login -- the actual credential check -- #
        body = urllib.parse.urlencode({
            'email': email, 'password': password, '_token': token,
        }).encode('utf-8')
        status, location, set_cookies, raw = _request_first_hop(
            f'{EAGLE_ROOT_HOST}/login', root_cookie, method='POST', body=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        root_cookie = merge_set_cookies(root_cookie, set_cookies)
        if status >= 500:
            raise EagleLoginRejected(f'Main Eagle login POST returned {status}.', status)
        if status not in (301, 302, 303, 307, 308) or not location:
            detail = _scrape_login_error(raw.decode('utf-8', 'replace'))
            raise EagleLoginRejected(
                detail or f'Main Eagle login did not redirect ({status}).', status)
        root_landing_path = urllib.parse.urlsplit(location).path.rstrip('/')
        if root_landing_path == '/login':
            detail = _scrape_login_error(raw.decode('utf-8', 'replace'))
            raise EagleLoginRejected(detail or 'Eagle rejected these credentials.', status)
        logger.info('[EAGLE] %s: main Eagle login succeeded, redirected to %s',
                   self.product, root_landing_path)

        # -- 3: the SSO bridge -- authenticated root session, ask for a #
        # -- Translation handoff -- #
        status, bridge_location, set_cookies, _ = _request_first_hop(
            f'{EAGLE_ROOT_HOST}/user/service/translation', root_cookie)
        root_cookie = merge_set_cookies(root_cookie, set_cookies)
        if status in (401, 403):
            raise EagleLoginRejected(
                f'Main Eagle rejected the Translation SSO bridge ({status}).', status)
        if status not in (301, 302, 303, 307, 308) or not bridge_location:
            raise EagleLoginRejected(
                f'Translation SSO bridge did not redirect as expected ({status}).', status)

        # -- Validate the bridge's redirect BEFORE following it. -- #
        bridge_target = urllib.parse.urlsplit(bridge_location)
        expected = urllib.parse.urlsplit(self.host)
        if (bridge_target.scheme, bridge_target.netloc) != (expected.scheme, expected.netloc):
            raise EagleLoginRejected(
                'Translation SSO bridge redirected to an unexpected host.', status)
        access_token = (urllib.parse.parse_qs(bridge_target.query).get('accessToken') or [''])[0]
        if not access_token:
            raise EagleLoginRejected(
                'Translation SSO bridge redirect carried no accessToken.', status)

        # -- 4: consume the token. self.cookie, not root_cookie -- this -- #
        # -- request goes to a DIFFERENT host and must never carry the -- #
        # -- root domain's session. -- #
        status, _, set_cookies, _ = _request_first_hop(bridge_location, self.cookie)
        if status >= 400:
            raise EagleLoginRejected(
                f'Translation did not accept the SSO handoff ({status}).', status)
        self._absorb(set_cookies)
        if not self.cookie_rotated:
            raise EagleLoginRejected(
                'The SSO handoff completed but Translation issued no session cookie.', status)
        self.cookie_rotated = False
        logger.info('[EAGLE] %s: SSO handoff landed at %s',
                   self.product, bridge_target.path or '/')

        # -- 5: independently confirm -- a 200 on the landing request is -- #
        # -- not proof by itself; only a real, authenticated /api/auth/user -- #
        # -- is. -- #
        try:
            self.whoami()
        except EagleHTTPError as exc:
            # Covers EagleSessionExpired (a subclass -- the 401/null-user
            # shapes) and any other transport failure on the verification
            # call alike: either way, the handoff did not produce a
            # working session.
            raise EagleLoginRejected(
                f'SSO handoff completed but the session did not verify: {exc.detail}',
                exc.status) from exc

        logger.info('[EAGLE] %s login succeeded', self.product)

    def list_available(self, max_pages: int = 10) -> List[RawTask]:
        """Every page of `/api/user/tasks`, normalised."""
        tasks, page = [], 1
        while page <= max_pages:
            data = self._json(f'/api/user/tasks?page={page}')
            rows = data.get('data') or []
            for row in rows:
                tasks.append(self._normalise(row))
            if not data.get('next_page_url'):
                break
            page += 1
        return tasks

    def list_mine(self) -> List[RawTask]:
        data = self._json('/api/user/tasks/awarded')
        rows = data.get('data') if isinstance(data, dict) else data
        return [self._normalise(r) for r in (rows or [])]

    def get_task(self, external_id: str) -> RawTask:
        data = self._json(f'/api/user/tasks/{external_id}')
        row = data.get('task') if isinstance(data, dict) and 'task' in data else data
        return self._normalise(row)

    def claim(self, external_id: str) -> dict:
        """
        Claim. SPENDS MINUTES and starts the one-hour deadline.

        Returns `{minutes_balance, task}` -- the platform hands back the new
        balance, which is why the caller should persist it rather than
        guessing.
        """
        return self._json(f'/api/user/tasks/{external_id}/claim', method='POST', payload={})

    def submit(self, external_id: str, translated_text: str) -> dict:
        return self._json(
            f'/api/user/tasks/{external_id}/submit', method='POST',
            payload={'translated_text': translated_text},
        )

    def media_url(self, external_id: str) -> str:
        return f'{self.host}/api/user/tasks/{external_id}/source'

    def _normalise(self, row: dict) -> RawTask:
        return RawTask(
            external_id=str(row.get('id')),
            product=self.product,
            status=str(row.get('status') or ''),
            title=str(row.get('file_name') or ''),
            language=EAGLE_LANGUAGE_IDS.get(row.get('source_language_id'), ''),
            target_language=EAGLE_LANGUAGE_IDS.get(row.get('target_language_id'), ''),
            duration_seconds=(float(row['duration_minutes']) * 60
                              if row.get('duration_minutes') else None),
            minutes_required=row.get('minutes_required'),
            payout=(float(row['payout_amount']) if row.get('payout_amount') else None),
            media_url=self.media_url(row.get('id')),
            project=project_from_row(row, self.product),
            # Verified: the source endpoint requires the session.
            media_needs_session=True,
            page_url=f'{self.host}/tasks/{row.get("id")}',
            claimed=bool(row.get('user_id')),
            deadline_at=str(row.get('deadline_at') or ''),
            raw=row,
        )


# ===================================================================== #
# Transcription -- Laravel + DataTables
# ===================================================================== #

#: The dashboard's "Available Minutes" stat card:
#:
#:   <p ...>Available Minutes</p>
#:   <h2 class="mb-0"><span class="number-font-chars">9</span></h2>
#:
#: "Available Balance" right beside it is money ($), and "Total Minutes
#: Awarded/Used" are lifetime counters -- this anchors on the exact label.
_AVAILABLE_MINUTES_RE = re.compile(
    r'Available Minutes\s*</p>.*?number-font-chars"[^>]*>\s*([\d,]+)',
    re.S | re.I,
)


def _parse_available_minutes(html: str) -> Optional[int]:
    """
    Pull the whole-minute balance out of the transcription dashboard.

    None on any miss -- absent, markup changed, not a number. Unknown is the
    safe direction: it lets the platform decide affordability at claim time,
    exactly as before this was wired at all.
    """
    match = _AVAILABLE_MINUTES_RE.search(html or '')
    if not match:
        return None
    try:
        return int(match.group(1).replace(',', ''))
    except ValueError:
        return None


class TranscriptionClient:
    product = 'transcription'

    def __init__(self, cookie_header: str, host: str = TRANSCRIBE_HOST):
        self.cookie = cookie_header or ''
        # True once a response reissued a cookie, so the caller can
        # persist the new value rather than let it die with the
        # process.
        self.cookie_rotated = False
        self.host = host.rstrip('/')


    def _absorb(self, set_cookies) -> None:
        """
        Fold a reissued cookie into the one we send next time.

        The session cookie IS the credential. Laravel rotates it on its own
        schedule, not only at login, and a client that keeps replaying the
        previous value is presenting a session the server has moved past.
        It works until it abruptly does not, which is what "session expired
        on a session I never left" looks like from the outside.
        """
        merged = merge_set_cookies(self.cookie, set_cookies)
        if merged != self.cookie:
            self.cookie = merged
            self.cookie_rotated = True
            logger.info('[EAGLE] %s reissued a session cookie; adopted it',
                        self.product)

    def _req(self, url: str, *args, **kwargs):
        """`_request` bound to this client's cookie, absorbing rotations."""
        status, final_url, cookies, raw = _request(url, self.cookie, *args, **kwargs)
        self._absorb(cookies)
        return status, final_url, cookies, raw

    def whoami(self) -> EagleAccount:
        """
        No identity API here, so this reads the panel.

        THE LOGGED-OUT SHAPE IS USUALLY A REDIRECT to the marketing site, not
        a 401 -- so the primary check is where it landed, not the status
        code. BUT A FLAT 403 IS ALSO A STALE-SESSION SHAPE, confirmed live on
        2026-09-10: the transcription session sat dead for 10+ hours with
        this endpoint returning 403 (never the redirect), which routed every
        failure into EagleHTTPError -- a category _guard() never retries --
        instead of EagleSessionExpired, the one that triggers automatic
        re-login. A real re-login was then confirmed to fix it immediately.
        Scoped to exactly 403, not every >=400: that is the shape that was
        actually observed and verified, not a guess extended to cover
        others there is no evidence for.
        """
        status, final_url, _, raw = self._req(f'{self.host}/user/stt/tasks')
        if status == 403:
            raise EagleSessionExpired(
                f'Transcription panel returned 403 (stale session).', status)
        if status >= 400:
            raise EagleHTTPError(f'transcription panel returned {status}', status)
        if 'transcribe.eaglewebcommerce.com' not in final_url:
            raise EagleSessionExpired(
                f'Transcription session redirected to {final_url}.', status)
        return EagleAccount(
            label='Eagle Transcribe',
            minutes_balance=self._available_minutes(),
        )

    def _available_minutes(self) -> Optional[int]:
        """
        The balance, from the dashboard's "Available Minutes" card -- it is
        not on the task panel whoami() checks, so this is a second request.

        BEST EFFORT, DELIBERATELY. The session was already confirmed above;
        a balance this cannot read (dashboard down, markup moved) is None,
        never a reason to fail the whole check. None means "unknown", which
        lets the platform decide affordability at claim time -- the same
        behaviour as before transcription reported a balance at all.
        """
        try:
            status, final_url, _, raw = self._req(f'{self.host}/user/dashboard')
        except EagleHTTPError:
            return None
        if status != 200 or 'transcribe.eaglewebcommerce.com' not in final_url:
            return None
        return _parse_available_minutes(raw.decode('utf-8', 'replace'))

    def login(self, email: str, password: str) -> None:
        """
        Authenticate directly, replacing whatever cookie this client is
        currently holding.

        THE CONTRACT BELOW WAS READ OFF THE LIVE FORM, NOT GUESSED: a GET
        to /login hands out a fresh `_token` (Laravel CSRF) alongside the
        XSRF-TOKEN/eagletranscribe_session cookies a plain page load always
        sets; the POST that follows carries all three, form-encoded.

        `remember` IS DELIBERATELY OMITTED. It is a plain, optional
        checkbox on the live form -- nothing requires it, and this logs in
        fresh each time rather than asking Eagle to extend anything.

        THE `recaptcha` FIELD IS DELIBERATELY NOT SENT EITHER. It exists as
        a hidden input in the markup, but no reCAPTCHA script loads
        anywhere on the page or in custom.js -- it looks vestigial, not
        wired to anything. Inventing a value for a security control would
        be guessing at solving it; if Eagle's server genuinely enforces it
        despite the missing widget, the POST below fails and says so
        (see _scrape_login_error) rather than pretending to have solved it.

        SUCCESS/FAILURE READS THE FIRST REDIRECT'S PATH, NOT ANY LATER HOP.
        Laravel's AuthenticatesUsers trait redirects a rejected attempt
        back to /login with flashed errors; a successful one redirects
        anywhere else. Nothing here needs to know the exact successful
        destination in advance -- only that it is not /login.

        THE POST DELIBERATELY DOES NOT LET urllib AUTO-FOLLOW THE
        REDIRECT (see _request_first_hop). Laravel's Auth::login() calls
        session()->regenerate() as standard session-fixation protection,
        which typically issues the freshly-authenticated cookie ON THE
        REDIRECT RESPONSE ITSELF -- not on whatever page it points at. A
        client that lets urllib auto-follow (`_req`'s default) only ever
        sees the FINAL response after the whole chain; the intermediate
        redirect's own Set-Cookie is invisible to it, and the follow-up
        request urllib makes on our behalf replays the OLD, pre-login
        cookie rather than the new one -- so a login Eagle genuinely
        accepted could still end up looking rejected if that stale cookie
        then fails whatever the destination page checks. Reading the first
        hop directly closes that gap: we see -- and adopt -- whatever
        cookie Eagle actually issued at the moment it decided the
        credentials were good, not whatever survives an unobserved second
        hop.
        """
        status, _, _, raw = self._req(f'{self.host}/login')
        if status >= 400:
            raise EagleLoginRejected(f'Could not load the login page ({status}).', status)
        html = raw.decode('utf-8', 'replace')
        match = re.search(r'name="_token"\s+value="([^"]+)"', html)
        if not match:
            raise EagleLoginRejected('No CSRF token on the transcription login page.', status)
        token = match.group(1)

        body = urllib.parse.urlencode({
            'email': email, 'password': password, '_token': token,
        }).encode('utf-8')
        status, location, set_cookies, raw = _request_first_hop(
            f'{self.host}/login', self.cookie, method='POST', body=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )

        # Absorb whatever cookie THIS response reissued -- exactly the one
        # `_req()` would have thrown away by auto-following the redirect.
        # Never logs a value; _absorb()'s own log line ("reissued a
        # session cookie; adopted it") already doesn't either.
        previous_cookie = self.cookie
        self._absorb(set_cookies)
        cookie_changed = self.cookie != previous_cookie

        redirect_path = ''
        if location:
            # Path only -- a query string on the Location could carry a
            # token or other value that has no business in a log line.
            redirect_path = urllib.parse.urlsplit(location).path.rstrip('/') or '/'

        logger.info(
            '[EAGLE] %s login POST: status=%s is_redirect=%s '
            'redirect_path=%s cookie_reissued=%s cookie_changed=%s',
            self.product, status, status in (301, 302, 303, 307, 308),
            redirect_path or '(none)', bool(set_cookies), cookie_changed,
        )

        if status >= 500:
            raise EagleLoginRejected(f'Login POST returned {status}.', status)

        if status in (301, 302, 303, 307, 308):
            if not location:
                raise EagleLoginRejected(
                    f'Login POST redirected ({status}) with no Location header.', status)
            if redirect_path == '/login':
                detail = _scrape_login_error(raw.decode('utf-8', 'replace'))
                raise EagleLoginRejected(detail or 'Eagle rejected these credentials.', status)
            logger.info('[EAGLE] %s login succeeded, redirected to %s',
                       self.product, redirect_path)
            return

        # Laravel's login route always redirects, success or failure --
        # confirmed on the live form (see the class docstring's research
        # note). A non-redirect response here is a shape nothing has
        # observed before; raising rather than guessing which way to read
        # it matches this file's own rule everywhere else.
        if status >= 400:
            detail = _scrape_login_error(raw.decode('utf-8', 'replace'))
            raise EagleLoginRejected(
                detail or f'Login POST returned {status} with no redirect.', status)
        raise EagleLoginRejected(
            f'Unexpected login response: {status} with no redirect. '
            'This shape has not been observed before and is not safe to '
            'treat as success.', status)

    def list_available(self, length: int = 100) -> List[RawTask]:
        """
        The DataTables server-side endpoint the task table itself calls.

        `draw` is DataTables' request counter; the platform echoes it back
        and it has no meaning to us beyond being required.
        """
        query = urllib.parse.urlencode({'draw': 1, 'start': 0, 'length': length})
        status, final_url, _, raw = self._req(f'{self.host}/user/stt/tasks?{query}',
            headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
        )
        if 'transcribe.eaglewebcommerce.com' not in final_url:
            raise EagleSessionExpired('Transcription session redirected.', status)
        if status >= 400:
            raise EagleHTTPError(f'task list returned {status}', status)
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception as exc:
            raise EagleSessionExpired(
                f'Task list returned HTML rather than JSON ({exc}).', status) from exc
        return [self._normalise(r) for r in (data.get('data') or [])]

    def list_mine(self, length: int = 100) -> List[RawTask]:
        """
        Work this account holds or has delivered -- Eagle's "Awarded Tasks".

        THE AUTHORITY ON WHAT IS STILL OURS. `get_submit_token` looked like
        it answered that question and does not: it returns a form token for
        anything, so three tasks whose deadlines had lapsed still produced
        one. This endpoint is the real answer -- a task missing from here has
        been reclaimed by the platform.

        It is also where a REVIEW OUTCOME arrives. The row carries the
        status, the display label Eagle shows its own users
        (`custom-status`), the money, and the text we submitted.
        """
        query = urllib.parse.urlencode({'draw': 1, 'start': 0, 'length': length})
        status, final_url, _, raw = self._req(
            f'{self.host}/user/stt/awards?{query}',
            headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
        )
        if 'transcribe.eaglewebcommerce.com' not in final_url:
            raise EagleSessionExpired('Transcription session redirected.', status)
        if status >= 400:
            raise EagleHTTPError(f'awarded list returned {status}', status)
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception as exc:
            raise EagleSessionExpired(
                f'Awarded list returned HTML rather than JSON ({exc}).', status) from exc
        return [self._normalise(r) for r in (data.get('data') or [])]

    def get_submit_token(self, external_id: str) -> str:
        """
        The Laravel CSRF token for the submit form, scraped from the task
        page. Fetched per task rather than cached because whether it is
        stable per session is UNVERIFIED, and a stale token fails the
        submission -- which costs the task.
        """
        status, _, _, raw = self._req(f'{self.host}/user/stt/task/{external_id}/show')
        if status >= 400:
            raise EagleHTTPError(f'task page returned {status}', status)
        html = raw.decode('utf-8', 'replace')
        match = re.search(r'name="_token"\s+value="([^"]+)"', html)
        if not match:
            raise EagleSessionExpired('No CSRF token on the task page.', status)
        return match.group(1)

    def claim(self, external_id: str) -> bool:
        """Accept. A plain authenticated GET; spends minutes, starts the clock."""
        status, final_url, _, _ = self._req(f'{self.host}/user/stt/task/{external_id}/accept')
        if 'transcribe.eaglewebcommerce.com' not in final_url:
            raise EagleSessionExpired('Session redirected during accept.', status)
        return status < 400

    def submit(self, external_id: str, content: str) -> dict:
        """
        `POST /user/stt/task/submit` as multipart: `_token`, `id`, `content`.

        Built by hand because the payload is three short fields and this
        avoids a dependency for one call.
        """
        token = self.get_submit_token(external_id)
        boundary = '----EagleFormBoundary7MA4YWxkTrZu0gW'
        parts = []
        for name, value in (('_token', token), ('id', str(external_id)), ('content', content)):
            parts.append(f'--{boundary}\r\n'
                         f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                         f'{value}\r\n')
        parts.append(f'--{boundary}--\r\n')
        body = ''.join(parts).encode('utf-8')

        status, final_url, _, raw = self._req(f'{self.host}/user/stt/task/submit', method='POST', body=body,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        )
        if status >= 400:
            raise EagleHTTPError(f'submit returned {status}', status)
        return {'status': status, 'final_url': final_url,
                'body_excerpt': raw[:400].decode('utf-8', 'replace')}

    def _normalise(self, row: dict) -> RawTask:
        length = row.get('length')
        return RawTask(
            external_id=str(row.get('id')),
            product=self.product,
            status=str(row.get('status') or ''),
            title=str(row.get('file_name') or row.get('task_id') or ''),
            language=language_code(row.get('language')),
            duration_seconds=float(length) if length else None,
            media_url=str(row.get('file_url') or ''),
            project=project_from_row(row, self.product),
            # Verified: the mp3 is served with no credentials at all.
            media_needs_session=False,
            page_url=f'{self.host}/user/stt/task/{row.get("id")}/show',
            claimed=bool(row.get('awarded')),
            deadline_at=str(row.get('deadline') or ''),
            # `raw` keeps the display name; only the normalised code is
            # promoted to a field.
            raw=row,
        )
