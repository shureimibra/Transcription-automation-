import React, { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { tasks as tasksApi, workers as workersApi } from '../api/workforce.js';
import { useAsync, usePolling } from '../hooks/useAsync.js';
import { useTaskStream } from '../hooks/useTaskStream.js';
import StatusBadge, { STATUS_ORDER, statusMeta } from '../components/StatusBadge.jsx';
import TaskActions from '../components/TaskActions.jsx';
import {
  Button, Card, EmptyState, ErrorState, Field, FOCUS, inputClass, LiveRegion, Skeleton,
} from '../components/ui/index.jsx';
import { duration, relative, taskLabel, taskRoute } from '../lib/format.js';

/**
 * Task management: find a task, see who has it, move it.
 *
 * ASSIGNMENT IS NOT CLAIMING. Assigning earmarks a task for a worker; the
 * worker still claims it, which is what takes the lock and starts their
 * clock. An admin cannot put a task into someone's hands without them
 * picking it up -- that distinction is in the state machine, and this screen
 * respects it rather than papering over it.
 *
 * REASSIGNING A HELD TASK IS TWO ACTS. The holder releases (or the admin
 * releases on their behalf) and then it is assigned onward. There is no
 * single "reassign" that silently takes work out of someone's hands without
 * an audit event for each half.
 */
export default function AdminTasks() {
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(() => tasksApi.list({}), []);
  const { data, error, loading, refresh } = useAsync(load, []);
  usePolling(refresh, 20000);
  useTaskStream(useCallback(() => refresh({ silent: true }), [refresh]));

  const roster = useAsync(useCallback(() => workersApi.list(), []), []);

  const onChange = useCallback(() => refresh({ silent: true }), [refresh]);

  const list = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data || []).filter((task) => {
      if (statusFilter && task.status !== statusFilter) return false;
      if (kindFilter && task.kind !== kindFilter) return false;
      if (!needle) return true;
      return [
        taskLabel(task), task.external_task_id, task.source_url,
        task.claimed_by_code, task.assigned_to_code,
        task.source_language, task.target_language,
      ].some((field) => String(field || '').toLowerCase().includes(needle));
    });
  }, [data, query, statusFilter, kindFilter]);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Tasks</h1>
          <p className="mt-0.5 text-xs text-ink-mute">
            {loading && !data ? 'Loading…' : `${list.length} of ${(data || []).length} tasks`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => refresh()} loading={loading}>Refresh</Button>
          <Button size="sm" variant="primary" onClick={() => setShowCreate(true)}>New task</Button>
        </div>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <Field label="Search" htmlFor="admin-search">
            <input
              id="admin-search" type="search" value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Title, task id, URL, worker code"
              className={inputClass}
            />
          </Field>
        </div>
        <div className="w-40">
          <Field label="Status" htmlFor="admin-status">
            <select id="admin-status" value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)} className={inputClass}>
              <option value="">Any status</option>
              {STATUS_ORDER.map((s) => (
                <option key={s} value={s}>{statusMeta(s).label}</option>
              ))}
            </select>
          </Field>
        </div>
        <div className="w-40">
          <Field label="Type" htmlFor="admin-kind">
            <select id="admin-kind" value={kindFilter}
                    onChange={(e) => setKindFilter(e.target.value)} className={inputClass}>
              <option value="">Any type</option>
              <option value="transcription">Transcription</option>
              <option value="translation">Translation</option>
            </select>
          </Field>
        </div>
      </div>

      {error && <ErrorState message={error.message} onRetry={() => refresh()} />}
      <LiveRegion message={notice} />

      {loading && !data ? (
        <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : list.length === 0 ? (
        <Card>
          <EmptyState
            title={query || statusFilter || kindFilter ? 'Nothing matches those filters' : 'No tasks yet'}
            action={
              (query || statusFilter || kindFilter) && (
                <Button size="sm" onClick={() => { setQuery(''); setStatusFilter(''); setKindFilter(''); }}>
                  Clear filters
                </Button>
              )
            }
          />
        </Card>
      ) : (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[60rem] text-left">
              <thead>
                <tr className="border-b border-edge text-2xs uppercase tracking-wide text-ink-mute">
                  <th scope="col" className="px-4 py-2 font-medium">Task</th>
                  <th scope="col" className="px-2 py-2 font-medium">Status</th>
                  <th scope="col" className="px-2 py-2 font-medium">Worker</th>
                  <th scope="col" className="px-2 py-2 font-medium">Updated</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {list.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    workers={roster.data || []}
                    onChange={onChange}
                    onNotice={setNotice}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {showCreate && (
        <CreateTaskDialog
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); refresh(); setNotice('Task created.'); }}
        />
      )}
    </div>
  );
}

function TaskRow({ task, workers, onChange, onNotice }) {
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState('');

  // Assignment only makes sense before someone holds it. Once claimed, the
  // route back to the pool is a release, which is its own audited act.
  const canAssign = task.status === 'new' || task.status === 'assigned';

  const assign = async (workerId) => {
    if (!workerId) return;
    setAssigning(true);
    setError('');
    try {
      await tasksApi.assign(task.id, workerId);
      onNotice?.(`${taskLabel(task)} assigned.`);
      onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setAssigning(false);
    }
  };

  const unassign = async () => {
    setAssigning(true);
    setError('');
    try {
      await tasksApi.unassign(task.id);
      onNotice?.(`${taskLabel(task)} returned to the open queue.`);
      onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setAssigning(false);
    }
  };

  return (
    <tr className="border-b border-edge/60 align-top last:border-0">
      <td className="px-4 py-3">
        <Link to={`/tasks/${task.id}`} className={`rounded text-xs font-medium text-ink hover:text-accent ${FOCUS}`}>
          {taskLabel(task)}
        </Link>
        <div className="mt-0.5 text-2xs text-ink-mute">
          <span className="capitalize">{task.kind}</span> · {taskRoute(task)}
          {task.duration_seconds ? ` · ${duration(task.duration_seconds)}` : ''}
        </div>
        {error && <p className="mt-1 text-2xs text-status-rejected">{error}</p>}
      </td>
      <td className="px-2 py-3"><StatusBadge status={task.status} size="sm" /></td>
      <td className="px-2 py-3">
        {task.claimed_by_code ? (
          <div className="text-2xs text-ink-dim">
            <span className="text-ink">{task.claimed_by_code}</span>
            <div className="text-ink-mute">holding</div>
          </div>
        ) : canAssign ? (
          <div className="flex items-center gap-1">
            <label className="sr-only" htmlFor={`assign-${task.id}`}>Assign task to a worker</label>
            <select
              id={`assign-${task.id}`}
              value={task.assigned_to || ''}
              disabled={assigning}
              onChange={(e) => (e.target.value ? assign(e.target.value) : unassign())}
              className="w-32 rounded-lg border border-edge bg-canvas px-2 py-1 text-2xs text-ink"
            >
              <option value="">Unassigned</option>
              {workers.map((w) => (
                <option key={w.id} value={w.id}>{w.worker_code}</option>
              ))}
            </select>
          </div>
        ) : (
          <span className="text-2xs text-ink-mute">—</span>
        )}
      </td>
      <td className="px-2 py-3 text-2xs text-ink-mute">{relative(task.updated_at)}</td>
      <td className="px-4 py-3">
        <div className="flex justify-end">
          <TaskActions
            task={task}
            onChange={onChange}
            size="sm"
            // A worker's own progression (start / review / ready) is theirs
            // to drive. An admin's business with someone else's task is the
            // outcome and the escape hatches.
            only={['approved', 'rejected', 'cancelled', 'new']}
          />
        </div>
      </td>
    </tr>
  );
}

function CreateTaskDialog({ onClose, onCreated }) {
  const [form, setForm] = useState({
    title: '', kind: 'transcription', source_url: '', external_task_id: '',
    external_source: '', source_language: '', target_language: '',
    duration_seconds: '', priority: 1, notes: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});

  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setFieldErrors({});
    if (form.kind === 'translation' && !form.target_language.trim()) {
      setFieldErrors({ target_language: 'A translation task needs a target language.' });
      return;
    }
    setBusy(true);
    try {
      await tasksApi.create({
        ...form,
        duration_seconds: form.duration_seconds ? Number(form.duration_seconds) : null,
        priority: Number(form.priority) || 1,
      });
      onCreated();
    } catch (err) {
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
        role="dialog" aria-modal="true" aria-labelledby="create-task-title"
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl2 border border-edge bg-surface p-5 shadow-panel"
      >
        <h2 id="create-task-title" className="text-sm font-semibold text-ink">New task</h2>
        <p className="mt-1 text-xs text-ink-mute">
          Tasks are created unassigned and open for anyone to claim.
        </p>

        <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
          {error && <ErrorState message={error} compact />}

          <Field label="Title" htmlFor="t-title" error={fieldErrors.title}>
            <input id="t-title" autoFocus value={form.title} onChange={set('title')} className={inputClass} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Type" htmlFor="t-kind" error={fieldErrors.kind}>
              <select id="t-kind" value={form.kind} onChange={set('kind')} className={inputClass}>
                <option value="transcription">Transcription</option>
                <option value="translation">Translation</option>
              </select>
            </Field>
            <Field label="Priority" htmlFor="t-priority" error={fieldErrors.priority}>
              <select id="t-priority" value={form.priority} onChange={set('priority')} className={inputClass}>
                <option value={1}>Normal</option>
                <option value={2}>High</option>
                <option value={3}>Urgent</option>
              </select>
            </Field>
          </div>

          <Field label="Source URL" htmlFor="t-url" error={fieldErrors.source_url}
                 hint="Where the media lives. Leave blank for an uploaded file.">
            <input id="t-url" type="url" value={form.source_url} onChange={set('source_url')} className={inputClass} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Source language" htmlFor="t-src" error={fieldErrors.source_language}
                   hint="Blank to auto-detect.">
              <input id="t-src" value={form.source_language} onChange={set('source_language')}
                     placeholder="en" className={inputClass} />
            </Field>
            <Field label="Target language" htmlFor="t-tgt" error={fieldErrors.target_language}>
              <input id="t-tgt" value={form.target_language} onChange={set('target_language')}
                     placeholder="fr" disabled={form.kind !== 'translation'} className={inputClass} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="External task id" htmlFor="t-ext" error={fieldErrors.external_task_id}>
              <input id="t-ext" value={form.external_task_id} onChange={set('external_task_id')} className={inputClass} />
            </Field>
            <Field label="Audio length (seconds)" htmlFor="t-dur" error={fieldErrors.duration_seconds}>
              <input id="t-dur" type="number" min={0} value={form.duration_seconds}
                     onChange={set('duration_seconds')} className={inputClass} />
            </Field>
          </div>

          <Field label="Internal notes" htmlFor="t-notes" error={fieldErrors.notes}
                 hint="Visible to administrators only.">
            <textarea id="t-notes" rows={2} value={form.notes} onChange={set('notes')}
                      className={`${inputClass} resize-y`} />
          </Field>

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" variant="primary" loading={busy}>Create task</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
