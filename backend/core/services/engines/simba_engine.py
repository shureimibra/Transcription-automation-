import logging
import os
from typing import Any, Dict, Generator, Optional

from .base import TranscriptionEngine

logger = logging.getLogger("AIPipeline")


class _Info:
    def __init__(self, language, language_probability=None):
        self.language = language
        self.language_probability = language_probability


class SimbaEngine(TranscriptionEngine):
    """
    UBC-NLP Simba-W: Whisper-large-v3 architecture fine-tuned on 39 African
    languages (EMNLP 2025, 'Voice of a Continent' / SimbaBench). CC-BY-4.0,
    built on Whisper's MIT-licensed foundation -- used for African languages
    Whisper itself doesn't support, or (Swahili, Somali) was directly
    observed producing poor output for during this project's testing.

    Simba-W reuses Whisper's original tokenizer completely unchanged --
    verified against its published generation_config.json/vocab_size: no new
    language tokens were added for the African languages it was fine-tuned
    on. UBC-NLP's own reference notebook therefore runs inference without
    forcing a language token at all; we follow that documented usage rather
    than guessing a token-forcing mechanism the model was never trained with.
    """

    MODEL_ID = "UBC-NLP/Simba-W"
    _pipeline = None  # class-level cache: loaded once, reused across jobs/instances

    @classmethod
    def _load(cls):
        if cls._pipeline is None:
            from transformers import pipeline
            logger.info(f"Loading Simba engine '{cls.MODEL_ID}' (first use)...")
            cls._pipeline = pipeline(
                "automatic-speech-recognition",
                model=cls.MODEL_ID,
                chunk_length_s=30,
                device="cpu",
            )
            logger.info("Simba engine loaded.")
        return cls._pipeline

    def transcribe(
        self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio file not found: {audio_path}")

        asr = self._load()
        result = asr(audio_path, return_timestamps=True)
        self.last_info = _Info(language=language or "auto")

        for chunk in result.get("chunks", []):
            start, end = chunk.get("timestamp", (0, 0))
            text = chunk["text"].strip()
            if not text:
                continue
            yield {
                "text": text,
                "start": round(start or 0.0, 2),
                "end": round((end if end is not None else start) or 0.0, 2),
                "language": language or "auto",
                "confidence": None,
            }
