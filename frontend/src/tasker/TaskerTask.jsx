import React, { useCallback, useEffect, useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { tasker, taskerWorkspaceApi } from '../api/tasker.js';
import { useAsync } from '../hooks/useAsync.js';
import { ErrorState, Skeleton, Spinner } from '../components/ui/index.jsx';
import ReviewWorkspace from '../worker/ReviewWorkspace.jsx';
import TranslationWorkspace from '../worker/TranslationWorkspace.jsx';
import { WorkspaceApiProvider } from '../worker/workspaceApi.js';
import { taskerPath } from './brand.js';
import { TranscriptionGuide, TranslationGuide } from './TaskerGuide.jsx';

/** How often a task that is still being transcribed is checked again. */
const POLL_MS = 15000;

function minutes(seconds) {
  if (!seconds) return null;
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

/**
 * One task, for an Eagle tasker.
 *
 * THE SAME EDITING SCREENS YOUR WORKERS USE, pointed at the tasker's
 * private copy through WorkspaceApiProvider. Claiming, deadlines and
 * Submit are not here: the tasker claimed on Eagle and pastes the result
 * back into Eagle themselves.
 */
export default function TaskerTask() {
  const { taskId } = useParams();
  const load = useCallback(() => tasker.get(taskId), [taskId]);
  const { data: task, error, loading, refresh } = useAsync(load, [taskId]);
  // The tasker's own guides plug into the same "Full guide" button the
  // workers' screens have (workspaceApi dialogs), with tasker content.
  const workspaceApi = useMemo(() => ({
    ...taskerWorkspaceApi(taskId),
    dialogs: { reviewGuide: TranscriptionGuide, translationGuide: TranslationGuide },
  }), [taskId]);

  // NOT READY YET: check again every few seconds until the transcript is.
  const waiting = task && ['not_started', 'transcribing'].includes(task.readiness);
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(() => refresh({ silent: true }), POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, refresh]);

  if (loading && !task) {
    return <div className="space-y-3"><Skeleton className="h-20 rounded-xl2" /><Skeleton className="h-64 rounded-xl2" /></div>;
  }
  if (error && !task) {
    return (
      <div className="mx-auto max-w-xl space-y-4 pt-6">
        <ErrorState message={error.status === 404
          ? 'This task is not available any more. Tasks stay open for 3 hours after you first open them.'
          : error.message} />
        <Link to={taskerPath.search} className="text-xs font-medium text-accent hover:underline">
          ← Open another task
        </Link>
      </div>
    );
  }

  const leftAt = task.left_pool_at ? new Date(task.left_pool_at) : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <Link to={taskerPath.search} className="text-2xs font-medium text-accent hover:underline">
            ← Open another task
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-tight text-ink">
            {task.title || `Task ${task.external_task_id}`}
          </h1>
          <p className="mt-0.5 text-xs text-ink-mute">
            <span className="font-mono">#{task.external_task_id}</span>
            {' · '}{task.kind === 'translation' ? 'Translation' : 'Transcription'}
            {minutes(task.duration_seconds) && ` · ${minutes(task.duration_seconds)}`}
          </p>
        </div>
      </div>

      {/* A WARNING, NOT A LOCK. This system cannot tell whether the person
          looking is the one who claimed it. */}
      {leftAt && (
        <p role="status"
           className="rounded-xl2 border border-status-review/40 bg-status-review/10 px-4 py-3 text-xs text-ink">
          <span className="font-semibold">Already claimed</span>{' '}
          at {leftAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.
          If that was you, carry on. If it wasn’t, this task isn’t yours to submit.
        </p>
      )}

      {waiting && (
        <div className="flex items-start gap-3 rounded-xl2 border border-edge bg-surface px-4 py-4">
          <Spinner className="mt-0.5 h-4 w-4 text-accent" />
          <div className="text-xs text-ink-dim">
            <p className="font-medium text-ink">Transcribing this task now</p>
            <p className="mt-1">
              It wasn’t prepared before you opened it. This page checks again every few
              seconds and opens the transcript as soon as it’s ready — usually a few
              minutes for each 10 minutes of audio.
            </p>
          </div>
        </div>
      )}

      {task.readiness === 'failed' && (
        <ErrorState message="This task’s audio could not be transcribed here. You’ll need to work it directly on the platform." />
      )}

      {task.readiness === 'english_ready' && (
        <p className="rounded-xl2 border border-edge bg-surface px-4 py-3 text-xs text-ink-dim">
          <span className="font-medium text-ink">The English is ready; the French isn’t yet.</span>{' '}
          Copy the prompt and the English into your own assistant and paste the French
          back below — or come back in a few minutes.
        </p>
      )}

      {['ready', 'english_ready'].includes(task.readiness) && (
        <WorkspaceApiProvider value={workspaceApi}>
          {task.kind === 'translation'
            ? <TranslationWorkspace task={task} />
            : <ReviewWorkspace task={task} />}
        </WorkspaceApiProvider>
      )}
    </div>
  );
}
