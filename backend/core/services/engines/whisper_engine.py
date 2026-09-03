from typing import Any, Dict, Generator, Optional

from .base import TranscriptionEngine

try:
    from backend.core.services.ai_pipeline import WhisperTranscriber
except ModuleNotFoundError:
    from core.services.ai_pipeline import WhisperTranscriber


class WhisperEngine(TranscriptionEngine):
    """
    Thin adapter around the existing, untouched WhisperTranscriber. This is
    the platform's default engine -- used for every language it wasn't
    explicitly proven to need a different engine for.
    """

    def __init__(self, model_size: str = "base"):
        super().__init__()
        self._impl = WhisperTranscriber(model_size=model_size)

    def transcribe(
        self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        for chunk in self._impl.transcribe(audio_path, enable_vad=enable_vad, language=language):
            self.last_info = self._impl.last_info
            yield chunk
        self.last_info = self._impl.last_info
