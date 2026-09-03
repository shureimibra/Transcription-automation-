import logging
from typing import Optional

from .base import TranslationEngine
from .m2m_engine import M2MEngine

try:
    from backend.core.services.languages import translation_engine_for
except ModuleNotFoundError:
    from core.services.languages import translation_engine_for

logger = logging.getLogger("AIPipeline")


class TranslationEngineRegistry:
    """
    Selects the translation engine for a translation direction, per the
    translation capability registry in core.services.languages. M2M100
    (MIT) is the default; NLLB (CC-BY-NC-4.0) is only used where M2M100
    doesn't cover one side of the direction. Same pattern as EngineRegistry
    for transcription -- adding a future engine means adding one branch here.

    Must consider BOTH source and target language, not just the target:
    M2M100 only covers 100 languages, so a source transcript in a
    NLLB-only language (e.g. Oromo) crashes with a raw tokenizer KeyError
    if the engine is chosen from the target language alone -- confirmed by
    a real failure translating an Oromo transcript to French, where French
    alone would have picked M2M100.
    """

    _instances = {}

    @classmethod
    def select(cls, target_language: str, source_language: Optional[str] = None) -> TranslationEngine:
        engine_name = translation_engine_for(target_language)
        if source_language and translation_engine_for(source_language) == "nllb":
            engine_name = "nllb"
        if engine_name not in cls._instances:
            cls._instances[engine_name] = cls._build(engine_name)
        logger.info(f"Translation Engine Registry: source='{source_language}' target='{target_language}' -> engine='{engine_name}'")
        return cls._instances[engine_name]

    @staticmethod
    def _build(engine_name: str) -> TranslationEngine:
        if engine_name == "nllb":
            from .nllb_engine import NLLBEngine
            return NLLBEngine()
        return M2MEngine()
