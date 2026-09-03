"""
The forced-JSON response contract.

Gemini's responseSchema accepts a subset of OpenAPI 3.0 Schema (uppercase
type names, `propertyOrdering` to pin field order). Combined with
responseMimeType="application/json" it constrains decoding, so the model
cannot return prose, markdown fences, or commentary around the payload --
which is what makes parsing safe enough to drive database writes from.

Schema-level constraint is necessary but NOT sufficient: it guarantees
well-formed shape, never truthful content. A schema-valid response can
still invent, drop, or renumber segments, so processor.py independently
verifies id integrity on top of this (see SegmentIntegrityError).

DEVIATION FROM SPEC, deliberate: the spec's example response included
`original_text` echoed back per segment. That's omitted here. We already
hold the original server-side, so echoing it roughly doubles output tokens
for zero information gain -- on a long lecture that is the difference
between fitting a free-tier per-minute token budget and not -- and a model
paraphrasing the "original" it echoes is a whole class of confusing diff
bug that simply cannot occur if it's never asked for it. The API response
to the browser still includes original_text; it just comes from our DB
rather than from the model.
"""

SEGMENTS_KEY = 'segments'
SEGMENT_ID_KEY = 'segment_id'
SEGMENT_TEXT_KEY = 'processed_text'
SEGMENT_CHANGED_KEY = 'changed'

RESPONSE_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        SEGMENTS_KEY: {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    # Positional index within the request batch, NOT the
                    # database UUID -- see prompts.build_payload for why the
                    # provider is never shown internal identifiers.
                    SEGMENT_ID_KEY: {'type': 'INTEGER'},
                    SEGMENT_TEXT_KEY: {'type': 'STRING'},
                    # The model's own claim about whether it altered this
                    # segment. Treated as a hint only: processor.py compares
                    # the strings itself and trusts that comparison instead,
                    # so a model that mislabels `changed` cannot produce a
                    # wrong diff.
                    SEGMENT_CHANGED_KEY: {'type': 'BOOLEAN'},
                },
                'required': [SEGMENT_ID_KEY, SEGMENT_TEXT_KEY, SEGMENT_CHANGED_KEY],
                'propertyOrdering': [SEGMENT_ID_KEY, SEGMENT_TEXT_KEY, SEGMENT_CHANGED_KEY],
            },
        },
    },
    'required': [SEGMENTS_KEY],
    'propertyOrdering': [SEGMENTS_KEY],
}
