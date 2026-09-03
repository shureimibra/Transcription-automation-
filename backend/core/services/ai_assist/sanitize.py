"""
Prompt-injection defense (RULE 5): transcript text is untrusted user data
and must never be able to escape its delimited block and be read as an
instruction to the model.

Three independent layers protect this, of which this module is only the
first -- deliberately, because no single layer is trustworthy alone:

  1. THIS MODULE: neutralizes delimiter look-alikes, so no segment can
     close the data block early or forge a new one.
  2. STRUCTURE (see prompts.build_payload below): segments are handed to
     the model as a JSON array, so json.dumps escapes every quote,
     newline, and control character on our behalf -- the model never sees
     raw interpolated prose.
  3. VALIDATION (see processor.AIPostProcessor): the response is a fixed
     schema keyed by segment_id, and any response whose ids don't match
     the request exactly is rejected outright. Even a model that fully
     obeyed an injected instruction cannot add, drop, reorder, or
     retarget a segment without failing that check.

Deliberately NOT attempted here: pattern-matching "ignore previous
instructions"-style phrasings. That's unbounded, trivially bypassed by
rewording, and would silently corrupt legitimate transcripts -- a lecture
*about* prompt injection is a perfectly valid thing to transcribe. The
structural guarantees above don't care what the text says.
"""
import re

# The sentinel strings prompts.py wraps untrusted content in.
TRANSCRIPT_OPEN = '<<<TRANSCRIPT_DATA>>>'
TRANSCRIPT_CLOSE = '<<<END_TRANSCRIPT_DATA>>>'

# Any run of 2+ angle brackets in either direction. Broader than the exact
# sentinels on purpose: it also defeats near-misses ("<<TRANSCRIPT_DATA>>",
# "<<<<END", ">>> ignore the above") that a strict equality check would let
# through, without needing to enumerate every variation.
_ANGLE_RUN_RE = re.compile(r'<{2,}|>{2,}')

# U+2039/U+203A (single guillemets) read almost identically to < and > for a
# human reading the transcript, but are ordinary letters to the parser -- so
# the substitution stays legible rather than mangling the text into noise.
_REPLACEMENTS = {'<': '‹', '>': '›'}


def _neutralize(match: 're.Match') -> str:
    run = match.group(0)
    return _REPLACEMENTS[run[0]] * len(run)


def sanitize_segment_text(text: str) -> str:
    """
    Returns `text` with delimiter-shaped angle-bracket runs replaced by
    visually-similar non-syntactic characters.

    Only ever applied to the COPY embedded in a prompt -- never to what's
    stored. AIRevisionSegment.original_text keeps the transcript verbatim,
    so this can never corrupt the user's data or the diff they review.
    """
    if not text:
        return text
    return _ANGLE_RUN_RE.sub(_neutralize, text)
