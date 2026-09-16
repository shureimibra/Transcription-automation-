import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../auth/AuthContext.jsx';
import WorkerDashboard from '../worker/WorkerDashboard.jsx';
import { auth as authApi, tasks as tasksApi } from '../api/workforce.js';

/**
 * THE BUG THIS FILE EXISTS FOR: a worker claimed two tasks in one motion.
 * The Claim button itself was never unresponsive -- it disables and shows a
 * spinner the instant it is clicked, see TaskActions.test.jsx -- but "Up
 * next" only shows four cards, and a successful claim removes one from the
 * list on a SILENT refetch. The next task slides up to fill the gap, in the
 * exact screen position the worker's eye was still on, looking like the
 * first click never registered. They clicked it too.
 *
 * The fix is a confirmation anchored to the page rather than to a card the
 * list is about to remove -- so it survives the reshuffle regardless of
 * where any given card ends up.
 */

function me(overrides = {}) {
  return {
    username: 'ana', role: 'tasker', is_admin: false,
    capabilities: ['workforce'],
    profile: { worker_code: 'W-01', display_name: 'Ana', max_concurrent_tasks: 3 },
    ...overrides,
  };
}

function task(overrides = {}) {
  return {
    id: 't-1', external_task_id: '38527', title: 'C 213.mp3',
    kind: 'transcription', status: 'new', is_ready: true, priority: 0,
    duration_seconds: 600, updated_at: '2026-09-16T10:00:00Z',
    available_transitions: ['claimed'],
    ...overrides,
  };
}

const wrap = (ui) => render(<MemoryRouter><AuthProvider>{ui}</AuthProvider></MemoryRouter>);

describe('WorkerDashboard: claim confirmation survives the queue reshuffle', () => {
  it('shows a page-anchored confirmation naming the task that was just claimed', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(me());
    const claimed = task({ status: 'claimed', available_transitions: ['in_progress'] });
    vi.spyOn(tasksApi, 'claim').mockResolvedValue(claimed);
    vi.spyOn(tasksApi, 'list').mockImplementation(({ available } = {}) => {
      if (available === 'true') return Promise.resolve([task()]);
      return Promise.resolve([]);
    });

    wrap(<WorkerDashboard />);

    const claimButton = await screen.findByRole('button', { name: 'Claim' });
    await userEvent.click(claimButton);

    // Names the actual task, not a generic "done" -- a worker mid-reshuffle
    // needs to know WHICH one just moved, not just that something did.
    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent(/claimed 38527.*C 213\.mp3/i);
  });

  it('does not show a claimed confirmation for other actions, such as starting work', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(me());
    const started = task({
      status: 'claimed', available_transitions: ['in_progress'],
    });
    const nowInProgress = task({
      status: 'in_progress', available_transitions: ['review'],
    });
    vi.spyOn(tasksApi, 'start').mockResolvedValue(nowInProgress);
    vi.spyOn(tasksApi, 'list').mockImplementation(({ mine } = {}) => {
      if (mine === 'true') return Promise.resolve([started]);
      return Promise.resolve([]);
    });

    wrap(<WorkerDashboard />);

    const startButton = await screen.findByRole('button', { name: 'Start work' });
    await userEvent.click(startButton);

    await waitFor(() => expect(tasksApi.start).toHaveBeenCalled());
    expect(screen.queryByText(/^Claimed /)).not.toBeInTheDocument();
  });
});
