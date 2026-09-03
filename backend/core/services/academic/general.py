from typing import Tuple

from .base import AcademicFormatter


class GeneralFormatter(AcademicFormatter):
    """
    Used when the detector classifies a transcript as GENERAL (no
    significant math/physics/chemistry content, or confidence too low to
    trust a subject-specific formatter). Deliberately a pure passthrough --
    never converts anything -- rather than e.g. running all three
    subject formatters "just in case", which would risk exactly the
    over-eager conversion the ticket explicitly warns against for
    general lecture content.
    """

    def format_segment(self, text: str) -> Tuple[str, bool]:
        return text, False
