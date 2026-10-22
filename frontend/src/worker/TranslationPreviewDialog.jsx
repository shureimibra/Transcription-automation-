import React, { useCallback, useState } from 'react';
import { useWorkspaceApi } from './workspaceApi.js';
import { useAsync } from '../hooks/useAsync.js';
import Verdict from '../components/Verdict.jsx';
import { isDelivered } from '../components/DeliveredBanner.jsx';
import SubmittedPanel from '../components/SubmittedPanel.jsx';
import { Button, ErrorState, Skeleton } from '../components/ui/index.jsx';

// Moved out of TranslationWorkspace.jsx unchanged: workers only. The Eagle
// tasker bundle shares TranslationWorkspace and must not carry a Submit
// path, so this dialog reaches the screen through workerApi.js, never by
// direct import.

/**
 * The document, as the server will send it. Fetched, never built here --
 * a second formatter in the browser could disagree with the one that
 * submits, and the disagreement would arrive as a rejection an hour later.
 */
export default function TranslationPreviewDialog({ task, onClose, onSubmitted }) {
  const { submission } = useWorkspaceApi();
  const load = useCallback(() => submission.preview(task.id), [task.id]);
  const { data, error, loading } = useAsync(load, [task.id]);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState('');
  const [sent, setSent] = useState(null);

  // A refused preview is blocking in its own right: if the document could
  // not be rendered, there is nothing to send.
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

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="tpreview-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <div className="border-b border-edge px-5 py-4">
          <h2 id="tpreview-title" className="text-sm font-semibold text-ink">
            What will be submitted
          </h2>
          <p className="mt-0.5 text-2xs text-ink-mute">
            Rendered by the server — this is exactly what the platform receives.
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {loading && <Skeleton className="h-40 rounded-lg" />}
          {notClaimed && (
            <p role="alert" className="mb-3 rounded-lg border border-status-review/50 bg-status-review/10 px-3 py-2 text-xs text-status-review">
              Claim this task first. You can preview it, but only a task you have claimed can be submitted.
            </p>
          )}
          {error && <ErrorState message={error.message} compact />}
          {data && (
            <>
              <Verdict
                findings={data.findings || []}
                blocking={Boolean(data.blocking)}
                className="mb-4"
              />
              <pre className="whitespace-pre-wrap rounded-lg border border-edge bg-canvas p-3 font-mono text-2xs text-ink">
                {data.content}
              </pre>
            </>
          )}
          {failure && (
            <p role="alert" className="mt-3 text-2xs text-status-rejected">{failure}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-edge px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>Back</Button>
          <Button
            variant="primary"
            onClick={send}
            loading={submitting}
            disabled={blocking || loading}
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
      </div>
    </div>
  );
}
