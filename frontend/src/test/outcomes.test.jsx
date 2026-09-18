import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReviewOutcomes from '../worker/ReviewOutcomes.jsx';

/**
 * A worker's own review outcomes.
 *
 * Until the sync learned to read Eagle's awarded feed, a submitted task's
 * status froze at "submitted" for ever -- the platform knew whether the work
 * was accepted and nobody here found out. Somebody could repeat the same
 * mistake for a week with no signal at all.
 */

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

const TASKS = [
  { id: 'a', external_task_id: '4452', kind: 'translation', status: 'approved',
    approved_at: '2026-08-30T10:00:00Z' },
  { id: 'b', external_task_id: '4130', kind: 'translation', status: 'rejected',
    rejected_at: '2026-08-26T10:00:00Z',
    rejection_reason: 'Speaker labels were wrong throughout.' },
  { id: 'c', external_task_id: '4639', kind: 'translation', status: 'submitted',
    submitted_at: '2026-09-01T04:00:00Z' },
];

describe('review outcomes', () => {
  it('counts each verdict separately', () => {
    wrap(<ReviewOutcomes tasks={TASKS} />);

    // Each label appears twice by design -- once in the tally at the top,
    // once on the row it describes -- so assert on the tally's number.
    expect(screen.getAllByText('Reviewed')).toHaveLength(2);
    expect(screen.getAllByText('Review failed')).toHaveLength(2);
    expect(screen.getAllByText('Awaiting review')).toHaveLength(2);
    expect(screen.getByText('1 of 2 accepted so far')).toBeInTheDocument();
  });

  it('shows the rejection reason in full, because that is the actionable part', () => {
    // The counts say how somebody is doing; only the reason says what to
    // change. It is never truncated or hidden behind a click.
    wrap(<ReviewOutcomes tasks={TASKS} />);

    expect(screen.getByText(/Speaker labels were wrong throughout/)).toBeInTheDocument();
  });

  it('names every task, so a worker can match it against the platform', () => {
    wrap(<ReviewOutcomes tasks={TASKS} />);

    for (const id of ['4452', '4130', '4639']) {
      expect(screen.getByText(new RegExp(id))).toBeInTheDocument();
    }
  });

  it('shows pending work as a state rather than an absence', () => {
    // Review takes up to ~32 hours, so most of a day sits undecided. Saying
    // so is what stops "no news" reading as "something went wrong".
    wrap(<ReviewOutcomes tasks={[TASKS[2]]} />);

    expect(screen.getAllByText('Awaiting review').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Nothing submitted yet/)).not.toBeInTheDocument();
  });

  it('says so plainly when nothing has been submitted', () => {
    wrap(<ReviewOutcomes tasks={[]} />);

    expect(screen.getByText('Nothing submitted yet')).toBeInTheDocument();
  });

  it('says nothing about a pass rate on a small sample', () => {
    // 1 of 2 is technically 50%, but a rate computed from two outcomes is
    // noise, not a verdict -- showing a precise-looking number here would
    // mislead more than it would inform.
    wrap(<ReviewOutcomes tasks={TASKS} />);

    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('shows the pass rate against the 92% target once there is enough decided work', () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      id: `t${i}`, external_task_id: `500${i}`, kind: 'translation',
      status: i < 9 ? 'approved' : 'rejected',
      approved_at: i < 9 ? '2026-09-01T00:00:00Z' : undefined,
      rejected_at: i >= 9 ? '2026-09-01T00:00:00Z' : undefined,
    }));
    wrap(<ReviewOutcomes tasks={many} />);

    // 9 of 10 accepted = 90%, below the 92% target.
    expect(screen.getByText(/90%, target 92%\+/)).toBeInTheDocument();
  });

  it('flags the rate when it is below target, and not when it clears it', () => {
    const below = Array.from({ length: 10 }, (_, i) => ({
      id: `b${i}`, external_task_id: `600${i}`, kind: 'translation',
      status: i < 8 ? 'approved' : 'rejected',
      approved_at: i < 8 ? '2026-09-01T00:00:00Z' : undefined,
      rejected_at: i >= 8 ? '2026-09-01T00:00:00Z' : undefined,
    }));
    const { container: belowContainer } = wrap(<ReviewOutcomes tasks={below} />);
    expect(belowContainer.querySelector('header p .text-status-rejected.font-semibold')).toBeTruthy();

    const above = Array.from({ length: 10 }, (_, i) => ({
      id: `a${i}`, external_task_id: `700${i}`, kind: 'translation',
      status: i < 10 ? 'approved' : 'rejected',
      approved_at: '2026-09-01T00:00:00Z',
    }));
    const { container: aboveContainer } = wrap(<ReviewOutcomes tasks={above} />);
    expect(aboveContainer.querySelector('header p .text-status-rejected.font-semibold')).toBeFalsy();
  });

  it('ignores task states that are not a review outcome', () => {
    // A cancelled or failed task was never reviewed; listing it under
    // outcomes would misreport what the platform decided.
    wrap(<ReviewOutcomes tasks={[
      { id: 'x', external_task_id: '999', status: 'cancelled' },
      { id: 'y', external_task_id: '998', status: 'failed' },
    ]} />);

    expect(screen.getByText('Nothing submitted yet')).toBeInTheDocument();
  });
});
