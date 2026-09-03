import logging
from typing import List, Optional

from .base import TranslationEngine

logger = logging.getLogger("AIPipeline")

# Our platform codes -> M2M100's own code, only where they differ.
CODE_ALIASES = {
    "ful": "ff",   # Fulfulde
    "nso": "ns",   # Northern Sotho
    "jw": "jv",    # Javanese
}


class M2MEngine(TranslationEngine):
    """
    facebook/m2m100_418M: MIT-licensed many-to-many translation across 100
    languages, 9,900 directions. Primary/default translation engine -- the
    only one used unless the target language isn't in its coverage.
    """

    MODEL_ID = "facebook/m2m100_418M"
    _model = None
    _tokenizer = None

    @classmethod
    def _load(cls):
        if cls._model is None:
            from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
            logger.info(f"Loading translation engine '{cls.MODEL_ID}' (first use)...")
            cls._tokenizer = M2M100Tokenizer.from_pretrained(cls.MODEL_ID)
            cls._model = M2M100ForConditionalGeneration.from_pretrained(cls.MODEL_ID)
            cls._model.eval()
            logger.info("M2M100 translation engine loaded.")
        return cls._model, cls._tokenizer

    @staticmethod
    def _m2m_code(code: str) -> str:
        return CODE_ALIASES.get(code, code)

    def translate(self, text: str, source_lang: Optional[str], target_lang: str) -> str:
        if not text or not text.strip():
            return text

        import torch
        model, tokenizer = self._load()
        tokenizer.src_lang = self._m2m_code(source_lang) if source_lang else "en"
        encoded = tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=tokenizer.get_lang_id(self._m2m_code(target_lang)),
            )
        return tokenizer.batch_decode(generated, skip_special_tokens=True)[0]

    def translate_batch(self, texts: List[str], source_lang: Optional[str], target_lang: str) -> List[str]:
        # Empty/whitespace segments pass through unchanged (matching
        # translate()'s single-item behavior) rather than being fed to the
        # model -- the tokenizer's padding would otherwise waste a slot on
        # nothing to translate.
        indices = [i for i, t in enumerate(texts) if t and t.strip()]
        if not indices:
            return list(texts)

        import torch
        model, tokenizer = self._load()
        tokenizer.src_lang = self._m2m_code(source_lang) if source_lang else "en"
        batch = [texts[i] for i in indices]
        encoded = tokenizer(batch, return_tensors="pt", padding=True)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=tokenizer.get_lang_id(self._m2m_code(target_lang)),
            )
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)

        results = list(texts)
        for idx, translated in zip(indices, decoded):
            results[idx] = translated
        return results
