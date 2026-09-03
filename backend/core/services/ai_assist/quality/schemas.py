"""
Forced-JSON contracts for the quality stages.

Same reasoning as ai_assist/schemas.py, applied to a harder problem. There,
schema constraint stops the model wrapping segments in prose. Here it stops
something worse: spec 17 explicitly forbids deciding acceptance by looking
for "## FINAL TRANSLATION" or a validation tick in free text, because that
turns a formatting accident into a submission decision. A declared schema
means the auditor's verdict arrives as typed fields the gate can compare,
never as a sentence the gate has to interpret.

Schema validity still guarantees only SHAPE, never truth (the point
ai_assist/schemas.py makes about invented segments). A schema-valid audit
can still under-report. That is why the deterministic checks in
deterministic.py run alongside rather than after, and why the acceptance
gate reads the issue list rather than the model's own `status` field.

WHY EVERY SCHEMA IS FLAT-ISH AND SMALL: Gemini's responseSchema is a subset
of OpenAPI 3.0 with no $ref, so a shared "issue" definition has to be
repeated by construction. `_ISSUE_SCHEMA` is built once in Python and
embedded into each parent, which keeps the duplication in one place.
"""

# ---------------------------------------------------------------------- #
# Source analysis (stage: analysis)
# ---------------------------------------------------------------------- #

ANALYSIS_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'domain': {'type': 'STRING'},
        'subdomain': {'type': 'STRING'},
        'register': {'type': 'STRING'},
        'audience': {'type': 'STRING'},
        # Free-form, NOT an enum. An enum here would cap the system at the
        # domains we thought of, which is the failure spec 1 and RULE 8 are
        # written to prevent -- the model must be able to answer
        # "veterinary epidemiology" without us having predicted it.
        'risk_level': {
            'type': 'STRING',
            'enum': ['low', 'standard', 'high', 'critical'],
        },
        'is_transcript': {'type': 'BOOLEAN'},
        'formatting_notes': {'type': 'STRING'},
        'confidence': {'type': 'NUMBER'},
        'notes': {'type': 'STRING'},
        'terminology': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'source_term': {'type': 'STRING'},
                    'target_term': {'type': 'STRING'},
                    'context': {'type': 'STRING'},
                    'confidence': {'type': 'NUMBER'},
                    # Renderings that would collapse a distinction the
                    # source draws -- the fiduciary-duty/duty-of-care case
                    # generalized. Carried into the translation prompt as a
                    # negative constraint, which is far more effective than
                    # hoping a positive one is enough.
                    'forbidden_variants': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                },
                'required': ['source_term', 'target_term'],
                'propertyOrdering': [
                    'source_term', 'target_term', 'context', 'confidence',
                    'forbidden_variants',
                ],
            },
        },
        'entities': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'surface_form': {'type': 'STRING'},
                    'entity_type': {'type': 'STRING'},
                    'normalized': {'type': 'STRING'},
                    # Drives spec 10's tiering. LOW confidence means the
                    # source spelling is KEPT, not corrected -- inventing a
                    # plausible name is worse than preserving an odd one.
                    'confidence': {'type': 'NUMBER'},
                    'evidence': {'type': 'STRING'},
                },
                'required': ['surface_form', 'entity_type'],
                'propertyOrdering': [
                    'surface_form', 'entity_type', 'normalized', 'confidence',
                    'evidence',
                ],
            },
        },
    },
    'required': ['domain', 'register', 'risk_level', 'confidence'],
    'propertyOrdering': [
        'domain', 'subdomain', 'register', 'audience', 'risk_level',
        'is_transcript', 'formatting_notes', 'confidence', 'notes',
        'terminology', 'entities',
    ],
}


# ---------------------------------------------------------------------- #
# Audit (stages: audit, re_audit)
# ---------------------------------------------------------------------- #

_ISSUE_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        # Positional index within the reviewed chunk, matching the ids the
        # payload was sent with. Never a database identifier.
        'segment_index': {'type': 'INTEGER'},
        'category': {
            'type': 'STRING',
            'enum': [
                'semantic', 'completeness', 'terminology', 'entity',
                'numeric', 'domain', 'linguistic', 'typography',
                'structure', 'transcript', 'leakage',
            ],
        },
        'severity': {
            'type': 'STRING',
            'enum': ['critical', 'high', 'medium', 'low'],
        },
        'source_span': {'type': 'STRING'},
        'target_span': {'type': 'STRING'},
        'problem': {'type': 'STRING'},
        'correction': {'type': 'STRING'},
        'rule': {'type': 'STRING'},
        'confidence': {'type': 'NUMBER'},
    },
    'required': ['category', 'severity', 'problem'],
    'propertyOrdering': [
        'segment_index', 'category', 'severity', 'source_span',
        'target_span', 'problem', 'correction', 'rule', 'confidence',
    ],
}

AUDIT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        # The auditor's own verdict. RECORDED FOR TELEMETRY, NEVER READ BY
        # THE GATE (RULE 12). engine.py derives acceptance from `issues`
        # alone, so a model that returns status='clean' while listing a
        # CRITICAL issue is refused on the issue -- and the disagreement is
        # visible in the stored report afterwards.
        'status': {
            'type': 'STRING',
            'enum': ['clean', 'issues_found'],
        },
        'confidence': {'type': 'NUMBER'},
        'issues': {'type': 'ARRAY', 'items': _ISSUE_SCHEMA},
    },
    'required': ['status', 'issues'],
    'propertyOrdering': ['status', 'confidence', 'issues'],
}


# ---------------------------------------------------------------------- #
# Repair (stage: repair)
# ---------------------------------------------------------------------- #

REPAIR_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'segments': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'segment_index': {'type': 'INTEGER'},
                    'repaired_text': {'type': 'STRING'},
                    # Which findings this edit addresses, and why. Not
                    # decoration: it is the only way to notice a repair pass
                    # that rewrote prose it was not asked to touch, and it
                    # lands in the stored report for the audit trail.
                    'addressed': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                    'unchanged': {'type': 'BOOLEAN'},
                },
                'required': ['segment_index', 'repaired_text'],
                'propertyOrdering': [
                    'segment_index', 'repaired_text', 'addressed', 'unchanged',
                ],
            },
        },
    },
    'required': ['segments'],
    'propertyOrdering': ['segments'],
}
