import React from 'react';
import { Button } from './ui/index.jsx';
import { CheckIcon, ClockIcon } from './icons.jsx';

/**
 * What a worker sees the moment their work is delivered.
 *
 * WHY THIS EXISTS. Submitting used to close the dialog and return you to a
 * task list. Nothing said "that worked". After an hour on the clock, with a
 * platform whose review takes up to a day and a half, silence is the one
 * response a person cannot act on — and this application has already told a
 * worker "the platform could not be reached" about work that had in fact
 * arrived. Ambiguity here is expensive, so the confirmation is explicit and
 * it says what happens next.
 *
 * IT ALSO SAYS DO NOT SUBMIT AGAIN. That is the instinct a silent success
 * creates, and a second submission of delivered work is exactly what we do
 * not want anyone reaching for.
 */
export default function SubmittedPanel({ task, onClose }) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-full
                       bg-money-soft text-money-ink">
        <CheckIcon className="h-7 w-7" />
      </span>

      <h3 className="text-base font-semibold text-ink">Submitted to the platform</h3>
      <p className="mt-1.5 max-w-md text-xs leading-relaxed text-ink-dim">
        {task?.external_task_id
          ? `Task ${task.external_task_id} has been delivered. `
          : 'Your work has been delivered. '}
        There is nothing else to do on it, and nothing more to send — please do
        not submit it again.
      </p>

      <p className="mt-4 flex items-center gap-2 rounded-lg bg-overlay px-3 py-2 text-2xs text-ink-dim">
        <ClockIcon className="h-3.5 w-3.5 shrink-0" />
        Review can take up to about 32 hours. The task moves to Approved or
        Rejected on its own — you do not need to wait on this screen.
      </p>

      <div className="mt-6">
        <Button variant="primary" onClick={onClose}>Take another task</Button>
      </div>
    </div>
  );
}
