import React, { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Card, Chip, EmptyState, ExpandableText, FOCUS } from '../components/ui/index.jsx';
import { CheckIcon, ClockIcon, AlertIcon } from '../components/icons.jsx';
import { dateTime, taskLabel } from '../lib/format.js';

/**
 * What the platform decided about work this person already sent.
 *
 * WHY A WORKER NEEDS THIS AT ALL. Until the sync learned to read Eagle's
 * awarded feed, a submitted task's status froze at "submitted" for ever:
 * the platform knew perfectly well whether the work was accepted, and
 * nobody here ever found out. A tasker could repeat the same mistake for a
 * week without a single signal that anything was wrong.
 *
 * SO THE REJECTION REASON IS THE POINT, not the counts. The counts tell
 * somebody how they are doing; the reason is the only thing that tells them
 * what to change. It is shown in full, in the platform's own words, next to
 * the task it belongs to.
 *
 * PENDING IS A STATE, NOT AN ABSENCE. Review takes up to about 32 hours, so
 * most of a day's work sits undecided. Showing that explicitly is what stops
 * "no news" reading as "something went wrong".
 */

//: The business's own stated target -- see worker_prompts.py's rationale
//: comments for the prompt-tightening work this number motivated.
const PASS_RATE_TARGET = 92;

const DECIDED = {
  approved: {
    label: 'Reviewed',
    tone: 'ready',
    icon: <CheckIcon className="h-3.5 w-3.5" />,
  },
  rejected: {
    label: 'Review failed',
    tone: 'danger',
    icon: <AlertIcon className="h-3.5 w-3.5" />,
  },
  submitted: {
    label: 'Awaiting review',
    tone: 'neutral',
    icon: <ClockIcon className="h-3.5 w-3.5" />,
  },
};

/** When the platform decided, or when we sent it if it has not. */
function decidedAt(task) {
  return task.approved_at || task.rejected_at || task.submitted_at || task.updated_at;
}

export default function ReviewOutcomes({ tasks = [], loading = false }) {
  const rows = useMemo(
    () => tasks
      .filter((task) => DECIDED[task.status])
      .sort((a, b) => String(decidedAt(b) || '').localeCompare(String(decidedAt(a) || ''))),
    [tasks],
  );

  const counts = useMemo(() => {
    const tally = { approved: 0, rejected: 0, submitted: 0 };
    rows.forEach((task) => { tally[task.status] += 1; });
    return tally;
  }, [rows]);

  const decided = counts.approved + counts.rejected;
  // 92% is the business's own stated pass-rate target -- shown here so a
  // tasker sees the same bar the platform is judged against, not just an
  // unlabelled fraction. NULL, not just unstyled, below 8 decided tasks: a
  // rate computed from one or two outcomes swings wildly on the very next
  // one, and a precise-looking "50%" from a single rejection would read as
  // a verdict it is not.
  const passRate = decided >= 8 ? Math.round((counts.approved / decided) * 100) : null;
  const belowTarget = passRate !== null && passRate < PASS_RATE_TARGET;

  return (
    <Card
      title="Your reviews"
      subtitle={
        decided ? (
          <>
            {counts.approved} of {decided} accepted so far
            {passRate !== null && (
              <span className={belowTarget ? 'font-semibold text-status-rejected' : undefined}>
                {' '}({passRate}%, target {PASS_RATE_TARGET}%+)
              </span>
            )}
          </>
        ) : (
          'What the platform decided about work you sent'
        )
      }
      padded={false}
    >
      <div className="grid grid-cols-3 gap-px border-b border-edge bg-edge">
        <Tally label="Reviewed" value={counts.approved} tone="text-money-ink" />
        <Tally label="Review failed" value={counts.rejected} tone="text-status-rejected" />
        <Tally label="Awaiting review" value={counts.submitted} tone="text-ink-dim" />
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={loading ? 'Loading…' : 'Nothing submitted yet'}
          message="Once you submit a task it appears here, and stays until the platform decides."
        />
      ) : (
        <ul className="divide-y divide-edge">
          {rows.map((task) => {
            const meta = DECIDED[task.status];
            return (
              <li key={task.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Link
                    to={`/tasks/${task.id}`}
                    className={`truncate text-xs font-medium text-ink hover:text-accent ${FOCUS} rounded`}
                  >
                    {taskLabel(task)}
                  </Link>
                  <Chip tone={meta.tone} icon={meta.icon}>{meta.label}</Chip>
                </div>

                <p className="mt-1 text-2xs text-ink-mute">
                  {task.status === 'submitted' ? 'Sent ' : 'Decided '}
                  {dateTime(decidedAt(task))}
                  {task.kind ? ` · ${task.kind}` : ''}
                </p>

                {/* THE REASON IS THE ONLY PART OF THIS PANEL THAT CAN CHANGE
                    WHAT SOMEBODY DOES NEXT -- but a real rejection can carry
                    eight or more numbered items, and showing every one of
                    them in full on every row pushed the rest of the page
                    below the fold the moment there was more than a
                    rejection or two. Collapsed to a preview by default,
                    same as the admin's "Recent rejections" panel; nothing
                    is shortened, only hidden behind an explicit click. */}
                {task.status === 'rejected' && task.rejection_reason && (
                  <div className="mt-2 rounded-lg border border-status-rejected/30
                                  bg-status-rejected/10 px-3 py-2 text-2xs leading-relaxed text-ink">
                    <span className="font-semibold">Why:</span>
                    <ExpandableText text={task.rejection_reason} className="mt-1" />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function Tally({ label, value, tone }) {
  return (
    <div className="bg-surface px-4 py-3 text-center">
      <div className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="mt-0.5 text-2xs text-ink-mute">{label}</div>
    </div>
  );
}
