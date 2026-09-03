import React from 'react';
import { CheckIcon, ClockIcon } from './icons.jsx';
import { dateTime } from '../lib/format.js';

/**
 * This task is finished and out of your hands.
 *
 * WHY A WHOLE BANNER RATHER THAN JUST HIDING THE BUTTON. A worker returning
 * to a submitted task sees a full editing surface -- a transcript, speaker
 * controls, a preview -- and nothing saying the work has gone. The server
 * refuses a second submission, so nothing breaks, but the person cannot tell
 * that from looking: they press Submit and get a red error for doing the
 * reasonable thing.
 *
 * The states this covers are the ones a WORKER can no longer act on.
 * `rejected` and `failed` are deliberately not among them: those come back
 * for rework, and the state machine offers the transitions to prove it.
 */

const DELIVERED = {
  submitted: {
    title: 'Submitted — waiting for review',
    body: 'The platform has this work. Nothing else is needed from you, and it '
      + 'cannot be sent again. Review can take up to about 32 hours; the task '
      + 'moves to Approved or Rejected on its own.',
  },
  approved: {
    title: 'Approved',
    body: 'The platform accepted this work. Nothing further is needed.',
  },
  cancelled: {
    title: 'Cancelled',
    body: 'This task was cancelled and is no longer yours to work on.',
  },
};

/** True when a worker can no longer act on this task. */
export function isDelivered(task) {
  return Boolean(DELIVERED[task?.status]);
}

export default function DeliveredBanner({ task }) {
  const state = DELIVERED[task?.status];
  if (!state) return null;

  const when = task.submitted_at || task.approved_at || task.updated_at;

  return (
    <div className="flex items-start gap-3 rounded-xl2 border border-money/25 bg-money-soft px-4 py-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full
                       bg-money text-white">
        <CheckIcon className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-xs font-semibold text-money-ink">{state.title}</p>
        <p className="mt-1 text-2xs leading-relaxed text-ink-dim">{state.body}</p>
        {when && (
          <p className="mt-1.5 flex items-center gap-1.5 text-2xs text-ink-mute">
            <ClockIcon className="h-3 w-3" />
            {dateTime(when)}
          </p>
        )}
      </div>
    </div>
  );
}
