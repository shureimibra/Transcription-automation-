import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { tasker } from '../api/tasker.js';
import { Button, inputClass } from '../components/ui/index.jsx';
import { taskerPath } from './brand.js';

/**
 * The tasker's home: one box for one task ID.
 *
 * EXACT ID ONLY, AND NOTHING TO BROWSE. An outside account should reach a
 * task only when it already knows the ID -- in practice, one it already
 * claimed on Eagle. The server enforces this; the page simply offers
 * nothing else.
 */
export default function TaskerSearch() {
  const navigate = useNavigate();
  const [reference, setReference] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const open = async (event) => {
    event.preventDefault();
    if (!reference.trim()) {
      setError('Paste the task link, or its ID.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const task = await tasker.lookup(reference.trim());
      navigate(taskerPath.task(task.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl pt-6 sm:pt-12">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-accent">Open a task</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight text-ink">
        Which task are you working on?
      </h1>
      <p className="mt-3 text-sm text-ink-dim">
        Claim it first, then copy the link from your browser’s address bar
        and paste it here.
      </p>

      <form onSubmit={open} className="mt-6 flex flex-col gap-2 sm:flex-row">
        <label htmlFor="task-ref" className="sr-only">Task link or ID</label>
        <input
          id="task-ref" autoFocus inputMode="text" autoComplete="off"
          placeholder="Paste the full task link (or just its ID)"
          value={reference} onChange={(e) => setReference(e.target.value)}
          className={`${inputClass} flex-1 font-mono`}
        />
        <Button type="submit" variant="primary" size="lg" loading={busy}>Open task</Button>
      </form>
      {error && (
        <p role="alert"
           className="mt-3 rounded-lg border border-status-review/40 bg-status-review/10 px-3 py-2 text-xs text-ink">
          {error}
        </p>
      )}

      <dl className="mt-10 grid gap-4 border-t border-edge pt-6 text-xs sm:grid-cols-3">
        <div>
          <dt className="font-medium text-ink">Your own copy</dt>
          <dd className="mt-1 text-ink-mute">Your edits are private. Nobody else’s changes reach your draft.</dd>
        </div>
        <div>
          <dt className="font-medium text-ink">70 minutes</dt>
          <dd className="mt-1 text-ink-mute">A task can still be opened for 70 minutes after it has been claimed.</dd>
        </div>
        <div>
          <dt className="font-medium text-ink">3 hours</dt>
          <dd className="mt-1 text-ink-mute">Once opened, it stays open to you for 3 hours.</dd>
        </div>
      </dl>
    </div>
  );
}
