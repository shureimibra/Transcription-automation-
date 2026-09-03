from abc import ABC, abstractmethod
from typing import List, Optional


class TranslationEngine(ABC):
    """
    Common interface every translation engine implements. Mirrors the
    TranscriptionEngine pattern: the rest of the application only ever talks
    to this interface, never to a specific engine directly.
    """

    @abstractmethod
    def translate(self, text: str, source_lang: Optional[str], target_lang: str) -> str:
        """Translates a single segment of text. source_lang may be None (let
        the engine auto-detect) if the job's spoken language is unknown."""
        raise NotImplementedError

    def translate_batch(self, texts: List[str], source_lang: Optional[str], target_lang: str) -> List[str]:
        """
        Translates many segments of text at once, returning translations in
        the same order -- callers map result[i] back onto the same original
        segment that supplied texts[i]. Batching amortizes tokenizer setup
        and per-call inference overhead across many segments instead of
        paying it once per segment (the dominant cost of translating a
        transcript one segment at a time).

        Default implementation just calls translate() per item, so any
        engine that doesn't override this (or a future engine that has no
        meaningful notion of batching) still works correctly, just without
        the speedup. Engines that can genuinely batch (M2M100, NLLB) should
        override this with a real batched tokenizer + model call.
        """
        return [self.translate(text, source_lang, target_lang) for text in texts]
