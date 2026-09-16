import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { tasks as tasksApi } from '../api/workforce.js';
import { useAsync, usePolling } from '../hooks/useAsync.js';
import { useTaskStream } from '../hooks/useTaskStream.js';
import { useAuth } from '../auth/AuthContext.jsx';
import TaskCard from '../components/TaskCard.jsx';
import ReviewOutcomes from './ReviewOutcomes.jsx';
import { taskLabel } from '../lib/format.js';
import {
  Card, EmptyState, ErrorState, FOCUS, Notice, PageHero, Skeleton, Stat,
} from '../components/ui/index.jsx';
import { HomeIcon } from '../components/icons.jsx';

// A worker's home screen: what they are holding, what they could take, and
// what they recently finished.
//
// CURRENT WORK COMES FIRST, always. The queue is interesting; the task
// already claimed is the one with a deadline attached. Putting the queue on
// top would invite claiming a second task before finishing the first.

const HELD = ['claimed', 'in_progress', 'review', 'ready_to_submit', 'rejected'];
const RECENT = ['submitted', 'approved', 'rejected', 'cancelled', 'failed'];

export default function WorkerDashboard() {
  const { profile } = useAuth();

  const load = useCallback(async () => {
    // Three narrow queries rather than one broad one: each list is filtered
    // server-side, so a worker's browser never receives the whole task table.
    const [mine, available, recent] = await Promise.all([
      tasksApi.list({ mine: 'true', status: HELD.join(',') }),
      tasksApi.list({ available: 'true' }),
      tasksApi.list({ mine: 'true', status: RECENT.join(',') }),
    ]);
    return { mine, available, recent };
  }, []);

  const { data, error, loading, refresh } = useAsync(load, []);
  usePolling(refresh, 20000);
  // The socket makes this feel immediate; the poll above is what makes it
  // correct when the socket is not there.
  useTaskStream(useCallback(() => refresh({ silent: true }), [refresh]));

  // A CLAIMED CARD DISAPPEARS FROM "UP NEXT" AS SOON AS THE REFETCH LANDS --
  // that is correct (it now belongs under "Current work"), but it is also
  // exactly what a mis-fired click looks like. A worker claimed two tasks
  // in one motion this way: clicked Claim, the grid silently reshuffled
  // (the card gone, the next four sliding up to fill its spot), read that
  // as "nothing happened", and clicked what was now a DIFFERENT task's
  // Claim button sitting in the same screen position. The button itself
  // was never unresponsive -- see TaskActions' `loading` state -- the
  // reshuffle just outran the worker's eye. A confirmation anchored to the
  // page, not to a card the list is about to remove, survives the reshuffle.
  const [claimedNotice, setClaimedNotice] = useState('');
  const clearNoticeTimer = useRef(null);

  useEffect(() => () => clearTimeout(clearNoticeTimer.current), []);

  const replaceTask = useCallback((updated) => {
    // Only the claim transition ever lands a task ON `claimed` -- every
    // other action in TaskActions' ACTIONS map targets a different status,
    // so this is a reliable "a claim just succeeded" signal without
    // threading a second callback through TaskCard/TaskActions for it.
    if (updated?.status === 'claimed') {
      setClaimedNotice(`Claimed ${taskLabel(updated)} — it moved to "Current work" below.`);
      clearTimeout(clearNoticeTimer.current);
      clearNoticeTimer.current = setTimeout(() => setClaimedNotice(''), 8000);
    }
    // A task that changed state may belong in a different list now, so the
    // authoritative move is a refetch -- but do it silently, without
    // blanking the screen the worker is looking at.
    if (updated) refresh({ silent: true });
  }, [refresh]);

  const held = data?.mine || [];
  const available = data?.available || [];

  // READY means the machine has already done its part -- transcribed, and
  // for a translation task translated too. It is the only count a tasker can
  // act on right now: claiming a cold task starts Eagle's one-hour clock on
  // work that has not begun.
  const byKind = {
    transcription: available.filter((t) => t.kind === 'transcription').length,
    translation: available.filter((t) => t.kind === 'translation').length,
  };
  const ready = {
    transcription: available.filter((t) => t.kind === 'transcription' && t.is_ready).length,
    translation: available.filter((t) => t.kind === 'translation' && t.is_ready).length,
  };
  // Ready work first, so the four tasks shown are the four worth taking.
  const upNext = [...available].sort(
    (a, b) => (b.is_ready === true) - (a.is_ready === true)
  );
  // Rejected work is the only closed state a worker can still act on, so it
  // gets its own section with its buttons intact.
  const rejected = useMemo(
    () => (data?.recent || []).filter((t) => t.status === 'rejected'),
    [data?.recent]
  );

  const capacity = profile?.max_concurrent_tasks ?? null;
  const atCapacity = capacity !== null && held.length >= capacity;

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-xl2" />)}
        </div>
        <Skeleton className="h-40 rounded-xl2" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHero
        icon={<HomeIcon className="h-6 w-6" />}
        eyebrow="Your workspace"
        title={`${greeting()}${profile?.display_name ? `, ${profile.display_name.split(' ')[0]}` : ''}`}
        subtitle={
          held.length === 0
            ? 'Nothing in progress. Claim a task below to begin.'
            : `${held.length} task${held.length === 1 ? '' : 's'} in your hands.`
        }
        actions={
          <button
            type="button"
            onClick={() => refresh()}
            disabled={loading}
            className="rounded-xl2 bg-white/15 px-3 py-2 text-xs font-semibold text-white backdrop-blur
              transition-colors hover:bg-white/25 disabled:opacity-50
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      />

      {error && (
        <ErrorState
          message={error.message}
          onRetry={() => refresh()}
        />
      )}

      {/* Anchored above the grid it is confirming, on purpose -- this is the
          one thing on the page that does NOT move when "Up next" reshuffles
          after a claim, which is the whole point of it. */}
      {claimedNotice && (
        <div
          className="flex items-center gap-2 rounded-xl2 border border-status-ready/30
                     bg-status-ready/10 px-4 py-2.5"
        >
          <Notice message={claimedNotice} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="In your hands" value={held.length} />
        <Stat
          label="Capacity"
          value={capacity === null ? null : `${held.length} / ${capacity}`}
          tone={atCapacity ? 'warn' : 'default'}
          hint={atCapacity ? 'Finish one before claiming another' : undefined}
        />
        <Stat
          label="Transcription ready"
          value={ready.transcription}
          hint={`${byKind.transcription} available`}
        />
        <Stat
          label="Translation ready"
          value={ready.translation}
          hint={`${byKind.translation} available`}
        />
      </div>

      <Card
        title="Current work"
        subtitle="Tasks you have claimed"
      >
        {held.length === 0 ? (
          <EmptyState
            title="No active tasks"
            message="When you claim a task it appears here with everything you need to work on it."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {held.map((task) => (
              <TaskCard key={task.id} task={task} onChange={replaceTask} />
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Up next"
        subtitle={atCapacity ? 'You are at your task limit' : 'Ready work first'}
        actions={
          <div className="flex items-center gap-3">
            <Link
              to="/tasks/transcription"
              className={`rounded text-xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}
            >
              Transcription
            </Link>
            <Link
              to="/tasks/translation"
              className={`rounded text-xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}
            >
              Translation
            </Link>
          </div>
        }
      >
        {available.length === 0 ? (
          <EmptyState title="The queue is empty" message="New tasks appear here as they arrive." />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {upNext.slice(0, 4).map((task) => (
              <TaskCard key={task.id} task={task} onChange={replaceTask} />
            ))}
          </div>
        )}
      </Card>

      {/* WHAT THE PLATFORM DECIDED, not merely what was sent.
          This replaces a "Recently closed" list of cards that showed the
          same tasks without the one fact a worker actually needs: whether
          the work was accepted, and if not, why. */}
      <ReviewOutcomes tasks={data?.recent || []} loading={loading} />

      {/* Rejected work comes back for rework, so it keeps its actions. */}
      {rejected.length > 0 && (
        <Card title="Sent back to you" subtitle="Rejected work you can revise and resubmit">
          <div className="grid gap-3 lg:grid-cols-2">
            {rejected.map((task) => (
              <TaskCard key={task.id} task={task} onChange={replaceTask} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}
