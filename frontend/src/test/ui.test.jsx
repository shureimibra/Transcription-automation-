import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StatusBadge, { statusMeta } from '../components/StatusBadge.jsx';
import { Button, ExpandableText, Stat } from '../components/ui/index.jsx';
import { duration, elapsed, percent, taskLabel, taskRoute } from '../lib/format.js';

// Small pieces, but each one encodes a decision that is easy to reverse by
// accident: an unknown status must stay visibly unknown, a missing number
// must not read as zero, and a button mid-request must not be clickable
// twice.

describe('StatusBadge', () => {
  it('names the states it knows', () => {
    render(<StatusBadge status="ready_to_submit" />);
    expect(screen.getByText('Ready to submit')).toBeInTheDocument();
  });

  it('shows a status it does not know rather than inventing a label', () => {
    render(<StatusBadge status="quarantined" />);
    // Visibly unknown beats confidently wrong about the state of someone's
    // work.
    expect(screen.getByText('quarantined')).toBeInTheDocument();
  });

  it('never relies on colour alone', () => {
    const meta = statusMeta('rejected');
    render(<StatusBadge status="rejected" />);
    // The word is present, not just the dot.
    expect(screen.getByText(meta.label)).toBeInTheDocument();
  });
});

describe('Stat', () => {
  it('renders a real zero as zero', () => {
    render(<Stat label="Rejected" value={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('renders an em dash when there is no value, not a zero', () => {
    render(<Stat label="Approval rate" value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });
});

describe('Button', () => {
  it('cannot be clicked twice while it is loading', async () => {
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Claim</Button>);

    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');

    await userEvent.click(button, { pointerEventsCheck: 0 });
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('ExpandableText', () => {
  it('shows short text in full, with no toggle at all', () => {
    render(<ExpandableText text="Speaker labels were wrong throughout." />);
    expect(screen.getByText('Speaker labels were wrong throughout.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('collapses long text to a preview, and expands it on click', async () => {
    const long = 'Mistake. '.repeat(40); // well past the default 220-char preview
    render(<ExpandableText text={long} />);

    expect(screen.getByText(/…$/)).toBeInTheDocument();
    expect(screen.queryByText(long)).not.toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: 'Show full feedback' });

    await userEvent.click(toggle);

    // getByText normalises whitespace when matching, so compare against the
    // trimmed form -- the rendered text is identical either way.
    expect(screen.getByText(long.trim())).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  });

  it('renders nothing at all for empty text, not an empty toggle', () => {
    const { container } = render(<ExpandableText text="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('formatting', () => {
  it('formats media length without an hour that is not there', () => {
    expect(duration(1245)).toBe('20:45');
    expect(duration(3725)).toBe('1:02:05');
    expect(duration(null)).toBe('—');
  });

  it('formats elapsed time in units a person reads', () => {
    expect(elapsed(45)).toBe('45s');
    expect(elapsed(600)).toBe('10m');
    expect(elapsed(5400)).toBe('1h 30m');
    expect(elapsed(3600)).toBe('1h');
  });

  it('keeps a missing rate missing instead of turning it into 0%', () => {
    expect(percent(0.8421)).toBe('84%');
    expect(percent(0)).toBe('0%');
    expect(percent(null)).toBeNull();
    expect(percent(undefined)).toBeNull();
  });

  it('describes a translation as a route and a transcription as a language', () => {
    expect(taskRoute({ kind: 'translation', source_language: 'en', target_language: 'fr' }))
      .toMatch(/→/);
    expect(taskRoute({ kind: 'transcription', source_language: '' }))
      .toBe('Auto-detect');
  });

  it('shows a real filename beside the task id', () => {
    expect(taskLabel({ external_task_id: '6021', title: 'A 116.mp3' }))
      .toBe('6021 · A 116.mp3');
  });

  it('keeps the task id in the filename but drops Eagle\'s own name from it', () => {
    // Eagle itself sends this for some batches. The id is worth keeping --
    // it is what a follow-up gets filed under -- but the platform's own
    // branding in front of it is not something a worker needs to see.
    expect(taskLabel({
      external_task_id: '39005', title: 'EagleTranscribe-task-39005-source.mp3',
    })).toBe('39005 · task-39005-source.mp3');
    expect(taskLabel({
      external_task_id: '4210', title: 'EagleTranslate-task-4210-source.wav',
    })).toBe('4210 · task-4210-source.wav');
  });

  it('does not touch a real filename that merely contains the word task', () => {
    // Only a leading "Eagle<Word>-" is stripped -- it must not eat a real,
    // distinguishing filename by accident.
    expect(taskLabel({ external_task_id: '501', title: 'task force meeting.mp3' }))
      .toBe('501 · task force meeting.mp3');
  });
});
