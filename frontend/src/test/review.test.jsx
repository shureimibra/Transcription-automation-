import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReviewWorkspace from '../worker/ReviewWorkspace.jsx';
import DeadlineClock from '../worker/DeadlineClock.jsx';
import { segments as segmentsApi, submission, taskMedia, transcript } from '../api/eagle.js';

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

    // And the transcript text no longer offers itself for editing.
    const line = screen.getByText('first turn');
    expect(line.closest('[role="button"]')).toBeNull();
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

    await userEvent.click(await screen.findByText('first turn'));
    const box = screen.getByRole('textbox');
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

    await userEvent.click(await screen.findByText('first turn'));
    const box = screen.getByRole('textbox');
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

  it('still opens for editing when clicked', async () => {
    // The highlight wraps text in spans; the click target must survive it.
    vi.spyOn(transcript, 'get').mockResolvedValue(MULTI);
    render(<ReviewWorkspace task={TASK} />);

    await userEvent.click(await screen.findByText(/First sentence here/));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });
});
