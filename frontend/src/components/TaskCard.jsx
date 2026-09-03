import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import StatusBadge from './StatusBadge.jsx';
import TaskActions from './TaskActions.jsx';
import { duration, relative, taskLabel, taskRoute } from '../lib/format.js';
import { taskMedia } from '../api/eagle.js';
import { Chip, FOCUS } from './ui/index.jsx';
import { AlertIcon, BoltIcon, ClockIcon, EyeIcon, LanguagesIcon, MicIcon } from './icons.jsx';

/**
 * One task, as a card.
 *
 * THE WHOLE CARD IS NOT A LINK. It contains buttons, and nesting
 * interactive controls inside a link produces a card where clicking
 * "Release" also navigates. The title is the link; the card is a container.
 *
 * THE SHAPE IS THE ONE OUR TASKERS ALREADY READ -- a tinted strip naming the
 * kind of work, the identifier, a row of facts as chips, then a divider with
 * a quiet action on the left and the committing action on the right. The
 * CONTENTS are ours: no earnings figure, because payment is held on the
 * platform and inventing a number here would be a lie a tasker plans around.
 *
 * PRIORITY IS A WORD, NOT ONLY A COLOUR. A red border alone is invisible to
 * a viewer who cannot distinguish it, and unexplained to everyone else.
 */

const PRIORITY = {
  3: { label: 'Urgent', tone: 'danger' },
  2: { label: 'High', tone: 'warn' },
};

const KIND = {
  transcription: { label: 'Transcription', icon: <MicIcon className="h-4 w-4" /> },
  translation:   { label: 'Translation', icon: <LanguagesIcon className="h-4 w-4" /> },
};

export default function TaskCard({ task, onChange, showWorker = false, actions = true }) {
  const priority = PRIORITY[task.priority];
  const kind = KIND[task.kind] || { label: task.kind || 'Task', icon: <MicIcon className="h-4 w-4" /> };
  const owned = Boolean(task.claimed_by_code);

  return (
    <article
      className="flex flex-col overflow-hidden rounded-xl2 border border-edge bg-surface
                 shadow-panel transition-shadow hover:shadow-lift"
    >
      {/* The strip names the kind of work before anything else, because a
          tasker filters on it faster than they read a title. */}
      <div className="flex items-center justify-between gap-3 border-b border-edge bg-accent-soft px-4 py-2.5">
        <span className="flex items-center gap-2 text-xs font-semibold text-accent-ink">
          {kind.icon}
          {kind.label}
        </span>
        <StatusBadge status={task.status} size="sm" />
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="min-w-0">
          <Link
            to={`/tasks/${task.id}`}
            className={`block truncate text-base font-semibold text-ink hover:text-accent ${FOCUS} rounded`}
          >
            {taskLabel(task)}
          </Link>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone="outline">{taskRoute(task)}</Chip>
          {task.duration_seconds ? (
            <Chip tone="neutral" className="tabular-nums">{duration(task.duration_seconds)}</Chip>
          ) : null}

          {/* READY means the machine work is done: transcribed, and for a
              translation task translated too. It is the most useful fact on
              the card, so it gets the one saturated colour the card is
              allowed -- claiming a cold task starts the external one-hour
              clock on work that has not begun. */}
          {task.is_ready && (
            <Chip
              tone="ready"
              icon={<BoltIcon className="h-3.5 w-3.5" />}
              title="Transcript is already prepared — you can start straight away"
            >
              Ready now
            </Chip>
          )}

          {!owned && (
            <Chip
              tone="neutral"
              icon={<ClockIcon className="h-3.5 w-3.5" />}
              title="Claiming starts the platform's one-hour clock. It cannot be paused."
            >
              1h window
            </Chip>
          )}

          {priority && <Chip tone={priority.tone}>{priority.label}</Chip>}
        </div>

        {task.status === 'rejected' && task.rejection_reason && (
          <Band tone="rejected" label="Returned">{task.rejection_reason}</Band>
        )}
        {task.status === 'failed' && task.failure_reason && (
          <Band tone="failed" label="Failed">{task.failure_reason}</Band>
        )}

        {/* LISTEN BEFORE COMMITTING. Claiming starts Eagle's one-hour clock
            and cannot be undone, so a decision made from a filename and a
            duration is a decision made blind -- accent, recording quality
            and crosstalk are what actually decide whether a task fits the
            hour. Only offered for unclaimed work; once it is yours the
            workspace has the real player. */}
        {!owned && <Preview task={task} />}

        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-edge pt-3">
          <p className="text-2xs text-ink-mute">
            {showWorker && task.claimed_by_code ? `${task.claimed_by_code} · ` : ''}
            Updated {relative(task.updated_at)}
          </p>
          {actions && <TaskActions task={task} onChange={onChange} size="sm" />}
        </div>
      </div>
    </article>
  );
}

function Band({ tone, label, children }) {
  const tones = {
    rejected: 'border-status-rejected/30 bg-status-rejected/10 text-status-rejected',
    failed: 'border-status-failed/30 bg-status-failed/10 text-status-failed',
  };
  return (
    <p className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-2xs ${tones[tone]}`}>
      <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
      <span className="text-ink"><span className="font-semibold">{label}:</span> {children}</span>
    </p>
  );
}

function Preview({ task }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  // AUDIO IS FETCHED ON DEMAND, and a 20-minute file is ~30 MB, which
  // measured at about twenty seconds. An <audio> element gives no sign of
  // life during that, so without this the button looks broken and gets
  // clicked again -- which is exactly when someone decides the feature does
  // not work and stops using it.
  const [loading, setLoading] = useState(false);

  if (failed) {
    return (
      <p className="text-2xs text-ink-mute">
        This audio could not be fetched. Tell your administrator the task id.
      </p>
    );
  }

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); setLoading(true); }}
        className={`inline-flex w-fit items-center gap-1.5 rounded-lg px-2 py-1 -ml-2 text-2xs
          font-semibold text-accent transition-colors hover:bg-accent-soft ${FOCUS}`}
      >
        <EyeIcon className="h-3.5 w-3.5" />
        Listen first
      </button>
    );
  }

  return (
    <div>
      {loading && (
        <p className="mb-1.5 text-2xs text-ink-mute" role="status">
          Fetching the audio… this takes a few seconds the first time.
        </p>
      )}
      <audio
        controls
        autoPlay
        preload="auto"
        src={taskMedia.url(task.id)}
        onCanPlay={() => setLoading(false)}
        onError={() => { setLoading(false); setFailed(true); }}
        aria-label={`Preview audio for task ${task.external_task_id || ''}`}
        className="w-full"
      />
    </div>
  );
}
