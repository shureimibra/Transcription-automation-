import React, { useCallback, useState } from 'react';
import { stats as statsApi, workers as workersApi } from '../api/workforce.js';
import { useAsync } from '../hooks/useAsync.js';
import {
  Button, Card, EmptyState, ErrorState, Field, inputClass, LiveRegion, Skeleton, Stat,
} from '../components/ui/index.jsx';
import { daysAgo, elapsed, isoDay, percent, relative } from '../lib/format.js';

/**
 * Worker performance over a date range, plus account management.
 *
 * A RATE OF NOTHING IS NOT ZERO. Where a worker has had no task decided in
 * the window, the approval column shows an em dash. Rendering 0% would read
 * as "everything they sent was rejected" -- a claim about a real person's
 * competence, made by a division the data does not support.
 *
 * TIMINGS ARE MEDIANS. One task left open over a weekend would otherwise
 * dominate a week of ordinary work and make the report useless.
 */

const PRESETS = [
  { id: '7', label: 'Last 7 days', from: () => daysAgo(6) },
  { id: '30', label: 'Last 30 days', from: () => daysAgo(29) },
  { id: '90', label: 'Last 90 days', from: () => daysAgo(89) },
];

export default function AdminWorkers() {
  const [range, setRange] = useState({ from: daysAgo(29), to: isoDay(new Date()) });
  const [draft, setDraft] = useState(range);
  const [showCreate, setShowCreate] = useState(false);
  const [actionError, setActionError] = useState('');
  const [busyWorker, setBusyWorker] = useState(null);
  const [notice, setNotice] = useState('');

  const loadPerformance = useCallback(() => statsApi.performance(range), [range.from, range.to]);
  const performance = useAsync(loadPerformance, [range.from, range.to]);

  const loadWorkers = useCallback(() => workersApi.list(), []);
  const roster = useAsync(loadWorkers, []);

  const applyRange = (event) => {
    event.preventDefault();
    setRange(draft);
  };

  const applyPreset = (preset) => {
    const next = { from: preset.from(), to: isoDay(new Date()) };
    setDraft(next);
    setRange(next);
  };

  const toggleWorker = async (worker) => {
    setBusyWorker(worker.id);
    setActionError('');
    try {
      const wasActive = worker.status === 'active';
      const result = wasActive
        ? await workersApi.deactivate(worker.id)
        : await workersApi.activate(worker.id);
      // Deactivation releases whatever they were holding; say so, because
      // the operator did not explicitly ask for that part.
      const released = result?.released_tasks;
      setNotice(
        wasActive
          ? `${worker.worker_code} suspended${released ? `, ${released} task${released === 1 ? '' : 's'} released` : ''}.`
          : `${worker.worker_code} reactivated.`
      );
      roster.refresh({ silent: true });
      performance.refresh({ silent: true });
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusyWorker(null);
    }
  };

  const rows = performance.data?.workers || [];
  const totals = performance.data?.totals;
  const rejections = performance.data?.recent_rejections || [];
  const passRatePct = totals?.pass_rate_target != null ? Math.round(totals.pass_rate_target * 100) : null;
  const belowPassRateTarget = (
    totals?.approval_rate != null && passRatePct != null
    && Math.round(totals.approval_rate * 100) < passRatePct
  );
  const belowDailyTarget = (
    totals?.avg_submitted_per_day != null && totals?.daily_target != null
    && totals.avg_submitted_per_day < totals.daily_target
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Workers</h1>
          <p className="mt-0.5 text-xs text-ink-mute">
            Throughput and outcomes
            {performance.data?.range && ` from ${performance.data.range.from} to ${performance.data.range.to}`}.
          </p>
        </div>
        <Button size="sm" variant="primary" onClick={() => setShowCreate(true)}>Add worker</Button>
      </header>

      <form onSubmit={applyRange} className="flex flex-wrap items-end gap-3">
        <div className="flex gap-1">
          {PRESETS.map((preset) => (
            <Button key={preset.id} type="button" size="sm" onClick={() => applyPreset(preset)}>
              {preset.label}
            </Button>
          ))}
        </div>
        <div className="w-40">
          <Field label="From" htmlFor="range-from">
            <input
              id="range-from" type="date" value={draft.from} max={draft.to}
              onChange={(e) => setDraft((d) => ({ ...d, from: e.target.value }))}
              className={inputClass}
            />
          </Field>
        </div>
        <div className="w-40">
          <Field label="To" htmlFor="range-to">
            <input
              id="range-to" type="date" value={draft.to} min={draft.from} max={isoDay(new Date())}
              onChange={(e) => setDraft((d) => ({ ...d, to: e.target.value }))}
              className={inputClass}
            />
          </Field>
        </div>
        <Button type="submit" size="md" variant="secondary" loading={performance.loading}>Apply</Button>
      </form>

      {performance.error && (
        <ErrorState message={performance.error.message} onRetry={() => performance.refresh()} />
      )}
      {actionError && <ErrorState message={actionError} onRetry={() => setActionError('')} />}
      <LiveRegion message={notice} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Submitted / day"
          value={totals ? totals.avg_submitted_per_day : null}
          hint={totals?.daily_target ? `target ${totals.daily_target}/day` : undefined}
          tone={belowDailyTarget ? 'warn' : 'default'}
        />
        <Stat label="Approved" value={totals?.approved ?? null} tone="good" />
        <Stat label="Rejected" value={totals?.rejected ?? null} tone={totals?.rejected ? 'bad' : 'default'} />
        <Stat
          label="Approval rate"
          value={totals ? percent(totals.approval_rate) : null}
          hint={
            totals?.decided
              ? `${totals.decided} decided · target ${passRatePct}%+`
              : 'nothing decided yet'
          }
          tone={belowPassRateTarget ? 'warn' : 'default'}
        />
      </div>

      <Card title="Performance" subtitle="Per worker, within the selected range">
        {performance.loading && !performance.data ? (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : rows.length === 0 ? (
          <EmptyState title="No workers" message="Add a worker to see performance here." />
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[52rem] text-left">
              <caption className="sr-only">
                Worker performance between {performance.data?.range?.from} and {performance.data?.range?.to}
              </caption>
              <thead>
                <tr className="border-b border-edge text-2xs uppercase tracking-wide text-ink-mute">
                  <th scope="col" className="px-4 py-2 font-medium">Worker</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">Submitted</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">Approved</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">Rejected</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">Approval</th>
                  <th scope="col" className="px-2 py-2 text-right font-medium" title="Median time from claiming a task to submitting it">
                    Median turnaround
                  </th>
                  <th scope="col" className="px-2 py-2 text-right font-medium" title="Median handling time divided by audio length">
                    × audio
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Holding</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.worker_code} className="border-b border-edge/60 last:border-0">
                    <th scope="row" className="px-4 py-2 text-left font-normal">
                      <div className="text-xs text-ink">{row.display_name}</div>
                      <div className="text-2xs text-ink-mute">
                        {row.worker_code}
                        {row.status !== 'active' && (
                          <span className="ml-1.5 text-status-cancelled">· {row.status}</span>
                        )}
                      </div>
                    </th>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-ink">{row.submitted}</td>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-status-approved">{row.approved}</td>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-status-rejected">{row.rejected}</td>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-ink">
                      {percent(row.approval_rate) ?? <span className="text-ink-mute">—</span>}
                    </td>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-ink-dim">
                      {row.median_handling_seconds === null ? '—' : elapsed(row.median_handling_seconds)}
                    </td>
                    <td className="px-2 py-2 text-right text-xs tabular-nums text-ink-dim">
                      {row.median_realtime_factor === null ? '—' : `${row.median_realtime_factor}×`}
                    </td>
                    <td className="px-4 py-2 text-right text-xs tabular-nums text-ink-dim">{row.held_now}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* WHERE A DEFECT PATTERN ACTUALLY SHOWS UP. A pass-rate number says
          how the team is doing; only the reasons, read together across
          every worker, say whether the same mistake keeps recurring (a
          prompt problem) or outcomes are scattered (something else). A
          rejection is permanent and unappealable, so this is real data,
          never a sample. */}
      <Card
        title="Recent rejections"
        subtitle={rejections.length ? 'Look for the same defect recurring' : undefined}
      >
        {rejections.length === 0 ? (
          <EmptyState
            title="No rejections in this range"
            message="Nothing the platform rejected shows up here."
          />
        ) : (
          <ul className="divide-y divide-edge/60">
            {rejections.map((task) => (
              <li key={task.id} className="py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2 text-2xs text-ink-mute">
                  <span>
                    {task.external_task_id ? `Task ${task.external_task_id}` : task.id.slice(0, 8)}
                    {task.kind ? ` · ${task.kind}` : ''}
                    {task.worker_code ? ` · ${task.worker_code}` : ''}
                  </span>
                  <span>{task.rejected_at ? relative(task.rejected_at) : ''}</span>
                </div>
                {task.rejection_reason && (
                  <p className="mt-1 whitespace-pre-wrap rounded-lg border border-status-rejected/30
                                bg-status-rejected/10 px-3 py-2 text-2xs leading-relaxed text-ink">
                    {task.rejection_reason}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Accounts" subtitle="Suspending a worker releases the tasks they hold">
        {roster.error && <ErrorState message={roster.error.message} onRetry={() => roster.refresh()} compact />}
        {roster.loading && !roster.data ? (
          <div className="space-y-2">{[0, 1].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : (
          <ul className="divide-y divide-edge/60">
            {(roster.data || []).map((worker) => (
              <li key={worker.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-ink">
                    {worker.display_name || worker.username}
                    {worker.is_admin && (
                      <span className="ml-2 rounded bg-overlay px-1.5 py-0.5 text-2xs text-ink-dim">admin</span>
                    )}
                  </div>
                  <div className="text-2xs text-ink-mute">
                    {worker.worker_code} · {worker.username} · up to {worker.max_concurrent_tasks} at once
                    {worker.last_activity_at && ` · last seen ${relative(worker.last_activity_at)}`}
                  </div>
                </div>
                {/* `active` and `status` can disagree: a profile marked
                    active whose Django account has been disabled cannot
                    work either. Say which it is rather than showing the
                    profile status and leaving the operator to wonder why
                    an "active" worker cannot sign in. */}
                <span className={`text-2xs ${worker.active ? 'text-status-approved' : 'text-status-cancelled'}`}>
                  {worker.active
                    ? 'Active'
                    : worker.user_is_active === false
                      ? 'Account disabled'
                      : worker.status}
                </span>
                <Button
                  size="sm"
                  variant={worker.status === 'active' ? 'secondary' : 'primary'}
                  loading={busyWorker === worker.id}
                  onClick={() => toggleWorker(worker)}
                >
                  {worker.status === 'active' ? 'Suspend' : 'Reactivate'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {showCreate && (
        <CreateWorkerDialog
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); roster.refresh(); performance.refresh({ silent: true }); }}
        />
      )}
    </div>
  );
}

/**
 * New worker account.
 *
 * The password minimum mirrors the backend's validators rather than being
 * enforced only there: a rejection after typing a whole form is a worse
 * experience than a hint before submitting. The server still validates --
 * this is a courtesy, not the check.
 */
function CreateWorkerDialog({ onClose, onCreated }) {
  const [form, setForm] = useState({
    username: '', password: '', email: '', worker_code: '',
    display_name: '', max_concurrent_tasks: 3,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});

  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setFieldErrors({});

    const errors = {};
    if (!form.username.trim()) errors.username = 'Required.';
    if (!form.worker_code.trim()) errors.worker_code = 'Required.';
    if (form.password.length < 12) errors.password = 'At least 12 characters.';
    if (Object.keys(errors).length) { setFieldErrors(errors); return; }

    setBusy(true);
    try {
      await workersApi.create({
        ...form,
        max_concurrent_tasks: Number(form.max_concurrent_tasks) || 3,
      });
      onCreated();
    } catch (err) {
      // DRF field errors come back as {field: [messages]}. Show them beside
      // the field they belong to rather than as one wall of text.
      const payload = err.payload;
      if (payload && typeof payload === 'object' && !payload.error) {
        const mapped = {};
        Object.entries(payload).forEach(([key, value]) => {
          mapped[key] = Array.isArray(value) ? value.join(' ') : String(value);
        });
        setFieldErrors(mapped);
        setError('Please correct the highlighted fields.');
      } else {
        setError(err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-worker-title"
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl2 border border-edge bg-surface p-5 shadow-panel"
      >
        <h2 id="create-worker-title" className="text-sm font-semibold text-ink">Add a worker</h2>
        <p className="mt-1 text-xs text-ink-mute">
          Creates a sign-in account and a worker profile together.
        </p>

        <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
          {error && <ErrorState message={error} compact />}

          <Field label="Username" htmlFor="w-username" error={fieldErrors.username}>
            <input id="w-username" autoFocus value={form.username} onChange={set('username')}
                   autoComplete="off" className={inputClass} />
          </Field>
          <Field label="Worker code" htmlFor="w-code" error={fieldErrors.worker_code}
                 hint="Short identifier shown on task cards, e.g. W-04.">
            <input id="w-code" value={form.worker_code} onChange={set('worker_code')} className={inputClass} />
          </Field>
          <Field label="Display name" htmlFor="w-name" error={fieldErrors.display_name}>
            <input id="w-name" value={form.display_name} onChange={set('display_name')} className={inputClass} />
          </Field>
          <Field label="Email" htmlFor="w-email" error={fieldErrors.email}>
            <input id="w-email" type="email" value={form.email} onChange={set('email')} className={inputClass} />
          </Field>
          <Field label="Temporary password" htmlFor="w-password" error={fieldErrors.password}
                 hint="At least 12 characters.">
            <input id="w-password" type="password" value={form.password} onChange={set('password')}
                   autoComplete="new-password" className={inputClass} />
          </Field>
          <Field label="Concurrent task limit" htmlFor="w-max" error={fieldErrors.max_concurrent_tasks}>
            <input id="w-max" type="number" min={1} max={20} value={form.max_concurrent_tasks}
                   onChange={set('max_concurrent_tasks')} className={inputClass} />
          </Field>

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" variant="primary" loading={busy}>Create worker</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
