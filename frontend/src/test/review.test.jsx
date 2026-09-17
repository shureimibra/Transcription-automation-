import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReviewWorkspace from '../worker/ReviewWorkspace.jsx';
import DeadlineClock from '../worker/DeadlineClock.jsx';
import { segments as segmentsApi, submission, taskMedia, transcript } from '../api/eagle.js';
import { tasks as tasksApi } from '../api/workforce.js';

/**
 * The review surface.
 *
 * WHAT THESE PROTECT:
 *
 *  * Audio comes from OUR backend. A worker must never be handed an Eagle
 *    URL, and the boundary holds even though transcription audio happens to
 *    be public on Eagle's side.
 *  * The submission preview is FETCHED, never rendered here. A second
 *    formatter in the browser could disagree with the one that submits, and
 *    the disagreement would arrive as a rejection an hour later.
 *  * A critical finding blocks the submit button. A rejection costs the
 *    platform minutes and the worker's hour.
 *  * Per-segment speaker reassignment reaches the server, because that is
 *    the graded work on an Eagle transcription task.
 */

const TASK = {
  id: 'task-1',
  job: 'job-1',
  kind: 'transcription',
  status: 'in_progress',
  deadline_at: null,
};

const JOB = {
  id: 'job-1',
  segments: [
    { id: 's1', start_time: 0, end_time: 9, speaker_label: 'Male_1', text: 'first turn' },
    { id: 's2', start_time: 9, end_time: 20, speaker_label: 'Male_2', text: 'second turn' },
    { id: 's3', start_time: 20, end_time: 30, speaker_label: 'Male_1', text: 'third turn' },
  ],
};

beforeEach(() => {
  vi.spyOn(transcript, 'get').mockResolvedValue(JOB);
  // jsdom cannot decode audio; keep the element inert.
  window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  window.HTMLMediaElement.prototype.pause = vi.fn();
  // THE ACCURACY CHECK CARD LOADS ON EVERY RENDER OF A TRANSCRIPTION TASK
  // NOW, not only when its own button is clicked -- see ReviewWorkspace's
  // checkLoad. Left unmocked, every other describe block below would hit
  // the real (unavailable) network. A harmless "not completed yet"
  // default here; tests specifically about the card override it.
  vi.spyOn(submission, 'checkPrompt').mockResolvedValue({
    prompt: '', accuracy_check_notes: '', accuracy_check_by: null,
    accuracy_check_at: null,
  });
});

describe('the audio boundary', () => {
  it('points the player at our backend, never at Eagle', () => {
    expect(taskMedia.url('task-1')).toBe('/api/v1/workforce/tasks/task-1/media/');
    expect(taskMedia.url('task-1')).not.toContain('eaglewebcommerce');
  });

  it('renders a player sourced from that endpoint', async () => {
    const { container } = render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const audio = container.querySelector('audio');
    expect(audio).toBeTruthy();
    expect(audio.getAttribute('src')).toBe('/api/v1/workforce/tasks/task-1/media/');
  });

  it('offers playback speeds above 1x, because 25 minutes will not fit otherwise', async () => {
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    for (const rate of ['1×', '1.5×', '2×']) {
      expect(screen.getByRole('button', { name: rate })).toBeInTheDocument();
    }
  });
});

describe('a task that has already been submitted', () => {
  // THE SERVER ALREADY REFUSES A SECOND SUBMISSION. What it cannot do is
  // stop the screen inviting one: a delivered task still showed a full
  // editing surface and a live Submit button, which answered a reasonable
  // click with a red error.

  const SENT = { ...TASK, status: 'submitted', submitted_at: '2026-09-01T04:27:50Z' };

  it('says the work is delivered and will not be sent again', async () => {
    render(<ReviewWorkspace task={SENT} />);

    expect(await screen.findByText(/Submitted — waiting for review/)).toBeInTheDocument();
    expect(screen.getByText(/cannot be sent again/)).toBeInTheDocument();
  });

  it('offers to show what was sent, not to send it', async () => {
    render(<ReviewWorkspace task={SENT} />);

    expect(await screen.findByRole('button', { name: 'View what was submitted' }))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Preview submission' }))
      .not.toBeInTheDocument();
  });

  it('disables Submit inside the dialog, and says why', async () => {
    render(<ReviewWorkspace task={SENT} />);
    await userEvent.click(await screen.findByRole('button', { name: 'View what was submitted' }));

    const dialog = await screen.findByRole('dialog');
    const submit = within(dialog).getByRole('button', { name: 'Submit to platform' });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute('title', 'This work has already been submitted');
  });

  it('will not let delivered work be edited, because the platform has it already', async () => {
    // Editing here changes only OUR copy, so the two silently disagree and
    // the next reader sees something Eagle never received.
    render(<ReviewWorkspace task={SENT} />);
    await screen.findByText('first turn');

    // The speaker dropdown is present but inert.
    const speaker = screen.getAllByRole('combobox')[0];
    expect(speaker).toBeDisabled();

    // And no Edit button offers itself on delivered work -- editing now
    // goes through that button explicitly rather than the text itself.
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
  });

  it('still shows the working surface for a REJECTED task, which comes back for rework', async () => {
    render(<ReviewWorkspace task={{ ...TASK, status: 'rejected' }} />);

    expect(await screen.findByRole('button', { name: 'Preview submission' })).toBeInTheDocument();
    expect(screen.queryByText(/waiting for review/)).not.toBeInTheDocument();
  });
});

describe('the verbatim standard', () => {
  // The two standards are opposites, so applying the wrong one produces a
  // document that is wrong in every line. Eagle picks per task and we do not
  // yet receive that choice, so the screen has to distinguish "the platform
  // said" from "we assumed" -- a guess shown as a fact is the dangerous one.

  it('states the standard and what it asks for', async () => {
    render(<ReviewWorkspace task={{
      ...TASK,
      project: 'Law class',
      transcription_standard: {
        name: 'strict_verbatim',
        label: 'Strict verbatim (legal / court-admissible)',
        assumed: false,
      },
    }} />);

    expect(await screen.findByText(/Strict verbatim/)).toBeInTheDocument();
    expect(screen.getByText(/Keep every filler/)).toBeInTheDocument();
    // Not an alert: the platform told us, so there is nothing to check.
    expect(screen.queryByText(/did not tell us/)).not.toBeInTheDocument();
  });

  it('says plainly when the standard was assumed rather than given', async () => {
    render(<ReviewWorkspace task={{
      ...TASK,
      project: '',
      transcription_standard: {
        name: 'edited_verbatim',
        label: 'Edited verbatim (professional / academic)',
        assumed: true,
      },
    }} />);

    expect(await screen.findByText(/Assumed: Edited verbatim/)).toBeInTheDocument();
    expect(screen.getByText(/did not tell us which standard/)).toBeInTheDocument();
  });

  it('says nothing at all on a translation task, which has no such standard', async () => {
    render(<ReviewWorkspace task={{ ...TASK, transcription_standard: null }} />);

    await screen.findByText('first turn');
    expect(screen.queryByText(/verbatim/i)).not.toBeInTheDocument();
  });
});

describe('the transcript', () => {
  it('scrolls inside its own box, not the page, same as translation', async () => {
    // Ported from TranslationWorkspace's own equivalent test. Before this,
    // the highlight moved with the playhead but nothing scrolled to keep
    // it in view -- a reader had to go hunting for wherever it had
    // wandered off to on a long transcript.
    render(<ReviewWorkspace task={TASK} />);
    const line = await screen.findByText('first turn');

    const box = line.closest('.overflow-y-auto');
    expect(box).not.toBeNull();
    expect(box.className).toMatch(/max-h-/);
    expect(box.className).toMatch(/overscroll-contain/);
  });

  it('no longer opens the editor just from clicking the text', async () => {
    // THE BUG THIS REPLACES: clicking a segment's words used to jump
    // straight into edit mode, with no way to just listen to that segment
    // without also opening its editor. Editing now goes through the
    // explicit Edit button only.
    render(<ReviewWorkspace task={TASK} />);
    const line = await screen.findByText('first turn');

    await userEvent.click(line);
    expect(screen.queryByRole('textbox', { name: '' })).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    expect(screen.getByRole('textbox', { name: '' })).toBeInTheDocument();
  });

  describe('searching the transcript', () => {
    // JOB's three segments are "first turn", "second turn", "third turn" --
    // "turn" is in all three, "second" only in one. Real scenario this
    // covers: finding the handful of segments a misheard name actually
    // appears in, instead of reading past everything that never mentioned
    // it.

    it('is closed by default, costing nothing on a screen that is not searching', async () => {
      render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      expect(screen.queryByPlaceholderText('Search the transcript…')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Search/ })).toBeInTheDocument();
    });

    it('narrows to the matching segments and reports how many', async () => {
      const { container } = render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      await userEvent.click(screen.getByRole('button', { name: /Search/ }));
      await userEvent.type(
        screen.getByPlaceholderText('Search the transcript…'), 'second');

      expect(await screen.findByText('1 of 1')).toBeInTheDocument();
      // The matched substring is highlighted, splitting "second turn"
      // across sibling spans -- find rows by their full text content
      // rather than an exact node, same as the sentence-splitting tests
      // above handle the same shape of problem.
      const rows = [...container.querySelectorAll('li')];
      const firstRow = rows.find((li) => li.textContent.includes('first turn'));
      const secondRow = rows.find((li) => li.textContent.includes('second turn'));
      expect(firstRow.className).toMatch(/opacity-40/);
      expect(secondRow.className).not.toMatch(/opacity-40/);
    });

    it('steps through multiple matches with Next/Previous', async () => {
      render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      await userEvent.click(screen.getByRole('button', { name: /Search/ }));
      await userEvent.type(
        screen.getByPlaceholderText('Search the transcript…'), 'turn');

      expect(await screen.findByText('1 of 3')).toBeInTheDocument();
      await userEvent.click(screen.getByRole('button', { name: 'Next match' }));
      expect(await screen.findByText('2 of 3')).toBeInTheDocument();
      await userEvent.click(screen.getByRole('button', { name: 'Previous match' }));
      expect(await screen.findByText('1 of 3')).toBeInTheDocument();
    });

    it('space still pauses even while the search box has focus', async () => {
      // A REAL, REPORTED BUG: the player's own Space shortcut is disabled
      // while any text input has focus (correct, so it does not hijack
      // Space while someone is mid-sentence in the segment editor) -- but
      // that left a worker who had just searched with no way to pause at
      // all, typing silently into the query box instead, with no obvious
      // way back short of remembering to click elsewhere.
      render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      await userEvent.click(screen.getByRole('button', { name: /Search/ }));
      const input = screen.getByPlaceholderText('Search the transcript…');
      await userEvent.type(input, 'turn');
      await screen.findByText('1 of 3');

      await userEvent.type(input, ' ');
      // The space still landed in the query -- multi-word search ("h
      // power") must keep working, not be traded away for this fix.
      expect(input).toHaveValue('turn ');
      expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalled();
    });

    it('reports no matches rather than an empty count', async () => {
      render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      await userEvent.click(screen.getByRole('button', { name: /Search/ }));
      await userEvent.type(
        screen.getByPlaceholderText('Search the transcript…'), 'nonexistent');

      expect(await screen.findByText('No matches')).toBeInTheDocument();
    });

    it('closes and clears the query, un-dimming everything', async () => {
      render(<ReviewWorkspace task={TASK} />);
      await screen.findByText('first turn');

      await userEvent.click(screen.getByRole('button', { name: /Search/ }));
      const input = screen.getByPlaceholderText('Search the transcript…');
      await userEvent.type(input, 'second');
      await screen.findByText('1 of 1');

      await userEvent.click(screen.getByRole('button', { name: 'Close search' }));

      expect(screen.queryByPlaceholderText('Search the transcript…')).not.toBeInTheDocument();
      expect(screen.getByText('first turn').closest('li').className)
        .not.toMatch(/opacity-40/);
    });
  });

  it('lists every turn with its speaker and timing', async () => {
    render(<ReviewWorkspace task={TASK} />);

    expect(await screen.findByText('first turn')).toBeInTheDocument();
    expect(screen.getByText('second turn')).toBeInTheDocument();
    expect(screen.getByText('0:00 – 0:09')).toBeInTheDocument();
  });

  it('summarises speakers by how much each one talks', async () => {
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    // Male_1 holds two turns, Male_2 one.
    expect(screen.getByText('2 turns · 0 min')).toBeInTheDocument();
    expect(screen.getByText('1 turns · 0 min')).toBeInTheDocument();
  });

  it('sends a per-segment speaker change to the server', async () => {
    // The graded work on an Eagle transcription task: a global rename cannot
    // fix one turn attributed to the wrong person.
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({
      ...JOB.segments[0], speaker_label: 'Male_2',
    });
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const select = screen.getByLabelText('Speaker for the turn at 0:00');
    await userEvent.selectOptions(select, 'Male_2');

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('job-1', 's1', { speaker_label: 'Male_2' })
    );
  });

  it('saves an edited line', async () => {
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({
      ...JOB.segments[0], text: 'corrected',
    });
    render(<ReviewWorkspace task={TASK} />);

    await screen.findByText('first turn');
    // Clicking the text itself now seeks the audio, matching
    // TranslationWorkspace's rows -- editing goes through the explicit
    // Edit button instead. s1 ("first turn") is the first row.
    await userEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    // Two textboxes are on screen once the Accuracy check card is showing
    // (its own paste-back box has an accessible name); the segment editor
    // has none, so that is what tells them apart.
    const box = screen.getByRole('textbox', { name: '' });
    await userEvent.clear(box);
    await userEvent.type(box, 'corrected');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('job-1', 's1', { text: 'corrected' })
    );
  });

  it('inserts an annotation tag at the remembered cursor position, not just at the end', async () => {
    // The bug this pins down: the dropdown used to check live focus at
    // insert time, which is never the textarea once the dropdown itself
    // has been clicked -- so every tag silently landed at the end
    // regardless of where the cursor actually was.
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({
      ...JOB.segments[0], text: 'first[pause] turn',
    });
    render(<ReviewWorkspace task={TASK} />);

    await screen.findByText('first turn');
    // See the previous test for why this goes through the Edit button now.
    await userEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    // See the previous test for why this needs a name filter.
    const box = screen.getByRole('textbox', { name: '' });
    // "first turn" -- place the cursor right after "first" (index 5), not
    // at the end, and remember it the same way onSelect does in real use.
    box.setSelectionRange(5, 5);
    fireEvent.select(box);

    await userEvent.selectOptions(screen.getByTitle('Insert an annotation at the cursor'), '[pause]');

    expect(box.value).toBe('first[pause] turn');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('job-1', 's1', { text: 'first[pause] turn' })
    );
  });

  // The "no cursor ever recorded" fallback (append at the end) is already
  // covered directly against the pure function in annotations.test.js.
  // Not re-tested here: this textarea is `autoFocus`, and focusing it
  // establishes a real (0, 0) selection immediately in the test
  // environment, so asserting "no position known" would really be
  // asserting cross-browser autofocus-cursor placement semantics rather
  // than anything this component's own code controls.

  it('renames a speaker everywhere through the alias endpoint', async () => {
    const alias = vi.spyOn(transcript, 'setSpeakerAliases').mockResolvedValue({});
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    // Every chip carries this title, so pick the one for Male_1 explicitly
    // rather than relying on there happening to be a single speaker.
    const chips = screen.getAllByTitle('Rename this speaker everywhere');
    await userEvent.click(chips.find((c) => c.textContent.includes('Male_1')));
    const field = screen.getByLabelText('New name for Male_1');
    await userEvent.clear(field);
    await userEvent.type(field, 'Female_1');
    await userEvent.click(screen.getByRole('button', { name: 'Rename' }));

    await waitFor(() =>
      expect(alias).toHaveBeenCalledWith('job-1', { Male_1: 'Female_1' })
    );
  });
});

describe('the submission preview', () => {
  const goodPreview = {
    content: 'Transcription Job: x\n\nMale_1:\n[00:00:00.000 --> 00:00:09.000]\nfirst turn\n\n[END OF RECORDING --> 00:00:30.000]',
    format: 'eagle', characters: 120, words: 20, findings: [], blocking: false,
  };

  it('shows the server-rendered document rather than building one here', async () => {
    const preview = vi.spyOn(submission, 'preview').mockResolvedValue(goodPreview);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));

    expect(preview).toHaveBeenCalledWith('task-1');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/END OF RECORDING/)).toBeInTheDocument();
    expect(within(dialog).getByText(/exactly what the platform receives/i)).toBeInTheDocument();
  });

  it('blocks submitting when the gate reports something critical', async () => {
    vi.spyOn(submission, 'preview').mockResolvedValue({
      ...goodPreview,
      blocking: true,
      findings: [{ severity: 'critical', code: 'missing-timestamps',
                   message: 'No timestamps found. Eagle grades timing.' }],
    });
    const send = vi.spyOn(submission, 'submit');
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText(/Eagle grades timing/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Submit to platform' })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it('refuses to offer Submit when the preview itself was refused', async () => {
    // THE CASE THAT SLIPPED THROUGH. `blocking` only exists on a preview the
    // server managed to RENDER. When it refuses outright -- a translation
    // task with nothing translated -- the response is an error with no
    // `blocking` field, and reading that as false left Submit enabled and
    // inviting directly underneath the message explaining why the work
    // could not be sent.
    vi.spyOn(submission, 'preview').mockRejectedValue(
      Object.assign(
        new Error('This task has not been translated yet.'),
        { status: 409 },
      )
    );
    const send = vi.spyOn(submission, 'submit');
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText(/has not been translated yet/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Submit to platform' })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it('submits when the document is clean', async () => {
    vi.spyOn(submission, 'preview').mockResolvedValue(goodPreview);
    const send = vi.spyOn(submission, 'submit').mockResolvedValue({ ...TASK, status: 'submitted' });
    const onTaskChange = vi.fn();

    render(<ReviewWorkspace task={TASK} onTaskChange={onTaskChange} />);
    await screen.findByText('first turn');
    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit to platform' }));

    await waitFor(() => expect(send).toHaveBeenCalledWith('task-1'));
    await waitFor(() => expect(onTaskChange).toHaveBeenCalled());
  });

  it("surfaces the server's message when submission fails", async () => {
    vi.spyOn(submission, 'preview').mockResolvedValue(goodPreview);
    vi.spyOn(submission, 'submit').mockRejectedValue(
      Object.assign(new Error('The platform could not be reached.'), { status: 502 })
    );
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit to platform' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The platform could not be reached.'
    );
  });
});

describe('the accuracy check prompt', () => {
  // THE TRANSCRIPTION-SIDE COUNTERPART TO "Copy a check instead" ON A
  // TRANSLATION TASK. Report-only for the same reason: a worker's
  // assistant has never heard the audio, so it hands back leads to verify
  // rather than a document to paste over the transcript.

  it('copies the prompt together with the rendered document, not either alone', async () => {
    vi.spyOn(submission, 'checkPrompt').mockResolvedValue({
      prompt: 'TRANSCRIPT-CHECK-PROMPT ',
    });
    vi.spyOn(submission, 'preview').mockResolvedValue({
      content: 'Male_1:\nfirst turn', words: 2, characters: 10,
      findings: [], blocking: false,
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<ReviewWorkspace task={TASK} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Copy accuracy check' }));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const [payload] = writeText.mock.calls[0];
    expect(payload).toContain('TRANSCRIPT-CHECK-PROMPT');
    expect(payload).toContain('first turn');
  });

  it('is not offered on a translation task, which has no such prompt', async () => {
    render(<ReviewWorkspace task={{ ...TASK, kind: 'translation' }} />);
    await screen.findByText('first turn');

    expect(screen.queryByRole('button', { name: 'Copy accuracy check' })).not.toBeInTheDocument();
  });

  it('surfaces the error instead of copying nothing', async () => {
    vi.spyOn(submission, 'checkPrompt').mockRejectedValue(new Error('down for maintenance'));
    vi.spyOn(submission, 'preview').mockResolvedValue({
      content: 'Male_1:\nfirst turn', words: 2, characters: 10,
      findings: [], blocking: false,
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<ReviewWorkspace task={TASK} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Copy accuracy check' }));

    expect(await screen.findByText(/down for maintenance/)).toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });
});

describe('the accuracy check paste-back', () => {
  // REQUIRED, NOT OPTIONAL -- see run_quality_gate's
  // 'self-check-not-completed' finding. This card sits after every
  // editing tool above it (speakers, per-line correction) and before
  // Preview/Submit: an additional checkpoint, not a replacement for any
  // of them.

  it('shows the step as required until it has been saved', async () => {
    render(<ReviewWorkspace task={TASK} />);

    expect(await screen.findByText('Required before this task can be submitted'))
      .toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save accuracy check' })).toBeDisabled();
  });

  it('is disabled empty and enabled once something is pasted', async () => {
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const box = screen.getByLabelText('What your assistant found');
    const save = screen.getByRole('button', { name: 'Save accuracy check' });
    expect(save).toBeDisabled();

    await userEvent.type(box, 'Checked the names against the audio.');
    expect(save).not.toBeDisabled();
  });

  it('saves the paste-back and shows the step as completed', async () => {
    const save = vi.spyOn(submission, 'saveAccuracyCheck').mockResolvedValue({
      saved: true, findings: [], blocking: false,
    });
    vi.spyOn(submission, 'checkPrompt')
      .mockResolvedValueOnce({
        prompt: '', accuracy_check_notes: '', accuracy_check_by: null,
        accuracy_check_at: null,
      })
      .mockResolvedValueOnce({
        prompt: '', accuracy_check_notes: 'Nothing to report.',
        accuracy_check_by: 'W-01', accuracy_check_at: '2026-09-13T00:00:00Z',
      });
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(
      screen.getByLabelText('What your assistant found'), 'Nothing to report.');
    await userEvent.click(screen.getByRole('button', { name: 'Save accuracy check' }));

    await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', 'Nothing to report.'));
    expect(await screen.findByText('Completed by W-01')).toBeInTheDocument();
  });

  it('surfaces the error instead of losing what was typed', async () => {
    vi.spyOn(submission, 'saveAccuracyCheck').mockRejectedValue(new Error('down'));
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const box = screen.getByLabelText('What your assistant found');
    await userEvent.type(box, 'Nothing to report.');
    await userEvent.click(screen.getByRole('button', { name: 'Save accuracy check' }));

    expect(await screen.findByText('down')).toBeInTheDocument();
    expect(box).toHaveValue('Nothing to report.');
  });

  it('is not offered on a translation task', async () => {
    render(<ReviewWorkspace task={{ ...TASK, kind: 'translation' }} />);
    await screen.findByText('first turn');

    expect(screen.queryByText('Accuracy check')).not.toBeInTheDocument();
  });
});

describe('the accuracy-check checklist', () => {
  // Turns the SAME pasted report into one-click actions -- see
  // AccuracyChecklist's own docstring for why "Apply" writes straight to
  // the segment instead of opening a second editable draft.

  const REPORT = (
    '* the phrase as transcribed: first turn\n'
    + '* what you think it should probably be: First Turn\n'
    + '* why: Should be capitalized as a proper title.'
  );

  it('parses the pasted report into a checklist as you type', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(screen.getByLabelText('What your assistant found'), REPORT);

    expect(await screen.findByText('First Turn')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Find it' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeInTheDocument();
  });

  it('"Find it" opens the search box already searching for the phrase', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');
    await userEvent.type(screen.getByLabelText('What your assistant found'), REPORT);
    await screen.findByText('First Turn');

    await userEvent.click(screen.getByRole('button', { name: 'Find it' }));

    expect(await screen.findByPlaceholderText('Search the transcript…'))
      .toHaveValue('first turn');
  });

  it('"Apply" corrects the segment and records a permanent, dateable event', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({
      ...JOB.segments[0], text: 'First Turn',
    });
    const apply = vi.spyOn(tasksApi, 'applyFinding').mockResolvedValue({ recorded: true });
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');
    await userEvent.type(screen.getByLabelText('What your assistant found'), REPORT);
    await screen.findByText('First Turn');

    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      'job-1', 's1', { text: 'First Turn' },
    ));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(
      'task-1', 'first turn', 'First Turn',
    ));
  });

  it('finds and applies a finding that lists several spelling alternatives', async () => {
    // CONFIRMED LIVE: a rule-2 finding ("the SAME name spelled two
    // different wrong ways") reports its phrase as "X" / "Y" / "Z" -- and
    // no single transcript segment ever contains that whole joined
    // string, only X. Every such finding showed "Not found in the
    // transcript -- likely already fixed" regardless of whether it had
    // been, because Find it / Apply / the found-check searched for the
    // joined text itself instead of the first alternative.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({
      ...JOB.segments[0], text: 'First Turn',
    });
    vi.spyOn(tasksApi, 'applyFinding').mockResolvedValue({ recorded: true });
    const MULTI_REPORT = (
      '* the phrase as transcribed: "first turn" / "1st turn" / "furst turn"\n'
      + '* what you think it should probably be: First Turn\n'
      + '* why: The same phrase spelled three different wrong ways.'
    );
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(screen.getByLabelText('What your assistant found'), MULTI_REPORT);
    await screen.findByText('First Turn');

    // The card still shows every alternative -- that IS the evidence for
    // a rule-2 finding -- but it must not be a dead "Not found" label.
    // (Only the outermost quotes are stripped by the parser; the inner
    // ones, around the second and third alternatives, stay as typed.)
    expect(screen.getByText('first turn" / "1st turn" / "furst turn')).toBeInTheDocument();
    expect(screen.queryByText(/Not found in the transcript/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      'job-1', 's1', { text: 'First Turn' },
    ));
  });

  it('matches a phrase copied with a curly apostrophe against straight-quoted transcript text', async () => {
    // CONFIRMED LIVE: "Professor Blondell's course" showed as "Not found
    // in the transcript" although the transcript had that exact text --
    // the apostrophe on the clipboard (copied out of a chat UI's own
    // rendering) was a curly U+2019, and the one in the transcript was a
    // straight U+0027. Two different characters, rendered identically,
    // compared unequal.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    vi.spyOn(transcript, 'get').mockResolvedValue({
      ...JOB,
      segments: [
        { id: 's1', start_time: 0, end_time: 9, speaker_label: 'Male_1',
          text: "in Professor Blondell's course, he already covered it" },
      ],
    });
    const QUOTE_REPORT = (
      '* the phrase as transcribed: Professor Blondell’s course\n'
      + '* what you think it should probably be: Professor Blundell’s course\n'
      + '* why: A real Oxford physics professor, misspelled phonetically.'
    );
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText(/Professor Blondell/);

    // PASTE, NOT TYPE. A worker gets this text onto the clipboard from a
    // chat UI and pastes it — nobody types a report out character by
    // character — and `userEvent.type`'s simulated keystrokes are not a
    // reliable way to deliver a curly-quote code point in jsdom, which
    // would make a real fix look broken here for a reason that has
    // nothing to do with the fix.
    const box = screen.getByLabelText('What your assistant found');
    await userEvent.click(box);
    await userEvent.paste(QUOTE_REPORT);

    // Anchored: the checklist's parent line also contains this text as a
    // substring ("Blondell's course → Blundell's course"), and an
    // unanchored regex matches that ancestor too, on top of the specific
    // span this is actually checking.
    expect(await screen.findByText(/^Professor Blundell.s course$/)).toBeInTheDocument();
    expect(screen.queryByText(/Not found in the transcript/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Find it' })).toBeInTheDocument();
  });

  it('keeps the checklist on screen after a successful save, instead of wiping the box', async () => {
    // CONFIRMED LIVE: a worker pasted a real report, saved it, and saw
    // nothing but "Already saved by ... — pasting again replaces it" — no
    // checklist, nothing to click. The box used to be cleared to '' the
    // instant the save succeeded, and the checklist is parsed straight out
    // of that same box, so it vanished at exactly the moment it was needed.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    vi.spyOn(submission, 'saveAccuracyCheck').mockResolvedValue({
      saved: true, findings: [], blocking: false,
    });
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(screen.getByLabelText('What your assistant found'), REPORT);
    await screen.findByText('First Turn');

    await userEvent.click(screen.getByRole('button', { name: 'Save accuracy check' }));

    await waitFor(() => expect(submission.saveAccuracyCheck).toHaveBeenCalled());
    expect(screen.getByLabelText('What your assistant found')).toHaveValue(REPORT);
    expect(screen.getByText('First Turn')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Find it' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeInTheDocument();
  });

  it('rehydrates the checklist from what was already saved, across a reload', async () => {
    // checkPrompt's own docstring promises the screen can show the step as
    // done "across a reload" -- but the checklist needs the actual saved
    // TEXT, not just `accuracy_check_by`, or a worker reopening a task they
    // checked earlier sees only "Completed by ..." with nothing to act on.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    vi.spyOn(submission, 'checkPrompt').mockResolvedValue({
      prompt: '', accuracy_check_notes: REPORT,
      accuracy_check_by: 'W-01', accuracy_check_at: '2026-09-13T00:00:00Z',
    });

    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    expect(await screen.findByText('First Turn')).toBeInTheDocument();
    expect(screen.getByLabelText('What your assistant found')).toHaveValue(REPORT);
    expect(screen.getByRole('button', { name: 'Find it' })).toBeInTheDocument();
  });

  it('shows an already-applied finding as done, from real history, not the notes text', async () => {
    // The whole point: accuracy_check_notes only ever holds the LATEST
    // pasted report, so this has to come from a permanent event, not from
    // anything re-derived out of the box's current contents.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([
      {
        id: 1, event_type: 'ACCURACY_CHECK_FINDING_APPLIED',
        occurred_at: '2026-09-10T12:00:00Z',
        metadata: { phrase: 'first turn', suggestion: 'First Turn' },
      },
    ]);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');
    await userEvent.type(screen.getByLabelText('What your assistant found'), REPORT);

    expect(await screen.findByText(/Applied/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument();
  });

  it('tells a worker a phrase is likely already fixed instead of a dead button', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    const report = (
      '* the phrase as transcribed: something nobody said\n'
      + '* what you think it should probably be: a correction\n'
      + '* why: example'
    );
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(screen.getByLabelText('What your assistant found'), report);

    expect(await screen.findByText(/Not found in the transcript/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument();
  });

  it('drops a "not sure" answer -- nothing to search for or apply', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    const report = (
      '* the phrase as transcribed: first turn\n'
      + '* what you think it should probably be: not sure\n'
      + '* why: unclear'
    );
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(screen.getByLabelText('What your assistant found'), report);

    expect(screen.queryByRole('button', { name: 'Find it' })).not.toBeInTheDocument();
  });

  it('says so when a pasted reply parses into nothing, instead of silently showing nothing at all', async () => {
    // CONFIRMED LIVE: a worker's assistant replied in a real shape the
    // parser did not yet handle, the checklist rendered nothing, and the
    // reasonable conclusion from the outside was "this was never built."
    // Once the reply itself no longer parses (garbage, or any future
    // format drift), this message is what stands between that and silence.
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    await userEvent.type(
      screen.getByLabelText('What your assistant found'),
      'Everything looked fine, nothing to flag here.',
    );

    expect(await screen.findByText(/Nothing in that reply matched/)).toBeInTheDocument();
  });

  it('stays silent when the box is simply empty, not "nothing matched"', async () => {
    vi.spyOn(tasksApi, 'events').mockResolvedValue([]);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    expect(screen.queryByText(/Nothing in that reply matched/)).not.toBeInTheDocument();
  });
});

describe('the full guide', () => {
  it('opens on request and closes again', async () => {
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');
    expect(screen.queryByRole('dialog', { name: /Transcription job guide/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Full guide' }));
    const dialog = await screen.findByRole('dialog', { name: /Transcription job guide/i });
    expect(within(dialog).getByRole('heading', {
      name: /What the checks still miss/i, level: 3,
    })).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: /Transcription job guide/i })).not.toBeInTheDocument();
  });

  it('is not offered on a translation task, which has its own guide elsewhere', async () => {
    render(<ReviewWorkspace task={{ ...TASK, kind: 'translation' }} />);
    await screen.findByText('first turn');

    expect(screen.queryByRole('button', { name: 'Full guide' })).not.toBeInTheDocument();
  });
});

describe('the deadline clock', () => {
  it('shows nothing until the platform has set a deadline', () => {
    const { container } = render(<DeadlineClock task={{ deadline_at: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('counts down from the deadline the server gave', () => {
    const in30 = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    render(<DeadlineClock task={{ deadline_at: in30 }} />);
    expect(screen.getByText(/left to submit/)).toBeInTheDocument();
    expect(screen.getByText(/^29:|^30:/)).toBeInTheDocument();
  });

  it('escalates to an alert as the hour runs out', () => {
    const in3 = new Date(Date.now() + 3 * 60 * 1000).toISOString();
    render(<DeadlineClock task={{ deadline_at: in3 }} />);

    expect(screen.getByRole('alert')).toBeInTheDocument();
    // Colour is not the only channel: the instruction changes too.
    expect(screen.getByText(/Submit now/)).toBeInTheDocument();
  });

  it('says plainly when the deadline has passed', () => {
    const past = new Date(Date.now() - 60 * 1000).toISOString();
    render(<DeadlineClock task={{ deadline_at: past }} />);

    expect(screen.getByText('deadline passed')).toBeInTheDocument();
    expect(screen.getByText(/no longer be accepted/)).toBeInTheDocument();
  });
});

describe('the gender the machine guessed', () => {
  // WHY THIS MATTERS. Clustering -- how many people, and which lines are
  // whose -- is measured and reliable. Male-or-female is a pitch reading
  // against a fixed boundary, and adult ranges genuinely overlap: a real
  // lecture measured 178 Hz against a 165 Hz boundary and went out as
  // Female_1 on all 432 lines. The system detected the doubt and wrote it
  // to a log nobody reads.
  const UNCERTAIN = {
    ...JOB,
    document_metadata: {
      uncertain_speakers: [{ label: 'Male_1', median_f0: 178.0, samples: 57 }],
    },
  };

  it('warns before the worker starts editing', async () => {
    vi.spyOn(transcript, 'get').mockResolvedValue(UNCERTAIN);
    render(<ReviewWorkspace task={TASK} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Check this speaker/);
    expect(alert).toHaveTextContent('Male_1');
  });

  it('says nothing when the machine was confident', async () => {
    vi.spyOn(transcript, 'get').mockResolvedValue(JOB);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('flips male to female everywhere in one click', async () => {
    const alias = vi.spyOn(transcript, 'setSpeakerAliases').mockResolvedValue({});
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const flips = screen.getAllByRole('button', { name: /→ Female/ });
    await userEvent.click(flips[0]);

    await waitFor(() =>
      expect(alias).toHaveBeenCalledWith('job-1', { Male_1: 'Female_1' })
    );
  });

  it('keeps the number, so two speakers are never merged', async () => {
    // Male_2 -> Female_2, not Female_1. The number says WHICH person; only
    // the gender was ever in doubt.
    const alias = vi.spyOn(transcript, 'setSpeakerAliases').mockResolvedValue({});
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText('first turn');

    const flips = screen.getAllByRole('button', { name: /→ Female/ });
    await userEvent.click(flips[1]);

    await waitFor(() =>
      expect(alias).toHaveBeenCalledWith('job-1', { Male_2: 'Female_2' })
    );
  });
});

describe('following the audio while it plays', () => {
  // Reading along by ear is how proofreading actually gets done. A whole
  // highlighted segment does not help when the segment runs four sentences
  // -- the reviewer loses their place after every pause.
  const MULTI = {
    ...JOB,
    segments: [
      {
        id: 's1', start_time: 0, end_time: 10, speaker_label: 'Male_1',
        text: 'First sentence here. Second sentence here. Third sentence here.',
      },
    ],
  };

  it('splits the segment being spoken into sentences', async () => {
    vi.spyOn(transcript, 'get').mockResolvedValue(MULTI);
    const { container } = render(<ReviewWorkspace task={TASK} />);
    await screen.findByText(/First sentence here/);

    // All three are present, as separate spans rather than one block.
    expect(container.textContent).toContain('First sentence here.');
    expect(container.textContent).toContain('Second sentence here.');
    expect(container.textContent).toContain('Third sentence here.');
  });

  it('leaves a single-sentence segment alone', async () => {
    vi.spyOn(transcript, 'get').mockResolvedValue(JOB);
    render(<ReviewWorkspace task={TASK} />);

    // JOB's segments are one sentence each; the text renders unchanged.
    expect(await screen.findByText('first turn')).toBeInTheDocument();
  });

  it('still opens for editing via the Edit button', async () => {
    // The highlight wraps text in spans; the Edit button (rendered outside
    // that wrapped text, in the row header) must still work regardless.
    vi.spyOn(transcript, 'get').mockResolvedValue(MULTI);
    render(<ReviewWorkspace task={TASK} />);
    await screen.findByText(/First sentence here/);

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    // Unnamed, unlike the Accuracy check card's own paste-back textarea.
    expect(screen.getByRole('textbox', { name: '' })).toBeInTheDocument();
  });
});
