import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import AdminOverview from '../admin/AdminOverview.jsx';
import AdminTasks from '../admin/AdminTasks.jsx';
import { stats as statsApi, tasks as tasksApi, workers as workersApi } from '../api/workforce.js';
import { work } from '../api/eagle.js';

/**
 * A tasker's own screen says "Awaiting review" for a task they have sent
 * and Eagle has not yet decided (status `submitted`). The admin screens
 * used to call that "Submitted" in the filter and show no such number
 * anywhere else, so the person running the operation could not see what
 * the people doing the work could.
 */

function task(overrides = {}) {
  return {
    id: 't-1', external_task_id: '6881', title: 'task-6881-source.mp3',
    kind: 'translation', status: 'submitted', priority: 0,
    duration_seconds: 300, updated_at: '2026-09-24T10:00:00Z',
    claimed_by_code: 'W-01', available_transitions: [],
    ...overrides,
  };
}

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('admin Tasks filter', () => {
  it('offers "Awaiting review" and filters to exactly those tasks', async () => {
    vi.spyOn(tasksApi, 'list').mockResolvedValue([
      task({ id: 'a', external_task_id: '6881', status: 'submitted' }),
      task({ id: 'b', external_task_id: '6900', status: 'approved' }),
    ]);
    vi.spyOn(workersApi, 'list').mockResolvedValue([]);
    wrap(<AdminTasks />);
    await screen.findByText(/2 of 2 tasks/);

    const select = screen.getByLabelText('Status');
    expect(within(select).getByRole('option', { name: 'Awaiting review' })).toBeInTheDocument();
    expect(within(select).queryByRole('option', { name: 'Submitted' })).toBeNull();

    await userEvent.selectOptions(select, 'submitted');

    expect(screen.getByText(/1 of 2 tasks/)).toBeInTheDocument();
  });
});

describe('admin Overview', () => {
  it('shows the total awaiting review and each worker\'s own count', async () => {
    vi.spyOn(statsApi, 'overview').mockResolvedValue({
      totals: {
        tasks: 40, unassigned: 3, workers: 2, active_workers: 2,
        by_status: { new: 3, submitted: 5, approved: 30, rejected: 2 },
      },
      workers: [{
        worker_code: 'W-01', display_name: 'nasra', held: 0,
        submitted: 10, awaiting_review: 4, last_activity_at: null,
      }],
      recent_events: [],
    });
    vi.spyOn(work, 'status').mockResolvedValue({ platform: [] });
    wrap(<AdminOverview />);

    const hint = await screen.findByText('sent, no verdict yet');
    const stat = hint.parentElement;
    expect(stat).toHaveTextContent('Awaiting review');
    expect(stat).toHaveTextContent('5');

    const row = screen.getByText('nasra').closest('tr');
    expect(row).toHaveTextContent('4');
  });
});
