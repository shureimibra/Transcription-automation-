"""
The cache-invalidation key for AcademicFormat -- see that model's own
docstring for why content-hash-based invalidation is used instead of
"does a row already exist" (segments can be edited after the fact via
EditSegmentView, with no version counter anywhere in the schema).
"""
import hashlib


def compute_source_text_hash(job) -> str:
    """sha256 over the ordered (segment.id, segment.text) pairs of a job's
    CURRENT segments. Two calls return the same hash if and only if the
    segments -- their text specifically -- are unchanged; any edit,
    addition, or removal changes it."""
    segments = job.segments.all().order_by('start_time')
    payload = '\n'.join(f'{seg.id}:{seg.text}' for seg in segments)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
