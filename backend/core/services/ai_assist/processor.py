"""
Orchestration: transcript segments in, processed text out.

This layer owns the decisions that keep RULE 3 (never mutate the original)
and RULE 4 (never desynchronize text from its timestamp) true regardless of
what the model returns:

  * Nothing here writes to the database or touches a TranscriptSegment. It
    reads `.text` and returns new strings. The caller decides what to store.
  * Every response is checked against the exact batch that was sent. Any
    mismatch -- wrong count, a duplicated id, an id that wasn't requested --
    discards that batch's output and keeps the original text. A partially
    trustworthy mapping is worse than no mapping, because a single dropped
    segment shifts every later text onto the wrong timestamp and speaker.

Failure policy, deliberately split in two:

  * Content failures (unparseable JSON, truncation, id mismatch) DEGRADE:
    that batch is returned unchanged and the run continues. The user gets a
    revision where some segments simply weren't improved -- visible in the
    diff, harmless, recoverable.
  * Transport and policy failures (provider unreachable, rate limited,
    budget exhausted, no key) PROPAGATE and fail the revision. Silently
    returning "nothing changed" for an outage would be a lie the UI has no
    way to tell apart from "the transcript was already clean".
"""
import logging
import time
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

import hashlib
import math

from ..translation.batching import batch_by_size
from . import prompts
from .errors import AIRateLimitedError, AIResponseSchemaError, SegmentIntegrityError
from .hashing import compute_segment_content_hash, get_prompt_version
from .providers import (
    TokenUsage, build_provider, float_config, int_config, resolve_model_name,
    size_output_tokens,
)
from .schemas import (
    RESPONSE_SCHEMA,
    SEGMENT_ID_KEY,
    SEGMENT_TEXT_KEY,
    SEGMENTS_KEY,
)

logger = logging.getLogger("AIPipeline")


def _get_segment_cache_model():
    try:
        from apps.transcriptions.models import AISegmentCache
        return AISegmentCache
    except Exception:
        try:
            from backend.apps.transcriptions.models import AISegmentCache
            return AISegmentCache
        except Exception:
            return None


def _resolve_prompt_version(
    operation: str, custom_instruction: Optional[str] = None,
    domain_context: Optional[str] = None,
) -> str:
    """
    Cache partition key. Two calls only ever share a cached segment result
    when they would have produced the same prompt.

    custom_instruction USED TO ONLY COUNT FOR THE 'custom' OPERATION,
    BECAUSE THAT USED TO BE THE ONLY OPERATION build_prompt() EVER PUT IT
    IN. Once build_prompt() started carrying a non-CUSTOM operation's
    custom_instruction too (warmpool._warm_translation's disfluency
    instruction on 'translate'), this stayed gated on operation == CUSTOM
    -- so a segment cached from BEFORE that fix, translated with no
    disfluency guidance at all, kept satisfying lookups from AFTER it,
    silently serving the old, worse result forever. Same failure shape as
    domain_context below; same fix: count it whenever it is present,
    regardless of operation.

    domain_context IS PART OF THE PROMPT NOW, SO IT MUST BE PART OF THIS. A
    segment cached before domain_context existed -- or under a different
    document's detected domain -- must not be handed back forever as though
    domain-aware translation had run, just because the segment's own text
    happened to match. Hashed rather than embedded verbatim: this string
    ends up in a cache row's indexed column, and domain_context can run to a
    few hundred characters of prose.
    """
    base_version = get_prompt_version(operation)
    suffix = ''
    if custom_instruction:
        instr_hash = hashlib.sha256(custom_instruction.strip().encode('utf-8')).hexdigest()[:10]
        suffix += f":{instr_hash}"
    if domain_context:
        domain_hash = hashlib.sha256(domain_context.strip().encode('utf-8')).hexdigest()[:10]
        suffix += f":d{domain_hash}"
    return f"{base_version}{suffix}"


def _estimate_saved_tokens(segments: List[Any]) -> int:
    total_chars = sum(len(getattr(s, 'text', '') or getattr(s, 'original_text', '') or '') for s in segments)
    text_tokens = math.ceil(total_chars / 4)
    # 2x for input + output savings + 15 tokens scaffolding per segment
    return text_tokens * 2 + 15 * len(segments)


class ProcessedSegment(NamedTuple):
    """One segment's result. `source` is the original row, untouched."""

    source: Any
    original_text: str
    processed_text: str
    changed: bool


class BatchResult(NamedTuple):
    segments: List[ProcessedSegment]
    usage: TokenUsage
    degraded: bool  # True when this batch fell back to unchanged text
    cache_hits: int = 0
    cache_misses: int = 0
    estimated_tokens_saved: int = 0
    #: WHICH MODEL ACTUALLY ANSWERED. The fallback chain exists so a spent
    #: model does not stop the day, but nothing recorded whether it ever
    #: fired: every revision came back with an empty `model_name`, so "are we
    #: still on flash-lite or have we been quietly running on the third
    #: fallback for a week?" was unanswerable from the data. It is computed
    #: here anyway, for the cache key -- it just was not carried out.
    model_name: str = ''



DEFAULT_MAX_ITEMS = 60
DEFAULT_MAX_CHARS = 9000
DEFAULT_INTER_BATCH_DELAY_SECONDS = 6.0
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 12.0
DEFAULT_RATE_LIMIT_MAX_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
_CANCEL_POLL_SECONDS = 1.0


class _Cancelled(Exception):
    """Internal signal: cancellation was observed while waiting, so stop."""


def build_batches(
    segments: List[Any],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> List[List[Any]]:
    """
    Splits segments into the exact batches a run would send.

    Module-level, not just a method, because the budget pre-check needs the
    batch count *before* deciding whether to dispatch -- and constructing an
    AIPostProcessor builds a provider, which requires an API key. Refusing an
    over-budget request must not depend on being able to make a request.
    """
    return batch_by_size(
        list(segments),
        lambda segment: getattr(segment, 'text', '') or getattr(segment, 'processed_text', '') or getattr(segment, 'original_text', '') or '',
        max_items=max_items or int_config('AI_ASSIST_MAX_ITEMS_PER_BATCH', DEFAULT_MAX_ITEMS),
        max_chars=max_chars or int_config('AI_ASSIST_MAX_CHARS_PER_BATCH', DEFAULT_MAX_CHARS),
    )


def _unchanged(batch: List[Any]) -> List[ProcessedSegment]:
    return [
        ProcessedSegment(
            source=segment,
            original_text=getattr(segment, 'text', '') or '',
            processed_text=getattr(segment, 'text', '') or '',
            changed=False,
        )
        for segment in batch
    ]


def _validate_and_map(batch: List[Any], response: Dict) -> List[ProcessedSegment]:
    """
    Maps a response back onto the batch that produced it, or raises
    SegmentIntegrityError.

    The id check is exact set equality against range(len(batch)), which
    rejects a short response, a long one, a duplicated id, and an invented id
    in a single comparison.
    """
    raw = response.get(SEGMENTS_KEY)
    if not isinstance(raw, list):
        raise AIResponseSchemaError(
            f'Response has no {SEGMENTS_KEY!r} array (keys: {sorted(response)!r}).'
        )

    by_id: Dict[int, Dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise AIResponseSchemaError(
                f'Expected objects in {SEGMENTS_KEY!r}, got {type(entry).__name__}.'
            )
        segment_id = entry.get(SEGMENT_ID_KEY)
        if not isinstance(segment_id, int) or isinstance(segment_id, bool):
            raise SegmentIntegrityError(
                f'Non-integer {SEGMENT_ID_KEY}: {segment_id!r}.'
            )
        if segment_id in by_id:
            raise SegmentIntegrityError(f'Duplicate {SEGMENT_ID_KEY} {segment_id}.')
        by_id[segment_id] = entry

    expected = set(range(len(batch)))
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        unexpected = sorted(set(by_id) - expected)
        raise SegmentIntegrityError(
            f'Segment ids do not match the request. '
            f'Sent {len(batch)}, got {len(by_id)}; '
            f'missing={missing[:10]}, unexpected={unexpected[:10]}.'
        )

    results: List[ProcessedSegment] = []
    for index, segment in enumerate(batch):
        original = getattr(segment, 'text', '') or ''
        processed = by_id[index].get(SEGMENT_TEXT_KEY)
        if not isinstance(processed, str):
            raise AIResponseSchemaError(
                f'{SEGMENT_TEXT_KEY} for segment {index} was '
                f'{type(processed).__name__}, expected a string.'
            )
        processed = processed.strip()

        # A model that returns nothing for a non-empty segment has dropped
        # content, not edited it. Treated as "no change" so an Accept All can
        # never blank out speech that was actually said. `changed` is
        # recomputed from the strings here rather than trusting the model's
        # own SEGMENT_CHANGED_KEY hint, so a mislabelled flag cannot produce
        # a wrong diff.
        if not processed and original.strip():
            logger.warning(
                '[AI ASSIST] Model returned empty text for segment %s; keeping original.',
                getattr(segment, 'id', index),
            )
            processed = original

        results.append(
            ProcessedSegment(
                source=segment,
                original_text=original,
                processed_text=processed,
                changed=processed.strip() != original.strip(),
            )
        )
    return results


class AIPostProcessor:
    """
    Runs one operation over a list of segments.

    Yields per batch rather than returning everything at once so the caller
    can broadcast progress, persist incrementally, and honour a cancellation
    request between provider calls -- the same shape
    run_academic_format_pipeline already uses.
    """

    def __init__(
        self,
        provider: Optional[Any] = None,
        max_items: Optional[int] = None,
        max_chars: Optional[int] = None,
        inter_batch_delay: Optional[float] = None,
        rate_limit_backoff: Optional[float] = None,
        rate_limit_max_retries: Optional[int] = None,
    ):
        # Injected in tests (see the FakeProvider pattern at tests.py:302);
        # built from settings in production.
        self.provider = provider if provider is not None else build_provider()
        self.max_items = max_items or int_config('AI_ASSIST_MAX_ITEMS_PER_BATCH', DEFAULT_MAX_ITEMS)
        self.max_chars = max_chars or int_config('AI_ASSIST_MAX_CHARS_PER_BATCH', DEFAULT_MAX_CHARS)
        # Pacing knob for tight free-tier request-per-minute ceilings.
        # Deliberately not `or`: 0 is a meaningful value (no pacing, correct
        # on a paid tier) and must not fall through to the default.
        self.inter_batch_delay = (
            inter_batch_delay
            if inter_batch_delay is not None
            else float_config(
                'AI_ASSIST_INTER_BATCH_DELAY_SECONDS',
                DEFAULT_INTER_BATCH_DELAY_SECONDS,
            )
        )
        # How long to wait out a 429 before giving up on the run. Same
        # zero-is-meaningful reasoning as above.
        self.rate_limit_backoff = (
            rate_limit_backoff
            if rate_limit_backoff is not None
            else float_config(
                'AI_ASSIST_RATE_LIMIT_BACKOFF_SECONDS',
                DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
            )
        )
        self.rate_limit_max_retries = (
            rate_limit_max_retries
            if rate_limit_max_retries is not None
            else int_config(
                'AI_ASSIST_RATE_LIMIT_MAX_RETRIES',
                DEFAULT_RATE_LIMIT_MAX_RETRIES,
            )
        )

    def build_batches(self, segments: List[Any]) -> List[List[Any]]:
        return build_batches(segments, self.max_items, self.max_chars)

    def iter_batches(
        self,
        segments: List[Any],
        operation: str,
        target_language: Optional[str] = None,
        custom_instruction: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        source_language: Optional[str] = None,
        use_cache: bool = True,
        domain_context: Optional[str] = None,
    ) -> Iterator[BatchResult]:
        if not segments:
            return

        batches = self.build_batches(segments)
        cache_model = _get_segment_cache_model() if use_cache else None
        model_name = getattr(self.provider, 'model', None) or resolve_model_name()
        prompt_version = _resolve_prompt_version(operation, custom_instruction, domain_context)
        target_lang = target_language or ''
        source_lang = source_language or ''

        for position, batch in enumerate(batches):
            if should_cancel is not None and should_cancel():
                return

            cached_map = {}
            miss_batch = []
            miss_indices = []

            if cache_model is not None:
                seg_hashes = [compute_segment_content_hash(getattr(s, 'text', '') or '') for s in batch]
                cached_entries = {
                    entry.content_hash: entry
                    for entry in cache_model.objects.filter(
                        operation=operation,
                        target_language=target_lang,
                        model_name=model_name,
                        prompt_version=prompt_version,
                        content_hash__in=set(seg_hashes),
                    )
                }

                for idx, (seg, chash) in enumerate(zip(batch, seg_hashes)):
                    if chash in cached_entries:
                        entry = cached_entries[chash]
                        cached_map[idx] = ProcessedSegment(
                            source=seg,
                            original_text=getattr(seg, 'text', '') or '',
                            processed_text=entry.result_text,
                            changed=entry.changed,
                        )
                    else:
                        miss_indices.append(idx)
                        miss_batch.append(seg)
            else:
                miss_batch = list(batch)
                miss_indices = list(range(len(batch)))

            # If all items in this batch are cached:
            if not miss_batch:
                ordered_segs = [cached_map[i] for i in range(len(batch))]
                saved = _estimate_saved_tokens(batch)
                yield BatchResult(
                    segments=ordered_segs,
                    usage=TokenUsage(0, 0, 0),
                    degraded=False,
                    cache_hits=len(batch),
                    cache_misses=0,
                    model_name=model_name,
                    estimated_tokens_saved=saved,
                )
                continue

            # If there are misses in this batch, send ONLY miss_batch to provider
            try:
                if position and self.inter_batch_delay > 0:
                    self._sleep(self.inter_batch_delay, should_cancel)
                miss_result = self._process_batch(
                    miss_batch, operation, target_language, custom_instruction,
                    should_cancel=should_cancel, domain_context=domain_context,
                )
            except _Cancelled:
                logger.info(
                    '[AI ASSIST] Cancelled while waiting; stopping after %d batch(es).',
                    position,
                )
                return

            # Save new results to cache
            if cache_model is not None and not miss_result.degraded:
                for res in miss_result.segments:
                    chash = compute_segment_content_hash(res.original_text)
                    try:
                        cache_model.objects.update_or_create(
                            operation=operation,
                            source_language=source_lang,
                            target_language=target_lang,
                            model_name=model_name,
                            prompt_version=prompt_version,
                            content_hash=chash,
                            defaults={
                                'source_text_snapshot': res.original_text,
                                'result_text': res.processed_text,
                                'changed': res.changed,
                            },
                        )
                    except Exception as exc:
                        logger.warning('[AI ASSIST] Failed to cache segment result: %s', exc)

            # Merge cached and newly processed results for this batch
            new_map = {miss_indices[k]: res for k, res in enumerate(miss_result.segments)}
            merged_batch_segs = []
            for i in range(len(batch)):
                if i in cached_map:
                    merged_batch_segs.append(cached_map[i])
                elif i in new_map:
                    merged_batch_segs.append(new_map[i])
                else:
                    merged_batch_segs.append(
                        ProcessedSegment(
                            source=batch[i],
                            original_text=getattr(batch[i], 'text', '') or '',
                            processed_text=getattr(batch[i], 'text', '') or '',
                            changed=False,
                        )
                    )

            saved = _estimate_saved_tokens([batch[i] for i in cached_map])
            yield BatchResult(
                segments=merged_batch_segs,
                usage=miss_result.usage,
                degraded=miss_result.degraded,
                cache_hits=len(cached_map),
                cache_misses=len(miss_batch),
                model_name=model_name,
                estimated_tokens_saved=saved,
            )

    def process(
        self,
        segments: List[Any],
        operation: str,
        target_language: Optional[str] = None,
        custom_instruction: Optional[str] = None,
        source_language: Optional[str] = None,
        use_cache: bool = True,
        domain_context: Optional[str] = None,
    ) -> Tuple[List[ProcessedSegment], TokenUsage, bool]:
        """Materializes every batch. Convenience for callers and tests that
        don't need incremental progress."""
        results: List[ProcessedSegment] = []
        usage = TokenUsage()
        degraded = False
        for batch_result in self.iter_batches(
            segments, operation,
            target_language=target_language,
            custom_instruction=custom_instruction,
            source_language=source_language,
            use_cache=use_cache,
            domain_context=domain_context,
        ):
            results.extend(batch_result.segments)
            usage = usage + batch_result.usage
            degraded = degraded or batch_result.degraded
        return results, usage, degraded

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _sleep(self, seconds: float, should_cancel: Optional[Callable[[], bool]] = None):
        """Waits, but stays cancellable.

        A rate-limited batch can now wait the better part of a minute, and
        `should_cancel` reads the DB flag the Cancel button sets. Sleeping the
        whole interval in one call would make Cancel appear ignored for as long
        as the wait lasts, so the interval is sliced and the flag re-checked
        between slices. Raises _Cancelled, which iter_batches turns into a
        clean stop rather than a failure.
        """
        if seconds <= 0:
            return
        remaining = seconds
        while remaining > 0:
            if should_cancel is not None and should_cancel():
                raise _Cancelled()
            slice_seconds = min(_CANCEL_POLL_SECONDS, remaining)
            time.sleep(slice_seconds)
            remaining -= slice_seconds
        if should_cancel is not None and should_cancel():
            raise _Cancelled()

    def _call_with_backoff(
        self,
        batch: List[Any],
        operation: str,
        target_language: Optional[str],
        custom_instruction: Optional[str],
        should_cancel: Optional[Callable[[], bool]] = None,
        domain_context: Optional[str] = None,
    ) -> Tuple[List[ProcessedSegment], TokenUsage]:
        """`_call`, but a 429 is waited out instead of failing the revision.

        The provider retries a 429 only when the response carried a
        google.rpc.RetryInfo it could read (providers.py). A free-tier 429
        frequently carries none, so without this loop the very first refusal
        ends the run -- which is exactly the failure the user reported. Here
        the absence of a delay is treated as "wait a sensible amount and try
        again" rather than "give up".

        Two cases are NOT retried, on purpose:
          * The provider asked for longer than MAX_RATE_LIMIT_WAIT_SECONDS.
            That is a daily quota window, not a per-minute burst; no wait
            inside this request can clear it, so failing now is honest and
            immediate instead of slow and identical.
          * Retries are exhausted. The error propagates and the revision fails
            with the rate-limit message, as before.
        """
        attempt = 0
        while True:
            try:
                return self._call(
                    batch, operation, target_language, custom_instruction,
                    domain_context=domain_context,
                )
            except AIRateLimitedError as exc:
                attempt += 1
                if attempt > self.rate_limit_max_retries:
                    logger.error(
                        '[AI ASSIST] Still rate limited after %d retr%s; failing the run.',
                        self.rate_limit_max_retries,
                        'y' if self.rate_limit_max_retries == 1 else 'ies',
                    )
                    raise
                if exc.retry_after and exc.retry_after > MAX_RATE_LIMIT_WAIT_SECONDS:
                    logger.error(
                        '[AI ASSIST] Provider asked for %.0fs -- that is a quota '
                        'window, not a burst. Failing immediately.',
                        exc.retry_after,
                    )
                    raise
                # Exponential: 12s, 24s, 48s by default. The provider's own
                # figure wins when it gave one, since it knows when the window
                # actually refills.
                wait = exc.retry_after or self.rate_limit_backoff * (2 ** (attempt - 1))
                wait = min(wait, MAX_RATE_LIMIT_WAIT_SECONDS)
                logger.warning(
                    '[AI ASSIST] Rate limited; waiting %.0fs before retry %d/%d.',
                    wait, attempt, self.rate_limit_max_retries,
                )
                self._sleep(wait, should_cancel)

    def _call(
        self,
        batch: List[Any],
        operation: str,
        target_language: Optional[str],
        custom_instruction: Optional[str],
        domain_context: Optional[str] = None,
    ) -> Tuple[List[ProcessedSegment], TokenUsage]:
        payload = prompts.build_payload(batch)
        prompt = prompts.build_prompt(
            operation,
            payload,
            target_language=target_language,
            custom_instruction=custom_instruction,
            domain_context=domain_context,
        )
        # Sized from THIS batch rather than a fixed ceiling. A long batch that
        # truncates does not fail cleanly -- it surfaces as a schema error and
        # falls into the split-and-retry path below, turning one request into
        # three. On a requests-per-day quota that is the whole budget.
        response, usage = self.provider.generate_json(
            prompts.SYSTEM_INSTRUCTION, prompt, RESPONSE_SCHEMA,
            max_output_tokens=size_output_tokens(
                sum(len(getattr(segment, 'text', '') or '') for segment in batch)
            ),
        )
        return _validate_and_map(batch, response), usage

    def _process_batch(
        self,
        batch: List[Any],
        operation: str,
        target_language: Optional[str],
        custom_instruction: Optional[str],
        should_cancel: Optional[Callable[[], bool]] = None,
        _split_depth: int = 0,
        domain_context: Optional[str] = None,
    ) -> BatchResult:
        try:
            segments, usage = self._call_with_backoff(
                batch, operation, target_language, custom_instruction,
                should_cancel=should_cancel, domain_context=domain_context,
            )
            return BatchResult(segments=segments, usage=usage, degraded=False)

        except (AIResponseSchemaError, SegmentIntegrityError) as first_error:
            logger.warning('[AI ASSIST] Batch failed validation: %s', first_error.detail)

            # A truncated or misaligned response on a multi-segment batch is
            # usually a size problem, and halving is a far better remedy than
            # repeating the identical request. Depth-limited so a genuinely
            # broken model degrades in a handful of calls instead of
            # recursing down to one call per segment.
            if len(batch) > 1 and _split_depth < 2:
                midpoint = len(batch) // 2
                logger.info(
                    '[AI ASSIST] Retrying as two batches of %d/%d.',
                    midpoint, len(batch) - midpoint,
                )
                halves = [
                    self._process_batch(
                        half, operation, target_language, custom_instruction,
                        should_cancel=should_cancel,
                        _split_depth=_split_depth + 1,
                        domain_context=domain_context,
                    )
                    for half in (batch[:midpoint], batch[midpoint:])
                ]
                return BatchResult(
                    segments=[s for half in halves for s in half.segments],
                    usage=halves[0].usage + halves[1].usage,
                    degraded=any(half.degraded for half in halves),
                )

            try:
                segments, usage = self._call_with_backoff(
                    batch, operation, target_language, custom_instruction,
                    should_cancel=should_cancel,
                )
                return BatchResult(segments=segments, usage=usage, degraded=False)
            except (AIResponseSchemaError, SegmentIntegrityError) as second_error:
                # Out of remedies. Keep the original text for these segments;
                # the transcript stays correct and the diff simply shows no
                # change here. Everything else in the revision still lands.
                logger.error(
                    '[AI ASSIST] Giving up on %d segment(s), keeping original text: %s',
                    len(batch), second_error.detail,
                )
                return BatchResult(
                    segments=_unchanged(batch), usage=TokenUsage(), degraded=True
                )
