import React, { useEffect, useState } from 'react';
import { tasks as tasksApi } from '../api/workforce.js';
import { Button, ErrorState } from './ui/index.jsx';
import PromptDialog from './PromptDialog.jsx';
import { CAPABILITY, useAuth } from '../auth/AuthContext.jsx';

/**
 * The buttons for one task, derived from the server's own answer.
 *
 * THE SERVER DECIDES WHAT IS POSSIBLE. Every task carries
 * `available_transitions` -- computed by the state machine for THIS caller's
 * role -- and this component renders exactly those. It never infers a button
 * from a status string, so a workflow change in states.py reaches the UI
 * without a matching frontend edit, and the UI can never offer an action the
 * server would refuse.
 *
 * A CONFLICT IS NOT AN ERROR TO APOLOGISE FOR. 409 means someone else got
 * there first; the task is re-fetched and the message says what happened,
 * rather than showing a red failure for a system working correctly.
 */

// Target state -> how to reach it. Keyed by the transition the server
// offers, not by the current status, so one entry covers every path to a
// state (e.g. `start` serves claimed -> in_progress, rejected -> in_progress
// and failed -> in_progress alike).
const ACTIONS = {
  // CLAIMING A COLD TASK IS A DELIBERATE ACT, NOT AN ACCIDENT.
  //
  // Claiming spends platform minutes and starts an hour that cannot be
  // paused. On a WARM task that hour is spent reviewing a finished
  // document. On a cold one it is spent waiting for the machine, and if the
  // transcript never arrives there is nothing to submit at all -- the
  // minutes are simply gone.
  //
  // That is not hypothetical: three tasks were lost that way in one
  // morning. The card already showed "Ready now" on the warm ones, but a
  // chip is information, and information does not stop a hurried click on a
  // card that looks like every other card.
  //
  // Warm tasks claim in one click, exactly as before. Only a cold one asks.
  claimed:         { label: 'Claim',            call: (t) => tasksApi.claim(t.id), variant: 'accent',
                     confirmWhen: (t) => !t.is_ready,
                     confirm: {
                       title: 'This task is not ready yet',
                       body: 'The transcript has not been prepared, so claiming now '
                         + 'spends the minutes and starts the one-hour clock while the '
                         + 'machine is still working — and if it does not finish, there '
                         + 'is nothing to submit. Prefer a task marked "Ready now".',
                       confirmLabel: 'Claim anyway',
                     } },
  // THE LABEL DEPENDS ON WHERE YOU ARE, because this map is keyed by the
  // TARGET state and two different moves land on in_progress. From
  // `claimed` it is the forward step; from `review` it is a step BACK, and
  // calling both "Start work" produced a loop a worker could not read their
  // way out of: review -> "Start work" -> in_progress -> "Move to review"
  // -> review, with the actual exit ("Mark ready") sitting there unclicked.
  in_progress:     {
                     label: (t) => (t.status === 'review' ? 'Back to editing' : 'Start work'),
                     call: (t) => tasksApi.start(t.id),
                     // Only primary when it IS the forward step. A filled
                     // button on the way backwards is an invitation.
                     variant: (t) => (t.status === 'review' ? 'secondary' : 'primary'),
                   },
  review:          { label: 'Move to review',   call: (t) => tasksApi.beginReview(t.id) },
  ready_to_submit: { label: 'Mark ready',       call: (t) => tasksApi.ready(t.id, t.deliverable_revision), variant: 'primary' },
  // ADMIN ONLY, AND HONESTLY LABELLED. This endpoint calls
  // `services.mark_submitted`, which moves OUR state and sends nothing
  // anywhere -- its own docstring says the external call was meant to have
  // happened already.
  //
  // As a worker button beside "Mark ready" it read like the last step of
  // the flow, and its confirmation text claimed "this marks the task as
  // delivered to the external platform". A worker could therefore walk the
  // whole sequence, finish on this, and be told the work was delivered when
  // nothing had been sent. That is the exact failure eagle_submit.py's
  // docstring calls worse than a loud one: it looks like income that will
  // never arrive and nobody goes looking for it.
  //
  // Workers now have ONE way to submit -- Preview, then "Submit to
  // platform" -- which renders the document, runs the gate and calls Eagle
  // before recording anything. This stays for an administrator
  // reconciling a submission the platform accepted while our own state
  // failed to record it, which has happened twice.
  submitted:       { label: 'Record as sent (no send)', adminOnly: true,
                     call: (t) => tasksApi.submit(t.id, ''), confirm: {
                       title: 'Record this task as submitted?',
                       body: 'This ONLY updates our own record. Nothing is sent to the '
                         + 'platform. Use it when the platform already has the work but '
                         + 'our status did not update — never as a way to submit.',
                       confirmLabel: 'Record as sent',
                     } },
  new:             { label: 'Release',          call: (t, reason) => tasksApi.release(t.id, reason), prompt: {
                       title: 'Release this task',
                       body: 'The task returns to the queue for someone else to pick up.',
                       field: 'Reason (optional)',
                       required: false,
                       confirmLabel: 'Release',
                     } },
  approved:        { label: 'Approve',          call: (t, note) => tasksApi.approve(t.id, note), variant: 'primary', prompt: {
                       title: 'Approve this task',
                       body: 'Records the external platform’s decision.',
                       field: 'Note (optional)',
                       required: false,
                       confirmLabel: 'Approve',
                     } },
  rejected:        { label: 'Reject',           call: (t, reason) => tasksApi.reject(t.id, reason), variant: 'danger', prompt: {
                       title: 'Reject this task',
                       body: 'The worker sees this reason and can revise the task.',
                       field: 'Reason',
                       required: true,
                       confirmLabel: 'Reject',
                     } },
  cancelled:       { label: 'Cancel task',      call: (t, reason) => tasksApi.cancel(t.id, reason), variant: 'danger', prompt: {
                       title: 'Cancel this task',
                       body: 'Cancelling is final — the task cannot be reopened.',
                       field: 'Reason (optional)',
                       required: false,
                       confirmLabel: 'Cancel task',
                     } },
  // Reachable only for a system actor; never offered to a person.
  failed:          null,
  assigned:        null,
};

/** A field that may be a plain value or a function of the task. */
const resolve = (value, task) => (typeof value === 'function' ? value(task) : value);


/**
 * Whether this viewer is an administrator, tolerating no provider at all.
 *
 * `useAuth` throws outside an AuthProvider by design, and this component is
 * rendered bare in tests. The hook itself is still called unconditionally,
 * so hook order is stable; only the throw is absorbed.
 *
 * THE FALLBACK IS "NOT AN ADMIN", which is the safe direction: the only
 * thing this gates is an operator tool that records a submission WITHOUT
 * sending it. Hiding it from someone who should see it costs a trip to the
 * admin screen; showing it to someone who should not costs a task.
 */
function useIsAdmin() {
  try {
    const { can } = useAuth();
    return Boolean(can(CAPABILITY.ADMINISTRATION));
  } catch {
    return false;
  }
}


export default function TaskActions({
  task, onChange, onBusyChange, size = 'md', only = null, className = '',
}) {
  const isAdmin = useIsAdmin();
  const [pending, setPending] = useState(null);
  const [error, setError] = useState('');
  // A conflict is kept apart from an error on purpose -- see below.
  const [taken, setTaken] = useState('');
  const [dialog, setDialog] = useState(null);

  // CONFIRMED LIVE: a worker clicked Claim on a card, then immediately
  // clicked that same card's title to open the task -- before the claim
  // request had actually resolved. TaskCard's title link is not disabled
  // by TaskActions' own `pending` state (they did not know about each
  // other), so the navigation went through mid-flight, landed on the
  // task's own page while it was still, genuinely, unclaimed on the
  // server, and showed a live Claim button there too. A second click
  // followed. `onBusyChange` is how a parent that renders something else
  // clickable on the same card -- the title, here -- learns to hold off
  // until this component's own action has actually finished.
  useEffect(() => {
    onBusyChange?.(Boolean(pending));
  }, [pending, onBusyChange]);

  if (!task) return null;

  // An adminOnly action is hidden from workers even when the state machine
  // would allow it. The machine is about what MAY happen; this is about
  // what should be offered, and "record as sent without sending" is an
  // operator's reconciliation tool, not a step in anyone's workflow.
  const available = (task.available_transitions || [])
    .filter((target) => ACTIONS[target])
    .filter((target) => !ACTIONS[target].adminOnly || isAdmin)
    .filter((target) => (only ? only.includes(target) : true));

  const perform = async (target, input) => {
    setPending(target);
    setError('');
    setTaken('');
    try {
      const updated = await ACTIONS[target].call(task, input);
      onChange?.(updated);
    } catch (err) {
      if (err.isConflict) {
        // LOSING A RACE IS NOT AN ERROR, AND MUST NOT LOOK LIKE ONE.
        //
        // Six people sort by "Ready now" and reach for the same card, so
        // collisions are the normal shape of a busy morning, not a fault.
        // This used to render through ErrorState: a red panel, a warning
        // triangle, and a "Try again" link -- five taskers seeing what
        // looked like a broken system at the same moment, and the one
        // action offered was to retry a claim on a task that is gone.
        //
        // A separate, calm channel instead, and no retry.
        setTaken(err.message);
        try { onChange?.(await tasksApi.get(task.id)); } catch { /* keep the message */ }
      } else {
        setError(err.message);
      }
    } finally {
      setPending(null);
      setDialog(null);
    }
  };

  const activate = (target) => {
    const action = ACTIONS[target];
    // `confirmWhen` makes a confirmation conditional on the task rather than
    // on the action, so the common path stays one click.
    if (action.confirmWhen && !action.confirmWhen(task)) {
      perform(target);
      return;
    }
    if (action.prompt || action.confirm) {
      setDialog({ target, ...(action.prompt || action.confirm), isPrompt: Boolean(action.prompt) });
      return;
    }
    perform(target);
  };

  if (!available.length && !error && !taken) return null;

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-2">
        {available.map((target) => (
          <Button
            key={target}
            size={size}
            variant={resolve(ACTIONS[target].variant, task) || 'secondary'}
            loading={pending === target}
            disabled={Boolean(pending) && pending !== target}
            onClick={() => activate(target)}
          >
            {resolve(ACTIONS[target].label, task)}
          </Button>
        ))}
      </div>

      {error && (
        <ErrorState compact message={error} onRetry={() => setError('')} />
      )}

      {taken && (
        <p
          // `status`, not `alert`: this is information, not a problem to
          // interrupt anyone about.
          role="status"
          className="mt-2 flex items-start gap-2 rounded-lg border border-edge
                     bg-overlay px-3 py-2 text-2xs text-ink-dim"
        >
          <span aria-hidden="true" className="mt-px shrink-0">→</span>
          <span>
            <span className="font-medium text-ink">{taken}</span>{' '}
            The queue has been updated — take the next one.
          </span>
        </p>
      )}

      {dialog && (
        <PromptDialog
          title={dialog.title}
          body={dialog.body}
          fieldLabel={dialog.isPrompt ? dialog.field : null}
          required={dialog.required}
          confirmLabel={dialog.confirmLabel}
          busy={pending === dialog.target}
          onCancel={() => setDialog(null)}
          onConfirm={(value) => perform(dialog.target, value)}
        />
      )}
    </div>
  );
}
