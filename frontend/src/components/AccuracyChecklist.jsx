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
 */
export default function AccuracyChecklist({ findings, onFind, onApply, applying }) {
  if (!findings.length) return null;

  return (
    <div className="mt-4 space-y-2">
      <p className="text-2xs font-medium text-ink-mute">
        {findings.length} correction{findings.length === 1 ? '' : 's'} found in that reply
      </p>
      <ol className="space-y-2">
        {findings.map((finding, i) => (
          <li key={i} className="rounded-lg border border-edge bg-canvas p-3">
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
