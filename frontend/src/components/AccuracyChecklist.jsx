import React from 'react';
import { Button } from './ui/index.jsx';

/**
 * Turns a pasted accuracy-check report into something a worker acts on
 * one click at a time, instead of reading a list and doing the find-and-
 * retype work by hand for every finding.
 *
 * WHY LISTENING STILL COMES FIRST. "Apply" writes the correction straight
 * to the segment -- it does not open an editable draft for a second look.
 * That is safe only because "Find it" is the step that gets a worker to
 * the actual audio; Apply is for once they have already listened and
 * decided the suggestion is right, not a substitute for listening. A
 * report's own confidence is not proof -- see worker_prompts.py's own
 * "not sure" convention -- so this never offers an "apply everything"
 * shortcut, only one finding at a time.
 *
 * `findings` is the array `parseFindings` returned, each annotated by the
 * caller with `applied` (a permanent, dateable record already exists --
 * see ACCURACY_CHECK_FINDING_APPLIED) and `found` (whether the phrase is
 * still present anywhere in the current transcript). A finding that is
 * neither found nor already applied most likely means the wording moved
 * since this report was generated -- shown as a plain note, not a dead
 * button pretending it still works.
 *
 * WHY `hasText` EXISTS, SEPARATELY FROM `findings`. Rendering nothing
 * when there are no findings is right when the box is simply empty -- but
 * it is indistinguishable from the feature never having been built when
 * the box has real text in it that just did not match a shape
 * parseFindings recognises. CONFIRMED LIVE: a worker's assistant replied
 * in a real third shape (bare bulleted triples, no quotes, no labels)
 * that the parser did not yet handle, the checklist rendered nothing at
 * all, and the reasonable conclusion from the outside was "this was
 * never implemented." `hasText` lets this component say what actually
 * happened instead of staying silent.
 */
export default function AccuracyChecklist({ findings, hasText = false, onFind, onApply, applying }) {
  if (!findings.length) {
    if (!hasText) return null;
    return (
      <p className="mt-4 text-2xs text-ink-mute" role="status">
        Nothing in that reply matched a shape this can turn into a checklist
        automatically — the text is still saved above, just not clickable.
        Work through it by hand, or ask your assistant to list each finding
        as: the phrase, then what it should probably be, then why.
      </p>
    );
  }

  return (
    <div className="mt-4 space-y-2">
      <p className="text-2xs font-medium text-ink-mute">
        {findings.length} correction{findings.length === 1 ? '' : 's'} found in that reply
      </p>
      <ol className="space-y-2">
        {findings.map((finding, i) => (
          <li key={i} className="rounded-lg border border-edge bg-canvas p-3">
            {/* NOTATION FINDINGS ARE FLAGGED UP FRONT, before the phrase
                itself -- the whole point is that a worker can tell at a
                glance this is a formatting fix and act on it without
                listening first, the opposite default from everything else
                on this list. See accuracyFindings.js's classifyReason. */}
            {finding.notation && (
              <span className="mb-1 inline-block rounded-full bg-accent/10 px-1.5 py-0.5
                                text-2xs font-medium text-accent">
                Notation fix — no audio check needed
              </span>
            )}
            <p className="text-xs text-ink">
              <span className="text-ink-mute line-through decoration-status-rejected/60">
                {finding.phrase}
              </span>
              {' → '}
              <span className="font-medium">{finding.suggestion}</span>
            </p>
            {finding.reason && (
              <p className="mt-1 text-2xs text-ink-mute">{finding.reason}</p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {finding.applied ? (
                <span className="text-2xs font-medium text-status-approved">
                  ✓ Applied{finding.appliedAt ? ` — ${finding.appliedAt}` : ''}
                </span>
              ) : !finding.found ? (
                <span className="text-2xs text-ink-mute">
                  Not found in the transcript — likely already fixed
                </span>
              ) : finding.spansSegments ? (
                // A SHORT-SEGMENT TASK MAKES THIS COMMON, NOT RARE.
                // Confirmed live: a phrase can straddle the exact point a
                // clause breaks into the next segment. Find it still
                // works -- it jumps straight to where the phrase starts --
                // but Apply cannot rewrite across two segments, so it is
                // not offered as a button that would only ever fail.
                <>
                  <Button size="sm" variant="secondary" onClick={() => onFind(finding)}>
                    Find it
                  </Button>
                  <span className="text-2xs text-ink-mute">
                    Spans two lines — edit both by hand
                  </span>
                </>
              ) : (
                <>
                  <Button size="sm" variant="secondary" onClick={() => onFind(finding)}>
                    Find it
                  </Button>
                  <Button
                    size="sm" variant="primary"
                    loading={applying === finding.phrase}
                    onClick={() => onApply(finding)}
                  >
                    Apply
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
