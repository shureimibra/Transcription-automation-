import logging
from typing import Optional

from .base import TranscriptionEngine
from .whisper_engine import WhisperEngine

try:
    from backend.core.services.languages import engine_for
except ModuleNotFoundError:
    from core.services.languages import engine_for

logger = logging.getLogger("AIPipeline")


class EngineRegistry:
    """
    Selects the transcription engine for a given language, per the language
    capability registry in core.services.languages. Whisper is the default;
    other engines are only used where that registry says a language needs
    them. Engines are instantiated lazily and cached process-wide, since each
    one may load a large model into memory on first use -- adding a new
    engine to the platform means adding one branch here, nothing else.
    """

    _instances = {}

    @classmethod
    def select(cls, language: Optional[str]) -> TranscriptionEngine:
        engine_name = engine_for(language) if language else "whisper"
        if engine_name not in cls._instances:
            cls._instances[engine_name] = cls._build(engine_name)
        logger.info(f"Engine Registry: language='{language}' -> engine='{engine_name}'")
        return cls._instances[engine_name]

    @staticmethod
    def _build(engine_name: str) -> TranscriptionEngine:
        if engine_name == "simba":
            from .simba_engine import SimbaEngine
            return SimbaEngine()
        if engine_name == "mms":
            from .mms_engine import MMSEngine
            return MMSEngine()
        return WhisperEngine(model_size="base")
