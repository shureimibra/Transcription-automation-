"""
Shared contract for academic formatters (mathematics.py/physics.py/
chemistry.py/general.py). Mirrors core.services.translation.base's
TranslationEngine -- one small interface, one concrete class per subject,
selected via registry.py.
"""
from abc import ABC, abstractmethod
from typing import Tuple


class AcademicFormatter(ABC):
    """
    format_segment operates on ONE segment's text at a time -- never
    concatenated across segments (see run_academic_format_pipeline's own
    docstring for why) -- and must be conservative: return the text
    UNCHANGED (was_modified False) whenever nothing in it unambiguously
    matches a known spoken-technical pattern. There is no separate
    confidence gate layered on top of this; conservatism is a property of
    how narrowly each formatter's own rules are written, verified directly
    by tests that ordinary prose passes through byte-identical rather than
    just asserted in a docstring.
    """

    @abstractmethod
    def format_segment(self, text: str) -> Tuple[str, bool]:
        """Returns (formatted_text, was_modified)."""
        raise NotImplementedError
