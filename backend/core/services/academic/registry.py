from .base import AcademicFormatter
from .chemistry import ChemistryFormatter
from .general import GeneralFormatter
from .mathematics import MathematicsFormatter
from .physics import PhysicsFormatter


class AcademicFormatterRegistry:
    """
    Selects the formatter for a detected subject. Mirrors
    core.services.translation.registry.TranslationEngineRegistry's exact
    pattern: one cached instance per subject, a single select() entry
    point, adding a future subject means adding one branch here.

    STEM_MIXED uses the MathematicsFormatter -- its rules are the most
    general of the three (physics is a superset of math's rules, chemistry
    is unrelated notation) and a transcript classified STEM_MIXED by
    definition has meaningfully mixed content the detector wasn't
    confident enough to pin to one subject, so the broadest-coverage
    formatter is the safer default.
    """

    _instances = {}

    @classmethod
    def select(cls, subject: str) -> AcademicFormatter:
        if subject not in cls._instances:
            cls._instances[subject] = cls._build(subject)
        return cls._instances[subject]

    @staticmethod
    def _build(subject: str) -> AcademicFormatter:
        if subject == 'MATHEMATICS':
            return MathematicsFormatter()
        if subject == 'PHYSICS':
            return PhysicsFormatter()
        if subject == 'CHEMISTRY':
            return ChemistryFormatter()
        if subject == 'STEM_MIXED':
            return MathematicsFormatter()
        return GeneralFormatter()
