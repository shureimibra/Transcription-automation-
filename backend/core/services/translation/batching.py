from typing import List, TypeVar

T = TypeVar("T")

# ~20-50 segments per batch, whichever bound hits first. The character
# budget is a deliberately conservative proxy for token count (avoids a
# second tokenizer pass just to measure): M2M100/NLLB both have a 1024-token
# position limit, and roughly 4 characters/token keeps a worst-case batch
# comfortably under that even before padding.
MAX_SEGMENTS_PER_BATCH = 40
MAX_CHARS_PER_BATCH = 2000


def batch_by_size(items: List[T], get_text, max_items: int = MAX_SEGMENTS_PER_BATCH,
                   max_chars: int = MAX_CHARS_PER_BATCH) -> List[List[T]]:
    """
    Groups items into contiguous batches of ~20-50, splitting early if the
    accumulated text would exceed max_chars first. Order is always
    preserved -- a batch is a contiguous slice of the input, items are never
    reordered or merged across batches.
    """
    batches: List[List[T]] = []
    current: List[T] = []
    current_chars = 0

    for item in items:
        text_len = len(get_text(item) or "")
        if current and (len(current) >= max_items or current_chars + text_len > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += text_len

    if current:
        batches.append(current)

    return batches
