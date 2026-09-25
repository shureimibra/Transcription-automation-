import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import AdminWorkers from '../admin/AdminWorkers.jsx';
import { stats as statsApi, workers as workersApi } from '../api/workforce.js';

/**
 * The business's own stated targets -- 90 tasks/day, 92%+ accepted -- shown
 * against the real, date-scoped numbers, plus the real rejection reasons
 * together in one place so a recurring defect is visible without reading
 * one task at a time. See analytics.py's own docstring for why this lives
 * on the date-scoped report rather than the live overview.
 */

function performance(overrides = {}) {
  return {
    range: { from: '2026-08-19', to: '2026-09-18' },
    totals: {
      submitted: 20, approved: 18, rejected: 2, decided: 20,
      approval_rate: 0.9, rejection_rate: 0.1, created: 22,
      avg_submitted_per_day: 0.67, daily_target: 90, pass_rate_target: 0.92,
    },
    workers: [],
    recent_rejections: [],
    ...overrides,
  };
}

async function renderPage(overrides = {}) {
  vi.spyOn(statsApi, 'performance').mockResolvedValue(performance(overrides));
  vi.spyOn(workersApi, 'list').mockResolvedValue([]);
  render(<AdminWorkers />);
  // Let both async loads resolve.
  await screen.findByText('Workers');
}

describe('admin performance report', () => {
  it('shows the daily submission target beside the real average', async () => {
    await renderPage();

    expect(screen.getByText('target 90/day')).toBeInTheDocument();
  });

  it('shows the pass-rate target beside the real approval rate', async () => {
    await renderPage();

    expect(screen.getByText(/target 92%\+/)).toBeInTheDocument();
  });

  it('lists a recent rejection with its real reason, worker and task id', async () => {
    await renderPage({
      recent_rejections: [{
        id: 'abc12345', external_task_id: '6021', kind: 'translation',
        rejected_at: '2026-09-17T20:57:13Z', worker_code: 'W-01',
        rejection_reason: 'Duplicate name in the speaker list.',
      }],
    });

    expect(screen.getByText(/Task 6021/)).toBeInTheDocument();
    expect(screen.getByText(/W-01/)).toBeInTheDocument();
    expect(screen.getByText('Duplicate name in the speaker list.')).toBeInTheDocument();
  });

  it('says plainly when nothing was rejected in the range, instead of an empty gap', async () => {
    await renderPage({ recent_rejections: [] });

    expect(screen.getByText('No rejections in this range')).toBeInTheDocument();
  });

  it('shows how many tasks each worker has awaiting review, as the worker sees it', async () => {
    await renderPage({
      totals: {
        submitted: 10, approved: 3, rejected: 3, decided: 6, awaiting_review: 4,
        approval_rate: 0.5, rejection_rate: 0.5, created: 12,
        avg_submitted_per_day: 0.3, daily_target: 90, pass_rate_target: 0.92,
      },
      workers: [{
        worker_code: 'W-01', display_name: 'nasra', status: 'active',
        submitted: 10, approved: 3, rejected: 3, awaiting_review: 4, decided: 6,
        approval_rate: 0.5, rejection_rate: 0.5, held_now: 0,
        median_handling_seconds: 1380, median_realtime_factor: 1.23,
      }],
    });

    // The column header, the overall stat, and this worker's own figure.
    expect(screen.getAllByText('Awaiting review').length).toBeGreaterThanOrEqual(2);
    const row = screen.getByText('nasra').closest('tr');
    expect(row).toHaveTextContent('4');
  });
});
