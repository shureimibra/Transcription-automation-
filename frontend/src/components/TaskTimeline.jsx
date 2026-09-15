import React, { useCallback } from 'react';
import { tasks as tasksApi } from '../api/workforce.js';
import { useAsync } from '../hooks/useAsync.js';
import { statusMeta } from './StatusBadge.jsx';
import { EmptyState, ErrorState, Skeleton } from './ui/index.jsx';
import { dateTime, relative } from '../lib/format.js';

/**
 * The audit log for one task, oldest at the bottom.
 *
 * THIS IS A READ OF THE RECORD, NOT A RECONSTRUCTION. Every row is a
 * TaskEvent the backend wrote inside the same transaction as the state
 * change. Nothing here is inferred from the task's current fields, so the
 * timeline shows what actually happened rather than a story consistent with
 * where the task ended up.
 *
 * Failed events are kept and marked, not hidden. An audit log that only
 * records successes answers the wrong question.
 */

const EVENT_LABELS = {
  TASK_CREATED: 'Task created',
  TASK_ASSIGNED: 'Assigned',
  TASK_UNASSIGNED: 'Unassigned',
  TASK_CLAIMED: 'Claimed',
  TASK_RELEASED: 'Released',
  TASK_STARTED: 'Work started',
  TRANSCRIPTION_STARTED: 'Transcription started',
  TRANSCRIPTION_COMPLETED: 'Transcription completed',
  AI_PROCESSING_STARTED: 'AI processing started',
  AI_PROCESSING_COMPLETED: 'AI processing completed',
  REVIEW_STARTED: 'Review started',
  READY_FOR_SUBMISSION: 'Marked ready',
  SUBMITTED: 'Submitted',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
  ACCURACY_CHECK_COMPLETED: 'Accuracy check pasted back',
  ACCURACY_CHECK_FINDING_APPLIED: 'Correction applied',
};

function label(event) {
  return EVENT_LABELS[event.event_type] || String(event.event_type).replace(/_/g, ' ').toLowerCase();
}

export default function TaskTimeline({ taskId, limit = null }) {
  const load = useCallback(() => tasksApi.events(taskId), [taskId]);
  const { data, error, loading, refresh } = useAsync(load, [taskId]);

  if (loading && !data) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-8" />)}
      </div>
    );
  }
  if (error) return <ErrorState message={error.message} onRetry={() => refresh()} compact />;

  const events = limit ? (data || []).slice(0, limit) : (data || []);
  if (!events.length) {
    return <EmptyState title="No history yet" message="Events appear here as the task moves." />;
  }

  return (
    <ol className="relative space-y-0">
      {events.map((event, index) => {
        const meta = event.to_status ? statusMeta(event.to_status) : null;
        return (
          <li key={event.id} className="relative flex gap-3 pb-4 last:pb-0">
            {/* The rail stops at the last item so it does not trail off. */}
            {index < events.length - 1 && (
              <span className="absolute left-[3px] top-3 h-full w-px bg-edge" aria-hidden="true" />
            )}
            <span
              className={`relative mt-1.5 h-[7px] w-[7px] shrink-0 rounded-full ${
                event.success === false ? 'bg-status-failed' : meta ? meta.dot : 'bg-ink-mute'
              }`}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-xs font-medium text-ink">{label(event)}</span>
                {event.success === false && (
                  <span className="text-2xs font-medium text-status-failed">did not complete</span>
                )}
                <span className="text-2xs text-ink-mute" title={dateTime(event.occurred_at)}>
                  {relative(event.occurred_at)}
                </span>
              </div>
              <div className="mt-0.5 text-2xs text-ink-mute">
                {event.actor_username ? `by ${event.actor_username}` : 'by the system'}
                {event.from_status && event.to_status && (
                  <> · {statusMeta(event.from_status).label} → {statusMeta(event.to_status).label}</>
                )}
              </div>
              {event.message && (
                <p className="mt-1 whitespace-pre-wrap break-words text-2xs text-ink-dim">
                  {event.message}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
