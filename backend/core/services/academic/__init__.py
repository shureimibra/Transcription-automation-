"""
Academic Content Formatting -- an optional, on-demand post-processing pass
over a job's already-completed transcript, converting spoken technical
phrases into notation ("x squared" -> "x²", "H two O" -> "H₂O"). Entirely
separate from, and never invoked by, the transcription pipeline itself
(ai_pipeline.py/tasks.run_transcription_pipeline) -- see
apps.transcriptions.tasks.run_academic_format_pipeline for the one place
this package is actually driven from, mirroring how the Translation
pipeline is a similarly separate, on-demand pass.

Pipeline shape:

    TranscriptSegment rows (already produced by ASR)
        |
        v
    SubjectDetector.detect()        -- core.services.academic.detector
        |
        v
    AcademicFormatterRegistry.select(subject)  -- .registry
        |                                          (.mathematics/.physics/
        |                                           .chemistry/.general)
        v
    validate() per segment           -- .validator
        |
        v
    AcademicSegment rows

Every formatter emits plain Unicode directly -- there is no LaTeX anywhere
in this package, deliberately (see mathematics.py's module docstring): the
stored/exported text is always immediately usable by every export format
with zero extra conversion step.
"""
from .detector import SubjectDetector
from .hashing import compute_source_text_hash
from .registry import AcademicFormatterRegistry
from .validator import validate

__all__ = [
    'SubjectDetector', 'AcademicFormatterRegistry', 'validate', 'compute_source_text_hash',
]
