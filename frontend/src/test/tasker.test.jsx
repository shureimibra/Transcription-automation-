import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReviewWorkspace from '../worker/ReviewWorkspace.jsx';
import { WorkspaceApiProvider } from '../worker/workspaceApi.js';
import { segments as workerSegments, submission as workerSubmission } from '../api/eagle.js';
import { taskerWorkspaceApi } from '../api/tasker.js';

/**
 * Outside Eagle taskers reuse the workers' review screen.
 *
 * WHAT THESE PROTECT:
 *
 *  * A tasker's edits go to THEIR copy, never through the worker endpoints
 *    that edit the shared transcript.
 *  * Their screen offers Copy, never Submit -- nothing is sent from here.
 *  * Every tasker call is addressed by task, not by the shared job.
 */

const TASK = {
  id: 'task-1', job: 'copy', kind: 'transcription', status: 'desk',
  external_task_id: '41052',
};

const TRANSCRIPT = {
  id: 'copy-1',
  segments: [
    { id: 's1', start_time: 0, end_time: 9, speaker_label: 'Male_1', text: 'first turn' },
    { id: 's2', start_time: 9, end_time: 20, speaker_label: 'Male_2', text: 'second turn' },
  ],
  speaker_aliases: {},
};

function fakeApi() {
  const api = taskerWorkspaceApi('task-1');
  api.transcript.get = vi.fn().mockResolvedValue(TRANSCRIPT);
  api.segments.update = vi.fn().mockImplementation((_j, id, changes) =>
    Promise.resolve({ ...TRANSCRIPT.segments.find((s) => s.id === id), ...changes }));
  api.submission.checkPrompt = vi.fn().mockResolvedValue({
    prompt: '', accuracy_check_notes: '', accuracy_check_by: null, accuracy_check_at: null,
  });
  api.submission.preview = vi.fn().mockResolvedValue({
    content: 'Transcription Job: A talk\nTranscribed By: [Your name]\n…',
    words: 6, characters: 50, findings: [], blocking: false, placeholder: '[Your name]',
  });
  api.tasks.events = vi.fn().mockResolvedValue([]);
  return api;
}

function renderAsTasker(api) {
  return render(
    <WorkspaceApiProvider value={api}>
      <ReviewWorkspace task={TASK} />
    </WorkspaceApiProvider>,
  );
}

beforeEach(() => {
  window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

describe('the tasker API', () => {
  it('addresses every call by task, through the tasker endpoints', () => {
    const api = taskerWorkspaceApi('task-1');
    expect(api.mode).toBe('desk');
    expect(api.taskMedia.url()).toBe('/api/v1/workforce/tasker/tasks/task-1/media/');
  });
});

describe('the review screen in tasker mode', () => {
  it('plays audio from the tasker endpoint', async () => {
    const { container } = renderAsTasker(fakeApi());
    await screen.findByText('first turn');
    expect(container.querySelector('audio').getAttribute('src'))
      .toBe('/api/v1/workforce/tasker/tasks/task-1/media/');
  });

  it('saves an edit to the private copy, never through the worker endpoint', async () => {
    const workerUpdate = vi.spyOn(workerSegments, 'update');
    const api = fakeApi();
    renderAsTasker(api);
    await screen.findByText('first turn');

    await userEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    const box = screen.getByDisplayValue('first turn');
    await userEvent.clear(box);
    await userEvent.type(box, 'corrected turn');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.segments.update).toHaveBeenCalledWith(
      'copy', 's1', { text: 'corrected turn' }));
    expect(workerUpdate).not.toHaveBeenCalled();
  });

  it('offers the final draft with Copy, and no Submit', async () => {
    const workerPreview = vi.spyOn(workerSubmission, 'preview');
    renderAsTasker(fakeApi());
    await screen.findByText('first turn');

    expect(screen.queryByRole('button', { name: /preview submission/i })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Get final draft' }));

    expect(await screen.findByRole('button', { name: 'Copy final draft' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /submit to platform/i })).toBeNull();
    expect(screen.getAllByText('[Your name]').length).toBeGreaterThan(0);
    expect(workerPreview).not.toHaveBeenCalled();
  });

  it('calls the accuracy check recommended, not required', async () => {
    renderAsTasker(fakeApi());
    expect(await screen.findByText('Recommended before you paste your final draft')).toBeInTheDocument();
  });
});
