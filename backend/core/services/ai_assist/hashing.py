"""
Cache keys for AI revisions.

Two different hashes, for two different questions:

  * `compute_request_hash` -- "have we already paid for exactly this?"
    Covers the text being sent AND every input that changes the output
    (operation, languages, model, custom instruction). A match means the
    stored revision is byte-for-byte what a new call would produce, so the
    request is served from the database and the provider is never billed.
  * `compute_source_text_hash` (reused from the academic package) -- "is
    this revision still based on the current transcript?" Job-wide, so the
    UI can mark a revision stale after any segment edit.

Segment text can be edited at any time through EditSegmentView and there is
no version counter anywhere in the schema, which is why both of these are
content hashes rather than timestamps or row-existence checks.
"""
import hashlib
import json
from typing import Any, Iterable, List, Optional

from ..academic.hashing import compute_source_text_hash  # re-exported

__all__ = [
    'compute_source_text_hash',
    'compute_range_text_hash',
    'compute_request_hash',
    'normalize_segment_text',
    'compute_segment_content_hash',
    'get_prompt_version',
    'PROMPT_VERSIONS',
]

# BUMP ON EVERY PROMPT EDIT. These feed both compute_request_hash and the
# AISegmentCache key, so a wording change that is not reflected here keeps
# serving results produced by the previous prompt -- silently, and for as
# long as the cache rows live.
#
# 'translate' is at v2 because the legal/French rules that had been written
# into the generic translate task were removed: they applied Task 4130's
# domain to every translation the platform performs, medical and engineering
# included (RULE 8). Domain guidance now arrives per-document from the
# quality layer instead. Every v1 translate cache entry was produced under
# the old wording and must not be reused.
#
# 'translate' is at v6 because the draft prompt had no instruction to write
# natively (translationese, false cognates) or to follow the document's own
# country's French (Canadian vs European) -- both were only asked of the
# worker's polish prompt, after the draft was already generic. Rules 13-15
# (titles, short words, currency) had also been added without a bump, so
# this clears entries produced without those too.
#
# 'translate' is at v5 because a FAILED review named two more causes: a
# sudden switch between familiar and polite address, and English idioms
# rendered word-for-word ("no contest" -> "indiscutable"). Every v4 entry
# was produced without those rules.
#
# v4 was because the prompt now also forbids inventing a
# reference, writing a third language, emitting markdown, and naming one
# statute two ways -- each written from a defect actually observed, and each
# previously prevented only at polish time, after the draft was already
# wrong. Every v3 entry was produced without them.
#
# v3 was because the prompt forbids changing a number's
# FORM. The old wording said to leave numbers "intact unless the target
# language genuinely requires them to change", which the model satisfied
# while turning "16 and 2 thirds percent" into "16,67 pour cent" -- right
# arithmetic, invented precision, and two BLOCKING findings at the gate on
# task 4813. Every v2 entry was produced under that wording.
PROMPT_VERSIONS = {
    'correct': 'v1',
    'academic': 'v1',
    'translate': 'v6',
    'improve': 'v1',
    'custom': 'v1',
}


def get_prompt_version(operation: str) -> str:
    return PROMPT_VERSIONS.get(operation, 'v1')


def normalize_segment_text(text: str) -> str:
    if not text:
        return ''
    return ' '.join(str(text).strip().split())


def compute_segment_content_hash(text: str) -> str:
    normalized = normalize_segment_text(text)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def compute_range_text_hash(segments: Iterable[Any]) -> str:
    """sha256 over the ordered (segment.id, segment.text) pairs actually being
    sent. Range-scoped on purpose: processing segments 0-50 and 51-100 of the
    same job are genuinely different requests and must not share a cache
    entry, while an edit *outside* the selected range should not needlessly
    invalidate a revision that never saw it."""
    payload = '\n'.join(f'{segment.id}:{segment.text}' for segment in segments)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def compute_request_hash(
    segments: List[Any],
    operation: str,
    model_name: str,
    target_language: Optional[str] = None,
    source_language: Optional[str] = None,
    custom_instruction: Optional[str] = None,
    source_revision_id: Optional[Any] = None,
    quality_profile: Optional[str] = None,
) -> str:
    """
    The full cache key. Any change to any component produces a different
    hash, so a cache hit is only ever returned for an identical request.

    `model_name` is included because the same prompt against a different
    model is a different result -- without it, changing GEMINI_MODEL would
    silently keep serving revisions produced by the old one.

    `quality_profile` is in the digest because a gated run and an ungated
    one over identical text are different requests with different outputs: a
    repair pass edits the deliverable, so serving a cached ungated revision
    to a request that asked for Eagle Web review would hand back text that
    never passed the gate while reporting that it had. Defaults to None so
    every existing caller keeps producing its existing hashes.

    `source_revision_id` records which version the text came from. The text
    hash almost always separates a chained request from a direct one by
    itself, but not when the upstream operation changed nothing at all: then
    "translate the corrected version" and "translate the original" hash the
    same bytes, and the cache would answer with the revision whose recorded
    lineage says it came from the original. The text would be right and the
    provenance wrong, so lineage goes into the digest.
    """
    key = {
        'text': compute_range_text_hash(segments),
        'operation': operation,
        'model': model_name,
        'target_language': target_language or '',
        'source_language': source_language or '',
        'custom_instruction': (custom_instruction or '').strip(),
        'source_revision': str(source_revision_id or ''),
        'quality_profile': (quality_profile or ''),
    }
    # sort_keys so the digest depends on the values, never on dict ordering.
    canonical = json.dumps(key, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
