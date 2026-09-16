import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import TaskCard from '../components/TaskCard.jsx';
import { tasks as tasksApi } from '../api/workforce.js';

// THE BUG THIS FILE EXISTS FOR, CONFIRMED LIVE: a worker clicked Claim,
// then immediately clicked the same card's title -- before the claim
// request had actually resolved. The title link and the Claim button knew
// nothing about each other, so the navigation went through mid-flight and
// landed on the task's own page while it was still genuinely unclaimed on
// the server, showing a live Claim button there too and inviting a second
// click on what was really the same task.

function makeTask(overrides = {}) {
  return {
    id: 'task-1', external_task_id: '38527', title: 'C 213.mp3',
    kind: 'transcription', status: 'new', is_ready: true, priority: 0,
    duration_seconds: 600, updated_at: '2026-09-16T10:00:00Z',
    available_transitions: ['claimed'],
    ...overrides,
  };
}

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('TaskCard: the title link cannot outrun its own pending action', () => {
  it('disables navigation via the title while Claim is in flight, and restores it once settled', async () => {
    let resolveClaim;
    vi.spyOn(tasksApi, 'claim').mockReturnValue(
      new Promise((resolve) => { resolveClaim = resolve; })
    );

    wrap(<TaskCard task={makeTask()} />);
    const titleLink = screen.getByRole('link', { name: '38527 · C 213.mp3' });
    expect(titleLink).not.toHaveAttribute('aria-disabled');

    await userEvent.click(screen.getByRole('button', { name: 'Claim' }));
    expect(titleLink).toHaveAttribute('aria-disabled', 'true');

    // A click during the busy window must not navigate -- this is the
    // exact race that let the second, mistaken claim happen. Nothing to
    // navigate TO in this test (no <Routes>), so the real assertion is
    // simply that the click does not throw and the link stays disabled.
    await userEvent.click(titleLink);
    expect(titleLink).toHaveAttribute('aria-disabled', 'true');

    resolveClaim(makeTask({
      status: 'claimed', claimed_by_code: 'W-01', available_transitions: ['in_progress'],
    }));

    await waitFor(() => expect(titleLink).not.toHaveAttribute('aria-disabled'));
  });
});
