from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Optional


class TranscriptionEngine(ABC):
    """
    Common interface every transcription engine implements. The rest of the
    application (tasks.py, models, exports) only ever talks to this
    interface -- it never knows or needs to know which underlying engine
    (Whisper, Simba, MMS, or a future addition) produced a transcript.
    """

    def __init__(self):
        # Populated once transcribe() starts running: an object exposing
        # .language and .language_probability, mirroring faster-whisper's
        # TranscriptionInfo so callers (tasks.py) don't need engine-specific handling.
        self.last_info = None

    @abstractmethod
    def transcribe(
        self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Yields {text, start, end, language, confidence} dicts, in order."""
        raise NotImplementedError
