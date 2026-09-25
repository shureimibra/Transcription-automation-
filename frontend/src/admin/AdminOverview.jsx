import React, { useCallback } from 'react';
import { Link } from 'react-router-dom';
import { stats as statsApi } from '../api/workforce.js';
import { work } from '../api/eagle.js';
import { useAsync, usePolling } from '../hooks/useAsync.js';
import { useTaskStream } from '../hooks/useTaskStream.js';
import { STATUS_ORDER, statusMeta } from '../components/StatusBadge.jsx';
import {
  Button, Card, EmptyState, ErrorState, FOCUS, Skeleton, Stat,
} from '../components/ui/index.jsx';
import { relative } from '../lib/format.js';

const PRODUCT_LABEL = { transcription: 'Transcription', translation: 'Translation' };

/**
 * The Eagle side of the operation, which has no other home in the UI.
 *
 * Both numbers here are ones an operator otherwise learns only after
 * something has already stalled: a session that has expired stops the
 * poller, and a minutes balance at zero makes every claim fail while the
 * queue still shows work as ready. Shown plainly so neither is a surprise.
 */
function PlatformCard({ platform }) {
  if (!platform || platform.length === 0) return null;
  return (
    <Card title="Platform" subtitle="Eagle session and minutes">
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full min-w-[28rem] text-left">
          <thead>
            <tr className="border-b border-edge text-2xs uppercase tracking-wide text-ink-mute">
              <th scope="col" className="px-4 py-2 font-medium">Product</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Minutes</th>
              <th scope="col" className="px-2 py-2 font-medium">Session</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Checked</th>
            </tr>
          </thead>
          <tbody>
            {platform.map((p) => {
              const known = p.minutes_balance !== null && p.minutes_balance !== undefined;
              const empty = known && p.minutes_balance <= 0;
              const expired = p.status === 'expired';
              return (
                <tr key={p.product} className="border-b border-edge/60 last:border-0">
                  <td className="px-4 py-2 text-xs text-ink">
                    {PRODUCT_LABEL[p.product] || p.product}
                  </td>
                  <td
                    className={`px-2 py-2 text-right text-xs tabular-nums ${
                      empty ? 'font-semibold text-status-failed' : 'text-ink'
                    }`}
                  >
                    {known ? p.minutes_balance : '—'}
                  </td>
                  <td className="px-2 py-2 text-2xs">
                    <span className={expired ? 'font-medium text-status-failed' : 'text-ink-dim'}>
                      {p.status || 'unknown'}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right text-2xs text-ink-mute">
                    {p.last_verified_at ? relative(p.last_verified_at) : 'never'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/**
 * The operator's first screen: what is happening right now.
 *
 * NOTHING HERE IS A RATE. Percentages belong on the performance report,
 * which is date-scoped; a lifetime approval rate on a live dashboard tells
 * an operator nothing they can act on today. This screen answers "where is
 * the work sitting and who is holding it".
 */
export default function AdminOverview() {
  const load = useCallback(() => statsApi.overview(), []);
  const { data, error, loading, refresh } = useAsync(load, []);
  usePolling(refresh, 15000);
  useTaskStream(useCallback(() => refresh({ silent: true }), [refresh]));

  // Its own call, not folded into the overview stats: the platform block is
  // admin-only and small, and a failure to read it must not blank the whole
  // dashboard. A missing/failed result simply hides the card.
  const loadPlatform = useCallback(() => work.status(), []);
  const { data: status, refresh: refreshPlatform } = useAsync(loadPlatform, []);
  usePolling(refreshPlatform, 60000);

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-xl2" />)}
        </div>
        <Skeleton className="h-56 rounded-xl2" />
      </div>
    );
  }

  const totals = data?.totals;
  const byStatus = totals?.by_status || {};
  const workers = data?.workers || [];
  const events = data?.recent_events || [];
  const maxCount = Math.max(1, ...Object.values(byStatus));

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Overview</h1>
          <p className="mt-0.5 text-xs text-ink-mute">Live state of the queue and the team.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/admin/workers" className={`rounded text-xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}>
            Performance report
          </Link>
          <Button size="sm" onClick={() => refresh()} loading={loading}>Refresh</Button>
        </div>
      </header>

      {error && <ErrorState message={error.message} onRetry={() => refresh()} />}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Tasks" value={totals?.tasks ?? null} />
        <Stat
          label="Waiting to be claimed"
          value={totals?.unassigned ?? null}
          tone={totals?.unassigned > 10 ? 'warn' : 'default'}
        />
        <Stat label="Active workers" value={totals?.active_workers ?? null} hint={`of ${totals?.workers ?? '—'}`} />
        <Stat
          label="In review or ready"
          value={
            totals
              ? (byStatus.review || 0) + (byStatus.ready_to_submit || 0)
              : null
          }
        />
        <Stat
          label="Awaiting review"
          value={totals ? (byStatus.submitted || 0) : null}
          hint="sent, no verdict yet"
        />
      </div>

      <PlatformCard platform={status?.platform} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Where the work is" subtitle="Tasks by status">
          {Object.keys(byStatus).length === 0 ? (
            <EmptyState title="No tasks yet" message="Create a task to get started." />
          ) : (
            <ul className="space-y-2">
              {STATUS_ORDER.filter((s) => byStatus[s]).map((s) => {
                const meta = statusMeta(s);
                const count = byStatus[s];
                return (
                  <li key={s} className="flex items-center gap-3">
                    <span className="w-28 shrink-0 text-2xs text-ink-dim">{meta.label}</span>
                    {/* The bar is proportion at a glance; the number beside
                        it is the fact. Neither depends on the other. */}
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-overlay">
                      <span
                        className={`block h-full rounded-full ${meta.dot}`}
                        style={{ width: `${Math.round((count / maxCount) * 100)}%` }}
                      />
                    </span>
                    <span className="w-8 shrink-0 text-right text-xs tabular-nums text-ink">{count}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title="Team" subtitle="Current load per worker">
          {workers.length === 0 ? (
            <EmptyState title="No workers yet" message="Add a worker to start assigning tasks." />
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left">
                <thead>
                  <tr className="border-b border-edge text-2xs uppercase tracking-wide text-ink-mute">
                    <th scope="col" className="px-4 py-2 font-medium">Worker</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">Holding</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">Submitted</th>
                    <th scope="col" className="px-2 py-2 text-right font-medium">Awaiting review</th>
                    <th scope="col" className="px-4 py-2 text-right font-medium">Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {workers.map((w) => (
                    <tr key={w.worker_code} className="border-b border-edge/60 last:border-0">
                      <td className="px-4 py-2">
                        <div className="text-xs text-ink">{w.display_name}</div>
                        <div className="text-2xs text-ink-mute">{w.worker_code}</div>
                      </td>
                      <td className="px-2 py-2 text-right text-xs tabular-nums text-ink">{w.held}</td>
                      <td className="px-2 py-2 text-right text-xs tabular-nums text-ink-dim">{w.submitted}</td>
                      <td className="px-2 py-2 text-right text-xs tabular-nums text-ink">{w.awaiting_review ?? 0}</td>
                      <td className="px-4 py-2 text-right text-2xs text-ink-mute">
                        {w.last_activity_at ? relative(w.last_activity_at) : 'never'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Card title="Recent activity" subtitle="The last events across every task">
        {events.length === 0 ? (
          <EmptyState title="Nothing has happened yet" />
        ) : (
          <ul className="divide-y divide-edge/60">
            {events.slice(0, 20).map((event) => (
              <li key={event.id} className="flex flex-wrap items-baseline gap-x-2 py-2 text-2xs">
                <Link
                  to={`/tasks/${event.task}`}
                  className={`rounded font-medium text-ink hover:text-accent ${FOCUS}`}
                >
                  {String(event.event_type).replace(/_/g, ' ').toLowerCase()}
                </Link>
                {event.success === false && (
                  <span className="font-medium text-status-failed">failed</span>
                )}
                <span className="text-ink-mute">
                  {event.actor_username ? `by ${event.actor_username}` : 'by the system'}
                </span>
                <span className="ml-auto text-ink-mute">{relative(event.occurred_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
