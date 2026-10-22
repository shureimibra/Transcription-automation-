import React from 'react';

/**
 * What the gate found, and whether any of it costs you the task.
 *
 * WHY THIS IS NOT JUST A LIST. Every non-critical finding used to render in
 * the same amber box, which asked a tasker to judge, one second before an
 * irreversible click, which of five warnings would get the work rejected.
 * They have no way to know that, and guessing wrong is expensive in both
 * directions -- submit and lose the task, or stall and lose the hour.
 *
 * WE NOW HAVE THE ANSWER, MEASURED. Task 4452 was accepted by the platform
 * carrying three findings: an altered identifier, a foreign word and a
 * missing non-breaking space. Severity below `critical` demonstrably does
 * not cost a task. That is a fact worth putting on the screen rather than
 * leaving in a commit message.
 *
 * THE SERVER STILL DECIDES. `blocking` is its verdict and this component
 * only reports it -- the ranking below orders the list, it never overrides
 * whether the submit button is live.
 *
 * ONE COMPONENT FOR BOTH PRODUCTS, because a transcription and a translation
 * are the same irreversible decision made against the same rubric. Two
 * renderings of the same verdict is two chances for them to disagree.
 */

const SEVERITY = {
  critical: { rank: 0, label: 'Blocking', className: 'bg-status-rejected/10 text-status-rejected ring-status-rejected/30' },
  high:     { rank: 1, label: 'High',     className: 'bg-status-review/10 text-status-review ring-status-review/30' },
  medium:   { rank: 2, label: 'Medium',   className: 'bg-overlay text-ink-dim ring-edge' },
  low:      { rank: 3, label: 'Low',      className: 'bg-overlay text-ink-mute ring-edge' },
};

export default function Verdict({ findings = [], blocking = false, className = '' }) {
  const ranked = [...findings].sort(
    (a, b) => (SEVERITY[a.severity]?.rank ?? 9) - (SEVERITY[b.severity]?.rank ?? 9)
  );
  const notes = ranked.filter((f) => f.severity !== 'critical').length;
  const stoppers = ranked.length - notes;

  return (
    <div className={`space-y-2 ${className}`}>
      {blocking ? (
        <p className="rounded-xl2 border border-status-rejected/30 bg-status-rejected/10 px-4 py-3 text-xs text-ink">
          <span className="font-semibold">
            {stoppers === 1
              ? 'One finding in this document blocks submission.'
              : `${stoppers || 'These'} findings in this document block submission.`}
          </span>{' '}
          Fix these, then preview again. Submitting as it stands risks a rejection,
          which costs the task and the minutes it was bought with.
        </p>
      ) : ranked.length === 0 ? (
        <p className="rounded-xl2 border border-money/25 bg-money-soft px-4 py-3 text-xs text-money-ink">
          <span className="font-semibold">No findings in this document.</span> It matches the
          format the platform has accepted.
        </p>
      ) : (
        <p className="rounded-xl2 border border-edge bg-overlay px-4 py-3 text-xs text-ink-dim">
          <span className="font-semibold text-ink">Nothing in this document blocks submission.</span>{' '}
          {notes === 1 ? 'One note' : `${notes} notes`} to glance at first — worth a look,
          not worth stalling for. Accepted work regularly carries findings at
          these levels.
        </p>
      )}

      {ranked.length > 0 && (
        <ul className="space-y-1.5">
          {ranked.map((finding, i) => {
            const meta = SEVERITY[finding.severity] || SEVERITY.low;
            return (
              <li
                key={i}
                className="flex items-start gap-2.5 rounded-lg border border-edge bg-surface px-3 py-2"
              >
                <span
                  className={`mt-px shrink-0 rounded-md px-1.5 py-0.5 text-2xs font-semibold uppercase
                    tracking-wide ring-1 ${meta.className}`}
                >
                  {meta.label}
                </span>
                <span className="text-2xs leading-relaxed text-ink">{finding.message}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
