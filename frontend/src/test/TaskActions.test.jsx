import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import TaskActions from '../components/TaskActions.jsx';
import { tasks as tasksApi } from '../api/workforce.js';

// These tests are about one claim: the UI offers exactly what the server
// says is possible, and nothing else. That is the property that keeps a
// worker from clicking a button that 403s.

function makeTask(overrides = {}) {
  return {
    id: 'task-1',
    status: 'new',
    kind: 'transcription',
    available_transitions: ['claimed'],
    deliverable_revision: null,
    // WARM BY DEFAULT, because that is the ordinary case in the queue: a
    // tasker claims something marked "Ready now" and it goes in one click.
    // Claiming a COLD task asks first, and has its own test below.
    is_ready: true,
    ...overrides,
  };
}

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('TaskActions', () => {
  it('renders only the transitions the server offered', () => {
    wrap(<TaskActions task={makeTask({ available_transitions: ['claimed'] })} />);

    expect(screen.getByRole('button', { name: 'Claim' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
  });

  it('renders nothing at all when the server offers nothing', () => {
    const { container } = wrap(
      <TaskActions task={makeTask({ status: 'approved', available_transitions: [] })} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('ignores transitions that have no human action, such as failed', () => {
    wrap(
      <TaskActions
        task={makeTask({ status: 'in_progress', available_transitions: ['failed', 'review'] })}
      />
    );
    expect(screen.getByRole('button', { name: 'Move to review' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /fail/i })).not.toBeInTheDocument();
  });

  it('calls the matching endpoint and reports the updated task', async () => {
    const updated = makeTask({ status: 'claimed', available_transitions: ['in_progress'] });
    const claim = vi.spyOn(tasksApi, 'claim').mockResolvedValue(updated);
    const onChange = vi.fn();

    wrap(<TaskActions task={makeTask()} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Claim' }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(updated));
    expect(claim).toHaveBeenCalledWith('task-1');
  });

  it('shows the server message and refetches when someone else claimed first', async () => {
    const conflict = Object.assign(new Error('Someone else is already working on this task.'), {
      isConflict: true,
    });
    vi.spyOn(tasksApi, 'claim').mockRejectedValue(conflict);
    const taken = makeTask({ status: 'claimed', available_transitions: [] });
    vi.spyOn(tasksApi, 'get').mockResolvedValue(taken);
    const onChange = vi.fn();

    wrap(<TaskActions task={makeTask()} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Claim' }));

    // The message is the server's own, not a rephrasing.
    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent('Someone else is already working on this task.');

    // AND IT IS NOT AN ALERT. Six taskers sorting by "Ready now" reach for
    // the same card, so losing a race is the normal shape of a busy
    // morning. Rendered as an alert it was a red panel with a warning
    // triangle and a "Try again" link -- five people seeing what looked
    // like a broken system at once, offered the one action that is
    // certainly wrong: retrying a claim on a task that is gone.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(notice).toHaveTextContent('take the next one');

    // And the task is refreshed to its real state rather than left stale.
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(taken));
  });

  it('asks before claiming a task that is not warm yet', async () => {
    // Claiming spends minutes and starts an hour that cannot be paused. On a
    // cold task that hour is spent waiting for the machine, and if the
    // transcript never arrives there is nothing to submit -- three tasks
    // were lost that way in a single morning.
    const claim = vi.spyOn(tasksApi, 'claim').mockResolvedValue(makeTask());

    wrap(<TaskActions task={makeTask({ is_ready: false })} onChange={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Claim' }));

    // Nothing has been spent yet.
    expect(claim).not.toHaveBeenCalled();
    expect(await screen.findByText(/This task is not ready yet/)).toBeInTheDocument();

    // And it is still possible on purpose -- supply is scarce enough that
    // taking a cold task is sometimes the right call.
    await userEvent.click(screen.getByRole('button', { name: 'Claim anyway' }));
    await waitFor(() => expect(claim).toHaveBeenCalledWith('task-1'));
  });

  it('claims a warm task in one click, with nothing in the way', async () => {
    const claim = vi.spyOn(tasksApi, 'claim').mockResolvedValue(makeTask());

    wrap(<TaskActions task={makeTask({ is_ready: true })} onChange={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Claim' }));

    await waitFor(() => expect(claim).toHaveBeenCalledWith('task-1'));
    expect(screen.queryByText(/not ready yet/)).not.toBeInTheDocument();
  });

  it('requires a reason before it will reject a task', async () => {
    const reject = vi.spyOn(tasksApi, 'reject').mockResolvedValue(makeTask({ status: 'rejected' }));

    wrap(
      <TaskActions
        task={makeTask({ status: 'submitted', available_transitions: ['rejected'] })}
      />
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));

    expect(reject).not.toHaveBeenCalled();
    expect(await screen.findByText('This is required.')).toBeInTheDocument();
  });

  it('sends the reason once one is given', async () => {
    const reject = vi.spyOn(tasksApi, 'reject').mockResolvedValue(makeTask({ status: 'rejected' }));

    wrap(
      <TaskActions
        task={makeTask({ status: 'submitted', available_transitions: ['rejected'] })}
      />
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('Reason'), 'Speaker labels are wrong');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith('task-1', 'Speaker labels are wrong')
    );
  });
});
