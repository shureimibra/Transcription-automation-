"""
The one place in this codebase that talks to the Gemini REST API for AI
Assist. Nothing above this module knows the wire format; nothing below the
API boundary ever sees the key.

Reuses the `LLMProvider` ABC from translation/llm_fast_engine.py rather than
inventing a parallel abstraction, so a provider written for one feature is
usable by the other. What's added on top is `generate_json`: forced
structured output plus token accounting, neither of which the translation
path needs.

Two deliberate departures from the existing `GeminiProvider`:

  1. THE KEY MOVES OUT OF THE URL. `GeminiProvider.generate` puts it in the
     query string (llm_fast_engine.py:99), which means the key is embedded
     in the request URL that HTTP libraries, proxies, and error text quote
     back. Here it travels in an `x-goog-api-key` header, and `_redact`
     scrubs it from any diagnostic string as a second line of defense
     (RULE 2: never expose the key).
  2. ERRORS ARE TYPED AND SPLIT. `GeminiProvider` raises
     `RuntimeError(f"...{e.read()}")` -- the raw provider body -- and the
     academic pipeline pattern stores str(e) where the browser can read it.
     Here every failure is an AIAssistError carrying a loggable `.detail`
     and a separate client-safe `.safe_message`.
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, NamedTuple, Optional, Tuple

from ..translation.llm_fast_engine import LLMProvider
from .errors import (
    AINotConfiguredError,
    AIProviderError,
    AIRateLimitedError,
    AIResponseSchemaError,
)

logger = logging.getLogger("AIPipeline")

DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Overridable via AI_ASSIST_MODEL or GEMINI_MODEL so a retired model ID stays a
# config change rather than a code change. That the model lineup outruns this
# repo is not hypothetical: the previous default here was "gemini-2.0-flash"
# (copied from llm_fast_engine.py:94), and on 2026-08-21 a live call answered
#
#   HTTP 404: This model models/gemini-2.0-flash is no longer available.
#   Please update your code to use models/gemini-3.6-flash for the latest
#   features and improvements.
#
# so the value below is Google's own named replacement, read out of that
# response. NOTE: llm_fast_engine.py's Fast Translation Mode still defaults to
# the retired ID and will 404 the same way until it is given a live one.
#: THE MODEL WE ASK FOR FIRST.
#:
#: Flash-Lite, not Flash. The free tier meters REQUESTS PER DAY and the
#: per-model allowances differ by more than an order of magnitude: a live
#: probe found gemini-3.6-flash spent after 20 requests in a day while the
#: lite family answered freely at a published ~1,000-1,500. Paid, it is also
#: the cheapest of the family and the one Google markets for translation.
#:
#: This default was gemini-3.6-flash, and the 20/day ceiling is what made an
#: evening of warming fail over and over while every log line blamed a
#: per-minute rate limit.
DEFAULT_MODEL = "gemini-3.5-flash-lite"

#: WHAT TO TRY WHEN THE FIRST ONE IS SPENT.
#:
#: Each model has its OWN daily allowance, so a 429 on one says nothing
#: about the next. Falling through them turns "no AI until tomorrow" into
#: "carry on with the next model", which on a free tier is the difference
#: between a working day and a stopped one.
#:
#: Ordered by free-tier headroom, not by capability: the work here is
#: batch translation against a fixed JSON schema, where the cheapest model
#: that follows the schema is the right one. A better model that has no
#: quota left is worth nothing.
#:
#: Set AI_ASSIST_MODEL_CHAIN to override, comma separated.
DEFAULT_MODEL_CHAIN = (
    'gemini-3.5-flash-lite',
    'gemini-3.5-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
)


def api_keys() -> list:
    """
    Every Gemini key we may spend from, in preference order.

    WHY A LIST. Quota is per project, not per model: a second account is a
    second daily allowance for the SAME model, which is a bigger lever than
    the model chain -- falling back from flash-lite to a heavier model buys
    a smaller allowance at a worse latency, while a second key buys the full
    flash-lite allowance again.

    `GEMINI_API_KEYS` is comma-separated. `GEMINI_API_KEY` remains valid on
    its own so an existing deployment keeps working untouched.
    """
    raw = config('GEMINI_API_KEYS', '') or config('GEMINI_API_KEY', '')
    seen, keys = set(), []
    for candidate in str(raw).split(','):
        key = candidate.strip()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def key_fingerprint(key: str) -> str:
    """
    A short, non-secret handle for a key.

    THE KEY ITSELF MUST NEVER REACH A LOG OR A CACHE KEY. Exhaustion is
    tracked per (account, model) and that tracking gets logged, so what goes
    in is a hash prefix -- enough to tell two accounts apart, useless to
    anyone who reads it.
    """
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]


def model_chain() -> list:
    """The models to try, in order, starting with the configured one."""
    configured = config('AI_ASSIST_MODEL_CHAIN', '')
    if configured:
        chain = [m.strip() for m in str(configured).split(',') if m.strip()]
    else:
        chain = list(DEFAULT_MODEL_CHAIN)
    return chain


class _ExhaustedModels:
    """
    Which models have said "no quota" today, so we stop asking.

    WHY REMEMBER AT ALL. Re-asking a spent model costs a real request on
    every batch, and on a daily allowance a wasted request is a task that
    does not get done. Once a model refuses for quota, it will refuse for
    the rest of the window.

    FORGOTTEN AFTER AN HOUR, because the window might have been per-minute
    rather than per-day and we cannot tell the two apart from the response.
    An hour is long enough to stop the waste and short enough that a
    per-minute limit heals itself without a restart.
    """

    WINDOW_SECONDS = 3600.0

    def __init__(self):
        self._spent = {}
        self._lock = threading.Lock()

    def mark(self, model: str, key_id: str = '') -> None:
        with self._lock:
            self._spent[(key_id, model)] = time.monotonic()

    def reset(self) -> None:
        """
        Forget every refusal.

        PROCESS-GLOBAL STATE NEEDS A WAY OUT. Remembering across runs is the
        whole point in production -- re-asking a spent model costs a real
        request from a daily allowance. But the same memory leaks between
        tests, where one case simulating a 429 silently made every later
        case skip that model and fall through the chain. A cache with no
        reset is a cache that eventually lies to somebody.
        """
        with self._lock:
            self._spent.clear()

    def is_spent(self, model: str, key_id: str = '') -> bool:
        # PER ACCOUNT, NOT PER MODEL. One project running out of flash-lite
        # says nothing about another project's flash-lite, and treating them
        # as the same fact threw away every other account's allowance the
        # moment the first one refused.
        with self._lock:
            at = self._spent.get((key_id, model))
            if at is None:
                return False
            if time.monotonic() - at > self.WINDOW_SECONDS:
                del self._spent[(key_id, model)]
                return False
            return True


EXHAUSTED = _ExhaustedModels()

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 8192

# Ceiling for auto-sized requests. The Flash/Flash-Lite families advertise
# 65536 output tokens; staying under it leaves room for the model's own
# thinking budget, which is billed and counted separately from the text.
MAX_OUTPUT_TOKENS_CEILING = 32768

# Output tokens per character of INPUT text, measured on a real run: a
# 5,151-character English transcript produced 1,969 completion tokens of
# French (0.382 tokens/char). Rounded up, and French is among the more
# expansive targets this platform handles.
OUTPUT_TOKENS_PER_INPUT_CHAR = 0.45

# Fixed overhead for the JSON envelope, segment ids and quoting.
OUTPUT_TOKENS_ENVELOPE = 512


def size_output_tokens(input_chars: int, floor: Optional[int] = None) -> int:
    """
    How many output tokens a request over `input_chars` of source needs.

    Deliberately generous (a 1.5x safety factor on top of an already
    rounded-up rate). Over-asking is free -- maxOutputTokens is a cap, not a
    reservation, and you are billed for what is generated. Under-asking
    costs a truncated response, a schema error, and a retry that may itself
    truncate. The asymmetry is total, so this errs high on purpose.
    """
    needed = int(input_chars * OUTPUT_TOKENS_PER_INPUT_CHAR * 1.5) + OUTPUT_TOKENS_ENVELOPE
    needed = max(needed, floor or DEFAULT_MAX_OUTPUT_TOKENS)
    return min(needed, MAX_OUTPUT_TOKENS_CEILING)

# Transient server-side failures worth one more attempt; a persistent outage
# still surfaces after these are exhausted.
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0

# A free-tier 429 usually carries a RetryInfo telling us exactly how long to
# wait. Honouring a short delay turns a failed revision into a slow one; past
# this ceiling we give up rather than block a worker thread indefinitely.
_MAX_RETRY_SLEEP_SECONDS = 30.0


class TokenUsage(NamedTuple):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


def config(name: str, default: Any = None) -> Any:
    """
    Django setting first, environment second, default last.

    Settings win so tests can use `override_settings`; the env fallback keeps
    this module importable and usable outside a configured Django process.
    Empty strings count as absent -- an unset variable in a .env file usually
    shows up as '' rather than missing.
    """
    try:
        from django.conf import settings as dj_settings

        value = getattr(dj_settings, name, None)
        if value not in (None, ''):
            return value
    except Exception:
        pass
    value = os.environ.get(name)
    return value if value not in (None, '') else default


def int_config(name: str, default: int) -> int:
    try:
        return int(config(name, default))
    except (TypeError, ValueError):
        return default


def float_config(name: str, default: float) -> float:
    try:
        return float(config(name, default))
    except (TypeError, ValueError):
        return default


def _parse_retry_delay(body: str) -> Optional[float]:
    """Pulls the seconds out of a google.rpc.RetryInfo `retryDelay` ("27s")."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    details = (payload.get('error') or {}).get('details') or []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        raw = detail.get('retryDelay')
        if isinstance(raw, str) and raw.endswith('s'):
            try:
                return float(raw[:-1])
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------- #
# Outbound pacing
# --------------------------------------------------------------------- #

class _RequestPacer:
    """
    Every outbound call passes through here, and none of them may burst.

    WHY THE EXISTING GUARD WAS NOT ENOUGH. `ai_budget.check_rate_limit`
    counts rows in the usage ledger -- and a ledger row is only written
    AFTER a response comes back. A request that is refused writes nothing,
    so the moment the provider starts saying no, our own counter goes quiet
    and reports plenty of headroom. It showed 18 requests in two hours while
    Google was refusing us continuously. A counter that can only see
    successes cannot pace anything.

    It also gates whole RUNS, not individual calls. A single warm task fires
    a dozen batches back-to-back; nothing between them looked at the clock.
    The daily budget was barely touched while the per-minute ceiling was
    being crossed within seconds.

    So this counts ATTEMPTS, at the one place every attempt passes, and
    blocks rather than failing -- these are background batches with no
    person waiting, and a run that waits four seconds is better than a
    revision marked failed and a task that stays cold.

    Process-local by design. A DB round trip per outbound call to coordinate
    across processes would cost more than it saves, and the poller is the
    only heavy caller; the web server's occasional request is noise beside
    it. If a second warm worker is ever added, this needs to become shared
    state -- until then, sharing it would be complexity with no payer.
    """

    def __init__(self):
        self._times: list = []
        self._lock = threading.Lock()

    def _limit(self) -> int:
        try:
            from apps.transcriptions.ai_budget import max_requests_per_minute
            return max(1, int(max_requests_per_minute()))
        except Exception:
            return 10

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                # Drop everything outside the trailing minute.
                self._times = [t for t in self._times if now - t < 60.0]
                limit = self._limit()
                if len(self._times) < limit:
                    self._times.append(now)
                    return
                # Sleep until the oldest attempt ages out of the window.
                sleep_for = 60.0 - (now - self._times[0]) + 0.05
            sleep_for = min(max(sleep_for, 0.05), 60.0)
            logger.info('[AI ASSIST] pacing: %d calls in the last minute '
                        '(limit %d); waiting %.1fs',
                        len(self._times), limit, sleep_for)
            time.sleep(sleep_for)


PACER = _RequestPacer()


class GeminiStructuredProvider(LLMProvider):
    """Structured-output Gemini client. One instance per revision run."""

    def __init__(
        self,
        api_key: str,
        keys: Optional[list] = None,
        model: str = DEFAULT_MODEL,
        api_base: str = DEFAULT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = DEFAULT_TEMPERATURE,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ):
        if not api_key:
            raise AINotConfiguredError('No Gemini API key was supplied.')
        # `api_key` stays the CURRENT one, so everything that already reads
        # it -- redaction, the request header -- keeps working. `keys` is the
        # pool it rotates through.
        self.api_key = api_key
        self.keys = list(keys) if keys else [api_key]
        if api_key not in self.keys:
            self.keys.insert(0, api_key)
        self.model = model
        self.api_base = api_base.rstrip('/')
        self.timeout = timeout
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        # Where the next call starts in the key pool; see _post.
        self._key_cursor = 0

    # ------------------------------------------------------------------ #
    # LLMProvider contract
    # ------------------------------------------------------------------ #

    def generate(self, prompt: str) -> str:
        """Plain-text completion, so this class is a drop-in LLMProvider."""
        data = self._post({'contents': [{'parts': [{'text': prompt}]}]})
        return self._extract_text(data)

    # ------------------------------------------------------------------ #
    # Structured output
    # ------------------------------------------------------------------ #

    def generate_json(
        self,
        system_instruction: str,
        prompt: str,
        schema: Dict,
        max_output_tokens: Optional[int] = None,
    ) -> Tuple[Dict, TokenUsage]:
        """
        Returns (parsed_object, usage).

        The system instruction is sent in its own `systemInstruction` field
        rather than glued onto the front of the user turn. That's not
        cosmetic: it keeps the output contract structurally separate from the
        turn that carries untrusted transcript text, so the two can't be
        confused for one another (RULE 5).

        `max_output_tokens` overrides the instance default for ONE call, so a
        caller that knows how much output its request needs can ask for it.
        That matters more than it sounds: the fixed 8192 default is only
        ~1.2x what a 20-minute transcript's translation actually emits, and
        exceeding it does not fail cleanly -- the JSON truncates mid-object,
        which surfaces as AIResponseSchemaError, which triggers the
        split-and-retry path, which turns one request into three. On a
        request-per-day quota that is the difference between fitting and not.
        """
        body = {
            'systemInstruction': {'parts': [{'text': system_instruction}]},
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {
                'responseMimeType': 'application/json',
                'responseSchema': schema,
                'temperature': self.temperature,
                'maxOutputTokens': max_output_tokens or self.max_output_tokens,
            },
        }
        data = self._post(body)
        text = self._extract_text(data)
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            # responseMimeType should make this impossible; it happens anyway
            # when output is truncated mid-object at maxOutputTokens.
            raise AIResponseSchemaError(
                f'Response was not valid JSON ({exc}). First 200 chars: {text[:200]!r}'
            ) from exc
        if not isinstance(parsed, dict):
            raise AIResponseSchemaError(
                f'Response JSON was {type(parsed).__name__}, expected an object.'
            )
        return parsed, self._extract_usage(data)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _redact(self, text: str) -> str:
        """Removes EVERY API key from any string bound for a log or error."""
        if not text:
            return text
        # All of them, not just the active one: a failure while rotating can
        # carry the key that just refused rather than the one now in hand.
        for key in getattr(self, 'keys', None) or [self.api_key]:
            if key:
                text = text.replace(key, '***REDACTED***')
        return text

    def _post(self, body: Dict) -> Dict:
        """
        One request, falling through to the next model when quota runs out.

        EACH MODEL HAS ITS OWN DAILY ALLOWANCE, so a 429 on one says nothing
        about the next. Trying the next one turns "no AI until tomorrow"
        into "carry on", which on a free tier is the whole difference
        between a working day and a stopped one.

        The model that actually answered is recorded on `self.model`, so the
        revision reports what produced it rather than what we hoped would.
        """
        # Configured model first, then the rest of the chain.
        ordered = [self.model] + [m for m in model_chain() if m != self.model]

        # EVERY ACCOUNT ON THE CHEAP MODEL BEFORE ANY ACCOUNT ON A DEARER
        # ONE. Quota is per project, so a second key is a second full
        # allowance of the SAME model -- which is worth more than the next
        # model down, because the chain trades away both speed and cost as
        # it descends. Exhausting flash-lite across four accounts beats
        # moving one account to flash.
        #
        # THE STARTING KEY ROTATES. Always beginning at the first key would
        # drain account one while the others sat idle, so the day would end
        # with one exhausted allowance and three untouched. `_key_cursor`
        # spreads the load without needing to track usage.
        keys = self.keys or [self.api_key]
        start = self._key_cursor % len(keys)
        self._key_cursor += 1

        last_rate_limit = None
        for model in ordered:
            for offset in range(len(keys)):
                key = keys[(start + offset) % len(keys)]
                key_id = key_fingerprint(key)
                if EXHAUSTED.is_spent(model, key_id):
                    continue
                try:
                    result = self._post_one(model, body, key)
                except AIRateLimitedError as exc:
                    EXHAUSTED.mark(model, key_id)
                    last_rate_limit = exc
                    logger.warning(
                        '[AI ASSIST] %s is out of quota on account %s',
                        model, key_id)
                    continue
                if model != self.model or key != self.api_key:
                    logger.info('[AI ASSIST] now using %s on account %s',
                                model, key_id)
                self.model = model
                self.api_key = key
                return result

        # Every model refused. Report the provider's own words -- the caller
        # turns this into "rate limit reached", and a person needs to know
        # it was not one model having a bad minute.
        raise last_rate_limit or AIRateLimitedError(
            'Every configured model refused for quota.')

    def _post_one(self, model: str, body: Dict, api_key: str = '') -> Dict:
        url = f'{self.api_base}/{model}:generateContent'
        encoded = json.dumps(body).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'x-goog-api-key': api_key or self.api_key,
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            # INSIDE THE LOOP, ONCE PER ACTUAL REQUEST.
            #
            # Pacing at the top of this method counted a _post as one
            # request when it can send three: the transport retry below is
            # a real HTTP call every time round. Pacing at 12 per minute
            # therefore permitted up to 36, and the provider -- which
            # allows 20 -- refused us while our own counter reported plenty
            # of headroom. The whole point of counting attempts rather than
            # successes was to stop exactly that kind of undercount.
            PACER.wait()
            request = urllib.request.Request(
                url, data=encoded, headers=headers, method='POST'
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode('utf-8'))

            except urllib.error.HTTPError as exc:
                raw = ''
                try:
                    raw = exc.read().decode('utf-8', errors='ignore')
                except Exception:
                    pass
                detail = self._redact(f'Gemini HTTP {exc.code}: {raw[:1000]}')

                if exc.code == 429:
                    delay = _parse_retry_delay(raw)
                    if (
                        attempt < _MAX_ATTEMPTS
                        and delay is not None
                        and delay <= _MAX_RETRY_SLEEP_SECONDS
                    ):
                        logger.warning(
                            '[AI ASSIST] Rate limited, retrying in %.1fs (attempt %d/%d)',
                            delay, attempt, _MAX_ATTEMPTS,
                        )
                        time.sleep(delay)
                        continue
                    # `delay` is forwarded rather than dropped. A free-tier
                    # 429 often carries no RetryInfo at all, in which case
                    # this loop never retried and the caller is the only
                    # thing standing between the user and a failed run --
                    # AIPostProcessor backs off on its own when there is no
                    # delay to honour, and gives up immediately when the
                    # delay it is handed is hours long.
                    raise AIRateLimitedError(detail, retry_after=delay)

                if exc.code in _TRANSIENT_STATUSES and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        '[AI ASSIST] Transient %s from provider, retrying (attempt %d/%d)',
                        exc.code, attempt, _MAX_ATTEMPTS,
                    )
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    last_error = AIProviderError(detail)
                    continue

                # 4xx other than 429: a bad request, key, or model name.
                # Retrying identical input cannot help.
                #
                # Two of them get their own client message, because
                # "could not be reached" actively misdirects: it sends the
                # operator to look at their network when the fault is in
                # config, and this exact confusion cost a live run once
                # already. The env var names stay in the logged detail --
                # naming server configuration in a response is disclosure,
                # however small (RULE 2).
                if exc.code == 404:
                    raise AIProviderError(
                        f'{detail} (requested model={self.model!r}; a 404 here '
                        f'means the model ID is retired or misspelled -- set '
                        f'AI_ASSIST_MODEL to a current one)',
                        safe_message=(
                            'The AI model configured on this server is not '
                            'available. A server administrator needs to update it.'
                        ),
                    )
                if exc.code in (401, 403):
                    raise AIProviderError(
                        f'{detail} (provider rejected credentials -- check '
                        f'GEMINI_API_KEY)',
                        safe_message=(
                            "The AI service rejected this server's credentials."
                        ),
                    )
                raise AIProviderError(detail)

            except urllib.error.URLError as exc:
                detail = self._redact(f'Gemini connection failed: {exc.reason}')
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    last_error = AIProviderError(detail)
                    continue
                raise AIProviderError(detail) from exc

            except OSError as exc:
                # RemoteDisconnected, ConnectionReset, socket read timeout.
                #
                # This clause is not redundant with URLError above. urllib's
                # do_open wraps failures from h.request() -- connection
                # *setup* -- in URLError, but lets anything raised by
                # h.getresponse() propagate untouched. So a provider that
                # closes the socket mid-response raises
                # http.client.RemoteDisconnected, which subclasses
                # ConnectionResetError -> OSError and is NOT a URLError.
                # Without this clause it escapes _post entirely and the
                # pipeline's bare `except Exception` tells the user the run
                # "failed unexpectedly" instead of that the service dropped.
                # Observed live on an 86-segment multi-batch run.
                #
                # Ordering is load-bearing: HTTPError is a URLError is an
                # OSError, so this must stay last of the three.
                detail = self._redact(f'Gemini connection dropped: {exc}')
                if attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        '[AI ASSIST] Connection dropped, retrying (attempt %d/%d): %s',
                        attempt, _MAX_ATTEMPTS, detail,
                    )
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    last_error = AIProviderError(detail)
                    continue
                raise AIProviderError(detail) from exc

            except ValueError as exc:  # json.loads on the envelope itself
                raise AIProviderError(
                    self._redact(f'Gemini returned an unreadable envelope: {exc}')
                ) from exc

        raise last_error or AIProviderError('Gemini request failed after retries.')

    def _extract_text(self, data: Dict) -> str:
        """
        Digs the text out of the response envelope, turning every shape of
        "no usable candidate" into a typed error instead of the KeyError /
        IndexError that indexing blindly would raise.
        """
        candidates = data.get('candidates') or []
        if not candidates:
            # No candidate at all means the *prompt* was refused. Surfacing
            # blockReason matters: it's the difference between "the service
            # is down" and "your transcript tripped a safety filter", and
            # only one of those is worth retrying.
            feedback = data.get('promptFeedback') or {}
            reason = feedback.get('blockReason') or 'no candidates returned'
            raise AIProviderError(f'Gemini returned no output ({reason}).')

        candidate = candidates[0]
        finish_reason = candidate.get('finishReason')
        parts = ((candidate.get('content') or {}).get('parts')) or []
        text = ''.join(part.get('text', '') for part in parts if isinstance(part, dict))

        if not text:
            raise AIProviderError(
                f'Gemini returned an empty candidate (finishReason={finish_reason}).'
            )
        if finish_reason == 'MAX_TOKENS':
            # The JSON is truncated. Flagged as a schema error so the caller
            # retries with a smaller batch rather than treating it as an outage.
            raise AIResponseSchemaError(
                'Gemini hit maxOutputTokens; the JSON response is truncated.'
            )
        return text

    @staticmethod
    def _extract_usage(data: Dict) -> TokenUsage:
        """
        Reads usageMetadata. Absent or partial metadata yields zeros rather
        than an error -- metering must never be able to fail a revision that
        otherwise succeeded. A zero row in the ledger is visibly wrong on
        inspection; a lost transcript is not recoverable.
        """
        usage = data.get('usageMetadata') or {}
        prompt_tokens = int(usage.get('promptTokenCount') or 0)
        completion_tokens = int(usage.get('candidatesTokenCount') or 0)
        # Reasoning-capable models bill thinking separately; totalTokenCount
        # already includes it, so prefer the provider's own total.
        total = int(usage.get('totalTokenCount') or 0)
        if not total:
            total = prompt_tokens + completion_tokens
        return TokenUsage(prompt_tokens, completion_tokens, total)


def resolve_model_name() -> str:
    return config('AI_ASSIST_MODEL') or config('GEMINI_MODEL') or DEFAULT_MODEL


def is_configured() -> bool:
    """Whether AI Assist can run at all. Drives GET /ai-capabilities/ so the
    UI hides the feature instead of offering a button that always fails."""
    return bool(api_keys())


def build_provider() -> GeminiStructuredProvider:
    """Raises AINotConfiguredError when no key is present."""
    keys = api_keys()
    if not keys:
        raise AINotConfiguredError(
            'GEMINI_API_KEY is not set; AI Assist cannot run.'
        )
    if len(keys) > 1:
        logger.info('[AI ASSIST] %d accounts available: %s', len(keys),
                    ', '.join(key_fingerprint(k) for k in keys))
    return GeminiStructuredProvider(
        api_key=keys[0],
        keys=keys,
        model=resolve_model_name(),
        api_base=config('AI_ASSIST_API_BASE', DEFAULT_API_BASE),
        timeout=int_config('AI_ASSIST_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
        temperature=float_config('AI_ASSIST_TEMPERATURE', DEFAULT_TEMPERATURE),
        max_output_tokens=int_config(
            'AI_ASSIST_MAX_OUTPUT_TOKENS', DEFAULT_MAX_OUTPUT_TOKENS
        ),
    )
