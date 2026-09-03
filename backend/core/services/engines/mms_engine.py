import logging
import os
from typing import Any, Dict, Generator, Optional

from .base import TranscriptionEngine

logger = logging.getLogger("AIPipeline")

# Our platform codes -> MMS's own ISO 639-3 adapter names. Every language we
# route to MMS needs an entry here (core.services.languages.MMS_LANGUAGES and
# WHISPER_TO_MMS_OVERRIDES) -- MMS's adapter vocabulary is keyed by ISO 639-3,
# not the platform's ISO 639-1 codes, so a missing entry here means the
# adapter is silently never switched (verified against the model's own
# processor.tokenizer.vocab on 2026-08-03; this previously omitted all 8
# WHISPER_TO_MMS_OVERRIDES languages, so a fresh process transcribing e.g.
# Swahili first would use whatever adapter happened to already be loaded).
MMS_LANGUAGE_CODES = {
    "kik": "kik",
    "nus": "nus",
    "ful": "ful",
    "am": "amh",
    "ha": "hau",
    "ln": "lin",
    "mg": "mlg",
    "sn": "sna",
    "so": "som",
    "sw": "swh",
    "yo": "yor",
}


class _Info:
    def __init__(self, language, language_probability=None):
        self.language = language
        self.language_probability = language_probability


class MMSEngine(TranscriptionEngine):
    """
    Meta MMS (facebook/mms-1b-all): CTC-based ASR covering 1,100+ languages.

    ** NON-COMMERCIAL LICENSE (CC-BY-NC-4.0). ** Used only as a last-resort
    engine for languages no other engine on this platform covers (Kikuyu,
    Nuer, Fulfulde). This was an explicit, user-approved tradeoff -- do not
    route additional languages here without revisiting the license question.

    MMS is a CTC model, not sequence-to-sequence -- it has no built-in
    segment timestamps the way Whisper does. We chunk the audio into fixed
    windows ourselves so the rest of the pipeline still gets real
    {start, end} segments instead of one undifferentiated blob of text.
    """

    MODEL_ID = "facebook/mms-1b-all"
    CHUNK_SECONDS = 20.0
    _model = None
    _processor = None
    _loaded_adapter = None

    @classmethod
    def _load(cls):
        if cls._model is None:
            from transformers import AutoProcessor, Wav2Vec2ForCTC
            logger.info(f"Loading MMS engine '{cls.MODEL_ID}' (first use; non-commercial license)...")
            try:
                # Once the model is fully cached locally, this skips every
                # "is there a newer version" HTTP round-trip to the HF Hub
                # (observed: ~20+ HEAD/GET requests, ~35s) and loads straight
                # from disk. Falls back to a normal network-checking load if
                # the local cache is missing or incomplete (e.g. first run).
                cls._processor = AutoProcessor.from_pretrained(cls.MODEL_ID, local_files_only=True)
                cls._model = Wav2Vec2ForCTC.from_pretrained(cls.MODEL_ID, local_files_only=True)
            except Exception:
                cls._processor = AutoProcessor.from_pretrained(cls.MODEL_ID)
                cls._model = Wav2Vec2ForCTC.from_pretrained(cls.MODEL_ID)
            cls._model.eval()
            logger.info("MMS engine loaded.")
        return cls._model, cls._processor

    def transcribe(
        self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        import torch
        import soundfile as sf

        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio file not found: {audio_path}")

        model, processor = self._load()

        mms_lang = MMS_LANGUAGE_CODES.get(language)
        if mms_lang and mms_lang != MMSEngine._loaded_adapter:
            processor.tokenizer.set_target_lang(mms_lang)
            model.load_adapter(mms_lang)
            MMSEngine._loaded_adapter = mms_lang

        self.last_info = _Info(language=language or "auto")

        audio, sample_rate = sf.read(audio_path)
        if sample_rate != 16000:
            raise RuntimeError(f"MMS expects 16kHz audio, got {sample_rate}Hz")

        chunk_samples = int(self.CHUNK_SECONDS * sample_rate)
        total_samples = len(audio)

        for start_sample in range(0, total_samples, chunk_samples):
            end_sample = min(start_sample + chunk_samples, total_samples)
            window = audio[start_sample:end_sample]
            if len(window) < sample_rate * 0.3:  # skip trailing scraps under 0.3s
                continue

            inputs = processor(window, sampling_rate=16000, return_tensors="pt")
            with torch.no_grad():
                logits = model(**inputs).logits
            ids = torch.argmax(logits, dim=-1)[0]
            text = processor.decode(ids).strip()
            if not text:
                continue

            yield {
                "text": text,
                "start": round(start_sample / sample_rate, 2),
                "end": round(end_sample / sample_rate, 2),
                "language": language or "auto",
                "confidence": None,
            }
