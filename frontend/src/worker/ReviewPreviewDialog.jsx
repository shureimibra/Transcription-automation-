import React, { useCallback, useState } from 'react';
import { useWorkspaceApi } from './workspaceApi.js';
import { useAsync } from '../hooks/useAsync.js';
import Verdict from '../components/Verdict.jsx';
import { isDelivered } from '../components/DeliveredBanner.jsx';
import SubmittedPanel from '../components/SubmittedPanel.jsx';
import { Button, ErrorState, Skeleton } from '../components/ui/index.jsx';

// Moved out of ReviewWorkspace.jsx unchanged: workers only. The Eagle tasker
// bundle shares ReviewWorkspace and must not carry a Submit path, so this
// dialog reaches the screen through workerApi.js, never by direct import.

/**
 * The last thing between a worker and a rejection.
 *
 * Shows the exact document the backend will send. Given that a rejection
 * costs the platform minutes and the worker's hour, five seconds of a human
 * looking at the real thing is the cheapest check available.
 */
export default function ReviewPreviewDialog({ task, onClose, onSubmitted }) {
  const { submission } = useWorkspaceApi();
  const load = useCallback(() => submission.preview(task.id), [task.id]);
  const { data, error, loading } = useAsync(load, [task.id]);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState('');
  const [sent, setSent] = useState(null);

  const send = async () => {
    setSubmitting(true);
    setFailure('');
    try {
      const updated = await submission.submit(task.id);
      setSent(updated || true);
      onSubmitted(updated);
    } catch (err) {
      setFailure(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  // NO PREVIEW MEANS NO SUBMIT.
  //
  // `blocking` only exists on a preview the server managed to RENDER. When
  // it refuses outright -- a translation task with nothing translated, a
  // task not yet in a submittable state -- the response is an error with no
  // `blocking` field at all, and reading it as false left the Submit button
  // blue and inviting underneath the very message explaining why the work
  // cannot be sent. The server refuses again, so nothing broken reaches the
  // platform, but the button was lying about what would happen.
  //
  // An error is therefore blocking in its own right: if we could not even
  // produce the document, there is nothing to send.
  // ALREADY DELIVERED MEANS NO SUBMIT BUTTON AT ALL.
  //
  // The server refuses a second submission, so nothing breaks -- but a live
  // button on a task that has already gone invites the click and answers it
  // with a red error for doing the reasonable thing. Read-only is the honest
  // shape for work that is out of your hands.
  const delivered = isDelivered(task);
  // A TASK NOBODY HAS CLAIMED CANNOT BE SUBMITTED. A worker may open and
  // even preview a task in the open pool to decide whether to take it;
  // sending it is a different act, and the server refuses it. Say so
  // here, before the click, rather than answering the click with a
  // vague refusal.
  const notClaimed = task?.status === 'new' || task?.status === 'assigned';
  const blocking = delivered || notClaimed || Boolean(error) || Boolean(data?.blocking);

  // DELIVERED. The dialog stays, and says so.
  if (sent) {
    return (
      <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
        <div
          role="dialog" aria-modal="true" aria-label="Submitted"
          className="w-full max-w-lg rounded-xl2 border border-edge bg-surface shadow-panel"
        >
          <SubmittedPanel task={task} onClose={onClose} />
        </div>
      </div>
    );
  }


  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog" aria-modal="true" aria-labelledby="preview-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <header className="border-b border-edge px-5 py-3">
          <h2 id="preview-title" className="text-sm font-semibold text-ink">
            What will be submitted
          </h2>
          <p className="mt-0.5 text-xs text-ink-mute">
            Rendered by the server — this is exactly what the platform receives.
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading && <Skeleton className="h-64" />}
          {notClaimed && (
            <p role="alert" className="mb-3 rounded-lg border border-status-review/50 bg-status-review/10 px-3 py-2 text-xs text-status-review">
              Claim this task first. You can preview it, but only a task you have claimed can be submitted.
            </p>
          )}
          {error && <ErrorState message={error.message} />}
          {failure && <ErrorState message={failure} />}

          {data && <Verdict findings={data.findings || []} blocking={Boolean(data.blocking)} />}

          {data && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-canvas p-3 font-mono text-2xs leading-relaxed text-ink-dim">
              {data.content}
            </pre>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-edge px-5 py-3">
          <span className="text-2xs text-ink-mute">
            {data ? `${data.words} words · ${data.characters} characters` : ''}
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={submitting}>Back</Button>
            <Button
              variant="primary"
              loading={submitting}
              disabled={blocking || loading}
              onClick={send}
              title={
                delivered
                  ? 'This work has already been submitted'
                  : notClaimed
                    ? 'Claim this task first'
                    : error
                    ? 'There is nothing to submit until this is resolved'
                    : blocking
                      ? 'Fix the critical findings first'
                      : undefined
              }
            >
              Submit to platform
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
