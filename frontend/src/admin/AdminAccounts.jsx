import React, { useCallback, useMemo, useState } from 'react';
import { accounts as accountsApi } from '../api/workforce.js';
import { useAsync } from '../hooks/useAsync.js';
import { useAuth } from '../auth/AuthContext.jsx';
import {
  Button, Card, EmptyState, ErrorState, Field, inputClass, LiveRegion, Skeleton,
} from '../components/ui/index.jsx';
import { relative } from '../lib/format.js';

/**
 * Accounts and what kind each one is.
 *
 * ROLE IS THE ACCOUNT'S KIND; STATUS IS WHETHER A TASKER MAY WORK RIGHT NOW.
 * They are shown in separate columns because conflating them is what made a
 * tool-only user impossible before: suspending a tasker must not turn them
 * into something else, and a suspended tasker is still a tasker.
 *
 * The role names come from the server, so a role added later appears here
 * without a change to this file — only the descriptions below are local, and
 * an unknown role falls back to its raw value rather than to a wrong label.
 */

const ROLE_HELP = {
  admin:  'Full control: every task, every worker, the analytics, and account management.',
  tasker: 'Works the queue: claims tasks, edits transcripts, submits. Measured on throughput.',
  tool:   'The transcription studio only. No queue, no assignments, no other worker’s data.',
  eagle_tasker: 'An outside Eagle worker. Looks up a task by its exact Eagle ID and edits a private copy. No queue, no studio, no submitting.',
};

const ROLE_ORDER = ['admin', 'tasker', 'tool', 'eagle_tasker'];

/** `eagle_tasker` reads as "eagle tasker", not as an identifier. */
const roleName = (role) => String(role).replace(/_/g, ' ');

export default function AdminAccounts() {
  const { user } = useAuth();
  const [showCreate, setShowCreate] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [promoting, setPromoting] = useState(null);

  const load = useCallback(() => accountsApi.list(), []);
  //: Which account is having its password reset, if any.
  const [resetting, setResetting] = useState(null);
  const { data, error: loadError, loading, refresh } = useAsync(load, []);

  const rows = useMemo(() => {
    const list = data || [];
    const rank = (r) => {
      const i = ROLE_ORDER.indexOf(r.role);
      return i === -1 ? ROLE_ORDER.length : i;
    };
    return [...list].sort((a, b) => rank(a) - rank(b) || a.username.localeCompare(b.username));
  }, [data]);

  const toggleActive = async (account) => {
    setBusyId(account.id);
    setError('');
    try {
      await accountsApi.setActive(account.id, !account.user_is_active);
      setNotice(account.user_is_active
        ? `${account.username} can no longer sign in, and has been signed out.`
        : `${account.username} can sign in again.`);
      refresh({ silent: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const changeRole = async (account, role, extra = {}) => {
    setBusyId(account.id);
    setError('');
    try {
      await accountsApi.setRole(account.id, role, extra);
      setNotice(`${account.username} is now ${roleName(role)}.`);
      setPromoting(null);
      refresh({ silent: true });
    } catch (err) {
      // A 409 here is the server refusing to strand held work. Show its
      // sentence: it names the number of tasks and what to do about it.
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const onRoleSelect = (account, role) => {
    if (role === account.role) return;
    // Becoming a tasker needs a worker code, and only when they have never
    // been one — the server keeps an existing profile on the way back.
    if (role === 'tasker' && !account.worker_code) {
      setPromoting({ account, role, code: '' });
      return;
    }
    changeRole(account, role);
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Accounts</h1>
          <p className="mt-0.5 text-xs text-ink-mute">
            Who can sign in, and what kind of account each one is.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => refresh()} loading={loading}>Refresh</Button>
          <Button size="sm" variant="primary" onClick={() => setShowCreate(true)}>Add account</Button>
        </div>
      </header>

      {loadError && <ErrorState message={loadError.message} onRetry={() => refresh()} />}
      {error && <ErrorState message={error} onRetry={() => setError('')} />}
      <LiveRegion message={notice} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ROLE_ORDER.map((role) => (
          <div key={role} className="rounded-xl2 border border-edge bg-surface px-4 py-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs font-medium capitalize text-ink">{roleName(role)}</span>
              <span className="text-sm tabular-nums text-ink-dim">
                {rows.filter((r) => r.role === role).length}
              </span>
            </div>
            <p className="mt-1 text-2xs leading-relaxed text-ink-mute">{ROLE_HELP[role]}</p>
          </div>
        ))}
      </div>

      <Card padded={false}>
        {loading && !data ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4"><EmptyState title="No accounts" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-left">
              <thead>
                <tr className="border-b border-edge text-2xs uppercase tracking-wide text-ink-mute">
                  <th scope="col" className="px-4 py-2 font-medium">User</th>
                  <th scope="col" className="px-2 py-2 font-medium">Role</th>
                  <th scope="col" className="px-2 py-2 font-medium">Worker</th>
                  <th scope="col" className="px-2 py-2 font-medium">Can reach</th>
                  <th scope="col" className="px-4 py-2 font-medium">Created</th>
                  <th scope="col" className="px-2 py-2 font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((account) => {
                  const isSelf = account.username === user?.username;
                  return (
                    <tr key={account.id} className="border-b border-edge/60 align-top last:border-0">
                      <td className="px-4 py-3">
                        <div className="text-xs text-ink">
                          {account.username}
                          {isSelf && <span className="ml-2 text-2xs text-ink-mute">(you)</span>}
                        </div>
                        {account.email && (
                          <div className="text-2xs text-ink-mute">{account.email}</div>
                        )}
                        {!account.user_is_active && (
                          <div className="text-2xs text-status-cancelled">account disabled</div>
                        )}
                      </td>
                      <td className="px-2 py-3">
                        <label className="sr-only" htmlFor={`role-${account.id}`}>
                          Role for {account.username}
                        </label>
                        <select
                          id={`role-${account.id}`}
                          value={account.role}
                          // An administrator may not demote themselves. The
                          // server refuses it too; disabling the control just
                          // avoids offering a dead end.
                          disabled={isSelf || busyId === account.id}
                          onChange={(e) => onRoleSelect(account, e.target.value)}
                          className="w-32 rounded-lg border border-edge bg-canvas px-2 py-1 text-2xs text-ink disabled:opacity-50"
                        >
                          {ROLE_ORDER.map((r) => (
                            <option key={r} value={r}>{roleName(r)}</option>
                          ))}
                          {!ROLE_ORDER.includes(account.role) && (
                            <option value={account.role}>{account.role}</option>
                          )}
                        </select>
                      </td>
                      <td className="px-2 py-3 text-2xs text-ink-dim">
                        {account.worker_code
                          ? <>
                              {account.worker_code}
                              <div className="text-ink-mute">{account.worker_status}</div>
                            </>
                          : <span className="text-ink-mute">—</span>}
                      </td>
                      <td className="px-2 py-3">
                        <div className="flex flex-wrap gap-1">
                          {(account.capabilities || []).map((c) => (
                            <span key={c} className="rounded bg-overlay px-1.5 py-0.5 text-2xs text-ink-dim">
                              {c}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-2xs text-ink-mute">
                        {relative(account.created_at)}
                      </td>
                      <td className="px-2 py-3">
                        {/* THE COMMONEST ADMINISTRATIVE REQUEST THERE IS.
                            It used to need SSH and a management command,
                            which is a lot of ceremony for "I forgot my
                            password" -- and unreachable from a phone, which
                            is where this is usually read. */}
                        <Button size="sm" variant="ghost"
                                disabled={busyId === account.id}
                                onClick={() => setResetting(account)}>
                          Reset password
                        </Button>
                        {/* Takes access away without granting anything else --
                            changing the role would (tool grants the studio). */}
                        {!isSelf && (
                          <Button size="sm" variant="ghost"
                                  disabled={busyId === account.id}
                                  onClick={() => toggleActive(account)}>
                            {account.user_is_active ? 'Disable sign-in' : 'Enable sign-in'}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {resetting && (
        <PasswordDialog
          account={resetting}
          onCancel={() => setResetting(null)}
          onDone={(username) => {
            setResetting(null);
            setNotice(`Password changed for ${username}. Tell them the new one — `
                      + `nothing is emailed, and their old sessions are now closed.`);
          }}
        />
      )}

      {promoting && (
        <WorkerCodeDialog
          username={promoting.account.username}
          busy={busyId === promoting.account.id}
          onCancel={() => setPromoting(null)}
          onConfirm={(code, displayName) =>
            changeRole(promoting.account, 'tasker', {
              worker_code: code, display_name: displayName,
            })}
        />
      )}

      {showCreate && (
        <CreateAccountDialog
          onClose={() => setShowCreate(false)}
          onCreated={(username, role) => {
            setShowCreate(false);
            setNotice(`Created ${username} as ${roleName(role)}.`);
            refresh();
          }}
        />
      )}
    </div>
  );
}

/** Asked for only when someone becomes a tasker and has no worker code yet. */
/**
 * Set a new password for somebody who forgot theirs.
 *
 * THE ADMIN TYPES IT AND READS IT OUT. There is no outbound mail on this
 * deployment, so a "send a reset link" flow would be a button that appears
 * to work and silently does nothing. Choosing a temporary password and
 * passing it on by whatever channel the team already uses is honest about
 * what the system can actually do.
 *
 * SHOWN, NOT MASKED. The admin is typing a password they must then read
 * aloud or paste into a message; masking it guarantees typos they cannot
 * see and cannot pass on correctly. Nobody is shoulder-surfing an operator
 * resetting an account on their own machine, and the value is about to be
 * spoken out loud anyway.
 */
function PasswordDialog({ account, onCancel, onDone }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const tooShort = password.length > 0 && password.length < 12;

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await accountsApi.setPassword(account.id, password);
      onDone(account.username);
    } catch (err) {
      setError(err?.message || 'Could not change the password.');
      setBusy(false);
    }
  };

  return (
    <Modal titleId="reset-password-title" onCancel={onCancel}>
      <h2 id="reset-password-title" className="text-sm font-semibold text-ink">
        New password for {account.username}
      </h2>
      <p className="mt-1 text-2xs text-ink-mute">
        You choose it and tell them yourself — nothing is emailed. Their
        current sessions are closed, so anyone signed in as them is signed out.
      </p>
      <form onSubmit={submit} className="mt-4 space-y-3">
        <Field label="New password" htmlFor="reset-password"
               error={tooShort ? 'At least 12 characters.' : ''}>
          <input
            id="reset-password"
            type="text"
            autoFocus
            autoComplete="off"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </Field>
        {error && (
          <p role="alert" className="text-2xs text-status-rejected">{error}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={busy}
                  disabled={password.length < 12}>
            Set password
          </Button>
        </div>
      </form>
    </Modal>
  );
}


function WorkerCodeDialog({ username, busy, onCancel, onConfirm }) {
  const [code, setCode] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [touched, setTouched] = useState(false);
  const missing = !code.trim();

  return (
    <Modal titleId="promote-title" onCancel={onCancel}>
      <h2 id="promote-title" className="text-sm font-semibold text-ink">
        Make {username} a tasker
      </h2>
      <p className="mt-1 text-xs text-ink-mute">
        A tasker needs a worker code — it identifies them on task cards and in the
        performance report.
      </p>
      <form
        className="mt-4 space-y-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (!missing) onConfirm(code.trim(), displayName.trim());
        }}
      >
        <Field label="Worker code" htmlFor="promote-code"
               error={touched && missing ? 'This is required.' : null}>
          <input id="promote-code" autoFocus value={code}
                 onChange={(e) => setCode(e.target.value)}
                 placeholder="W-07" className={inputClass} />
        </Field>
        <Field label="Display name" htmlFor="promote-name">
          <input id="promote-name" value={displayName}
                 onChange={(e) => setDisplayName(e.target.value)} className={inputClass} />
        </Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="primary" loading={busy}>Make tasker</Button>
        </div>
      </form>
    </Modal>
  );
}

function CreateAccountDialog({ onClose, onCreated }) {
  const [form, setForm] = useState({
    username: '', password: '', email: '', role: 'tool',
    worker_code: '', display_name: '', max_concurrent_tasks: 3,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const isTasker = form.role === 'tasker';

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setFieldErrors({});

    const errors = {};
    if (!form.username.trim()) errors.username = 'Required.';
    if (form.password.length < 12) errors.password = 'At least 12 characters.';
    if (isTasker && !form.worker_code.trim()) errors.worker_code = 'A tasker needs a worker code.';
    if (Object.keys(errors).length) { setFieldErrors(errors); return; }

    setBusy(true);
    try {
      const payload = {
        username: form.username.trim(),
        password: form.password,
        email: form.email.trim(),
        role: form.role,
      };
      if (isTasker) {
        payload.worker_code = form.worker_code.trim();
        payload.display_name = form.display_name.trim();
        payload.max_concurrent_tasks = Number(form.max_concurrent_tasks) || 3;
      }
      await accountsApi.create(payload);
      onCreated(payload.username, payload.role);
    } catch (err) {
      const payload = err.payload;
      if (payload && typeof payload === 'object' && !payload.error) {
        const mapped = {};
        Object.entries(payload).forEach(([k, v]) => {
          mapped[k] = Array.isArray(v) ? v.join(' ') : String(v);
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
    <Modal titleId="create-account-title" onCancel={onClose}>
      <h2 id="create-account-title" className="text-sm font-semibold text-ink">Add an account</h2>
      <p className="mt-1 text-xs text-ink-mute">
        The role decides what they see when they sign in.
      </p>

      <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
        {error && <ErrorState message={error} compact />}

        <Field label="Role" htmlFor="a-role" hint={ROLE_HELP[form.role]}>
          <select id="a-role" value={form.role} onChange={set('role')} className={inputClass}>
            {ROLE_ORDER.map((r) => <option key={r} value={r}>{roleName(r)}</option>)}
          </select>
        </Field>

        <Field label="Username" htmlFor="a-username" error={fieldErrors.username}>
          <input id="a-username" autoFocus value={form.username} onChange={set('username')}
                 autoComplete="off" className={inputClass} />
        </Field>
        <Field label="Email" htmlFor="a-email" error={fieldErrors.email}>
          <input id="a-email" type="email" value={form.email} onChange={set('email')}
                 className={inputClass} />
        </Field>
        <Field label="Temporary password" htmlFor="a-password" error={fieldErrors.password}
               hint="At least 12 characters.">
          <input id="a-password" type="password" value={form.password} onChange={set('password')}
                 autoComplete="new-password" className={inputClass} />
        </Field>

        {isTasker && (
          <>
            <Field label="Worker code" htmlFor="a-code" error={fieldErrors.worker_code}>
              <input id="a-code" value={form.worker_code} onChange={set('worker_code')}
                     placeholder="W-07" className={inputClass} />
            </Field>
            <Field label="Display name" htmlFor="a-name" error={fieldErrors.display_name}>
              <input id="a-name" value={form.display_name} onChange={set('display_name')}
                     className={inputClass} />
            </Field>
            <Field label="Concurrent task limit" htmlFor="a-max"
                   error={fieldErrors.max_concurrent_tasks}>
              <input id="a-max" type="number" min={1} max={20}
                     value={form.max_concurrent_tasks} onChange={set('max_concurrent_tasks')}
                     className={inputClass} />
            </Field>
          </>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="primary" loading={busy}>Create account</Button>
        </div>
      </form>
    </Modal>
  );
}

function Modal({ titleId, onCancel, children }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}
    >
      <div
        role="dialog" aria-modal="true" aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl2 border border-edge bg-surface p-5 shadow-panel"
      >
        {children}
      </div>
    </div>
  );
}
