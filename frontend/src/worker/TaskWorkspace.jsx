import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { tasks as tasksApi } from '../api/workforce.js';
import { useAsync } from '../hooks/useAsync.js';
import { useTaskStream } from '../hooks/useTaskStream.js';
import { useAuth } from '../auth/AuthContext.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import TaskActions from '../components/TaskActions.jsx';
import TaskTimeline from '../components/TaskTimeline.jsx';
import Dashboard from '../views/Dashboard.jsx';
import ReviewWorkspace from './ReviewWorkspace.jsx';
import TranslationWorkspace from './TranslationWorkspace.jsx';
import { taskMedia } from '../api/eagle.js';
import { Card, ErrorState, FOCUS, Skeleton } from '../components/ui/index.jsx';
import { dateTime, duration, taskLabel, taskRoute } from '../lib/format.js';

/**
 * Where a task is actually worked on.
 *
 * THE STUDIO IS MOUNTED, NOT REBUILT. `Dashboard` is the existing
 * transcription/AI/WebSocket surface, unchanged apart from an `embedded`
 * flag that drops its own page chrome. Every one of its behaviours --
 * upload, URL intake, live segment streaming, AI assist, diff review,
 * exports -- works here because it is the same component, not a
 * reimplementation of it.
 *
 * THE TASK HEADER IS SEPARATE FROM THE STUDIO. Task state belongs to the
 * workforce API and the studio knows nothing about it; keeping them in
 * separate panels is what lets the studio stay untouched.
 */
export default function TaskWorkspace() {
  const { taskId } = useParams();
  const { isAdmin } = useAuth();
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(() => tasksApi.get(taskId), [taskId]);
  const { data: task, error, loading, refresh, setData } = useAsync(load, [taskId]);

  const onChange = useCallback((updated) => {
    if (updated) setData(updated);
  }, [setData]);

  // OPENING THE WORKSPACE IS STARTING WORK.
  //
  // "Start work" only ever moved claimed -> in_progress and stamped
  // `started_at`. It granted no exclusivity -- claiming already did that, on
  // both sides -- so to a worker it was a button that appeared to do nothing,
  // sitting between them and the task. Asked what it was for, the honest
  // answer was "bookkeeping".
  //
  // The timestamp is still worth having: someone who banks three tasks during
  // a drop and works them one at a time is genuinely in progress on one of
  // them, and `claimed_at` cannot say which. So the transition still happens
  // -- it is triggered by the thing that actually means it, rather than by
  // asking a person to announce it.
  //
  // ONLY FROM `claimed`, and only once, so a task in review is never dragged
  // backwards by somebody re-reading it.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoStarted.current || !task || task.status !== 'claimed') return;
    autoStarted.current = true;
    tasksApi.start(task.id).then(onChange).catch(() => {
      // A failed auto-start is not worth interrupting anyone for: the task
      // is claimed, the workspace is open, and Submit walks the state on its
      // own. Losing a timestamp does not deserve an error banner.
      autoStarted.current = false;
    });
  }, [task, onChange]);

  // Only this task. A worker sitting on a task wants to know when an admin
  // rejects or cancels it under them, and does not need a refetch every time
  // something unrelated moves in the queue.
  useTaskStream(useCallback((message) => {
    if (message.task === taskId) refresh({ silent: true });
  }, [taskId, refresh]));

  if (loading && !task) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-xl2" />
        <Skeleton className="h-64 rounded-xl2" />
      </div>
    );
  }

  if (error && !task) {
    return (
      <div className="space-y-4">
        <ErrorState message={error.message} onRetry={() => refresh()} />
        <Link to="/tasks" className={`inline-block rounded text-xs text-accent ${FOCUS}`}>
          Back to the task queue
        </Link>
      </div>
    );
  }

  if (!task) return null;

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-2xs text-ink-mute">
        <Link to="/tasks" className={`rounded hover:text-ink ${FOCUS}`}>Task queue</Link>
        <span aria-hidden="true"> / </span>
        <span className="text-ink-dim">{taskLabel(task)}</span>
      </nav>

      <section className="rounded-xl2 border border-edge bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-lg font-semibold text-ink">{taskLabel(task)}</h1>
              <StatusBadge status={task.status} />
            </div>
            <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-2xs text-ink-mute">
              <div className="flex gap-1.5">
                <dt className="text-ink-mute">Type</dt>
                <dd className="capitalize text-ink-dim">{task.kind}</dd>
              </div>
              <div className="flex gap-1.5">
                <dt>Languages</dt>
                <dd className="text-ink-dim">{taskRoute(task)}</dd>
              </div>
              {task.duration_seconds ? (
                <div className="flex gap-1.5">
                  <dt>Length</dt>
                  <dd className="tabular-nums text-ink-dim">{duration(task.duration_seconds)}</dd>
                </div>
              ) : null}
              {task.claimed_by_code && (
                <div className="flex gap-1.5">
                  <dt>Held by</dt>
                  <dd className="text-ink-dim">{task.claimed_by_code}</dd>
                </div>
              )}
              <div className="flex gap-1.5">
                <dt>Created</dt>
                <dd className="text-ink-dim">{dateTime(task.created_at)}</dd>
              </div>
            </dl>
          </div>
          <TaskActions task={task} onChange={onChange} />
        </div>

        {/* ADMIN ONLY. `source_url` is absent from the worker serializer
            entirely, so for a tasker this is not merely hidden -- the live
            Eagle URL never reaches the browser. */}
        {isAdmin && task.source_url && <SourceUrl url={task.source_url} />}

        {task.status === 'rejected' && task.rejection_reason && (
          <div className="mt-4 rounded-lg border border-status-rejected/30 bg-status-rejected/10 px-3 py-2">
            <p className="text-2xs font-medium text-status-rejected">
              Rejected — this decision is final and cannot be revised
            </p>
            <p className="mt-1 whitespace-pre-wrap text-xs text-ink">{task.rejection_reason}</p>
          </div>
        )}
        {task.status === 'failed' && task.failure_reason && (
          <div className="mt-4 rounded-lg border border-status-failed/30 bg-status-failed/10 px-3 py-2">
            <p className="text-2xs font-medium text-status-failed">This task failed</p>
            <p className="mt-1 whitespace-pre-wrap text-xs text-ink">{task.failure_reason}</p>
          </div>
        )}
        {/* Admin-only annotations. The worker serializer omits `notes`
            entirely, so this is absent rather than merely hidden. */}
        {isAdmin && task.notes && (
          <div className="mt-4 rounded-lg border border-edge bg-raised px-3 py-2">
            <p className="text-2xs font-medium text-ink-mute">Internal notes</p>
            <p className="mt-1 whitespace-pre-wrap text-xs text-ink-dim">{task.notes}</p>
          </div>
        )}

        <div className="mt-4 border-t border-edge pt-3">
          <button
            onClick={() => setShowHistory((v) => !v)}
            aria-expanded={showHistory}
            className={`rounded text-2xs font-medium text-ink-dim hover:text-ink ${FOCUS}`}
          >
            {showHistory ? 'Hide history' : 'Show history'}
          </button>
          {showHistory && (
            <div className="mt-3">
              <TaskTimeline taskId={task.id} />
            </div>
          )}
        </div>
      </section>

      {/* A REJECTED TASK IS READ-ONLY, NOT REOPENED FOR EDITING. The
          decision is final -- states.py offers no transition out of
          REJECTED to anyone -- so mounting the live editable studio here
          would invite edits nothing can ever be done with. This shows the
          frozen document Eagle actually saw instead, which the live studio
          cannot: `job`/`polished_text` keep changing after submission, so
          only the stored snapshot can still answer "what did the reviewer
          see". */}
      {task.status === 'rejected' ? (
        <RejectedSubmission task={task} />
      ) : task.job && task.kind === 'translation' ? (
        /* A TRANSLATION IS A DIFFERENT JOB. ReviewWorkspace renders
           job.segments, which here are the ENGLISH source -- while the
           submitter renders the French. Sending a translation task there
           let a worker edit one document and submit another. */
        <TranslationWorkspace task={task} onTaskChange={onChange} />
      ) : task.job ? (
        <ReviewWorkspace task={task} onTaskChange={onChange} />
      ) : isAdmin ? (
        /* The studio is the right tool when an operator has to source the
           media by hand. A TASKER MUST NEVER LAND HERE: it asks them to
           paste a URL they should not have, to run a pipeline the warm pool
           runs for them. */
        <Card title="Transcription studio" padded={false}>
          <div className="p-4">
            <Dashboard embedded />
          </div>
        </Card>
      ) : (
        <NotReadyYet task={task} />
      )}
    </div>
  );
}

/**
 * What a tasker sees when the machine has not finished with a task yet.
 *
 * THE AUDIO IS STILL OFFERED. Downloading the media is the fast part;
 * transcribing it is the slow part. So a task can very often be listened to
 * before its transcript exists -- and listening is exactly how someone
 * decides whether to take it. Nothing here asks the worker to do the
 * machine's job.
 */
function NotReadyYet({ task }) {
  const [failed, setFailed] = useState(false);

  return (
    <Card title="Being prepared" padded>
      <p className="text-xs text-ink-dim">
        This task has not finished pre-processing. The transcript appears
        here automatically when it is ready — there is nothing to do in the
        meantime, and you do not need to stay on this page.
      </p>

      {!failed && (
        <div className="mt-4">
          <p className="mb-2 text-2xs font-medium text-ink-mute">
            Listen before you decide
          </p>
          <audio
            controls
            preload="none"
            src={taskMedia.url(task.id)}
            onError={() => setFailed(true)}
            className="w-full"
          >
            Your browser cannot play audio.
          </audio>
          <p className="mt-1.5 text-2xs text-ink-mute">
            Accent, recording quality and crosstalk are what decide whether a
            task fits in the hour. None of that shows in the length.
          </p>
        </div>
      )}
      {failed && (
        <p className="mt-4 text-2xs text-ink-mute">
          The audio has not been downloaded yet. It appears here shortly.
        </p>
      )}
    </Card>
  );
}

/**
 * The frozen document Eagle actually received, for a rejected task.
 *
 * READ-ONLY, ON PURPOSE. The task cannot be reopened -- there is no button
 * that would do anything here -- so this exists purely to let a worker (or
 * an admin) read and copy exactly what was submitted, next to the reason
 * it was rejected, without the live studio inviting an edit that goes
 * nowhere.
 */
function RejectedSubmission({ task }) {
  const [copied, setCopied] = useState(false);
  const content = task.submitted_content || '';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card title="What was submitted" padded>
      {content ? (
        <>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-2xs text-ink-mute">
              Exactly what was sent to the platform — nothing has changed
              since.
            </p>
            <button
              onClick={copy}
              className={`shrink-0 rounded text-2xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-lg
                           border border-edge bg-raised p-3 font-mono text-xs text-ink-dim">
            {content}
          </pre>
        </>
      ) : (
        <p className="text-xs text-ink-dim">
          No stored copy of the submitted content for this task — it was
          rejected before this was recorded.
        </p>
      )}
    </Card>
  );
}

/**
 * The task's media URL, ready to paste into the studio below.
 *
 * A COPY BUTTON RATHER THAN AUTOMATION. Pre-filling the studio would mean
 * reaching into its state, and the studio is deliberately untouched. Copying
 * is one click and keeps the seam between the two honest -- the worker
 * remains the one who decides what gets transcribed.
 */
function SourceUrl({ url }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be refused (insecure context, permissions).
      // The URL is on screen and selectable, so there is nothing to
      // apologise for -- just do not claim it was copied.
      setCopied(false);
    }
  };

  return (
    <div className="mt-4 flex items-center gap-2 rounded-lg border border-edge bg-raised px-3 py-2">
      <span className="shrink-0 text-2xs text-ink-mute">Source</span>
      <code className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-dim" title={url}>
        {url}
      </code>
      <button
        onClick={copy}
        className={`shrink-0 rounded text-2xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}
