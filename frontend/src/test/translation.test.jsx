import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TranslationWorkspace from '../worker/TranslationWorkspace.jsx';
import { segments as segmentsApi, submission, translation } from '../api/eagle.js';

/**
 * The translation surface.
 *
 * WHAT THESE PROTECT:
 *
 *  * The worker sees the FRENCH -- the document that actually gets
 *    submitted. The old screen showed job.segments, which on a translation
 *    task are the English source, so edits went to one document and the
 *    submission came from another.
 *  * The English CAN be corrected, and doing so warns that the French has
 *    not followed. A mis-heard name makes the French say something false,
 *    and no amount of polishing repairs that -- the assistant doing the
 *    polishing only ever sees the French.
 *  * A paste-back is checked the moment it lands, not at submission time.
 *  * A refused preview disables Submit.
 */

const TASK = {
  id: 'task-1',
  job: 'job-1',
  kind: 'translation',
  status: 'in_progress',
  deadline_at: null,
};

const DRAFT = {
  task: 'task-1',
  source_language: 'en',
  target_language: 'fr',
  source: [
    { id: 's1', start_time: 0, end_time: 9, text: 'Several times a second' },
    { id: 's2', start_time: 9, end_time: 20, text: 'It was nicknamed LGM1' },
  ],
  draft: [
    { start_time: 0, end_time: 9, text: 'Plusieurs fois par seconde' },
    { start_time: 9, end_time: 20, text: 'Il a été surnommé LGM1' },
  ],
  draft_text: 'Plusieurs fois par seconde\n\nIl a été surnommé LGM1',
  polished_text: '',
  polished_by: null,
  polished_at: null,
  // The server supplies these; a fixture without them lets a broken copy
  // button pass, because the code falls back to sending the text alone.
  prompts: {
    translate: 'TRANSLATE-PROMPT rules here --- TEXT --- ',
    polish: 'POLISH-PROMPT rules here --- TRANSLATION --- ',
    review: 'REVIEW-PROMPT DO NOT REWRITE --- TRANSLATION (French) --- ',
  },
};

beforeEach(() => {
  vi.spyOn(translation, 'get').mockResolvedValue(DRAFT);
  window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

describe('the two documents', () => {
  it('shows the French, not only the English', async () => {
    render(<TranslationWorkspace task={TASK} />);

    expect(await screen.findByText('Plusieurs fois par seconde')).toBeInTheDocument();
    expect(screen.getByText('Il a été surnommé LGM1')).toBeInTheDocument();
  });

  it('shows the English source alongside it', async () => {
    render(<TranslationWorkspace task={TASK} />);

    expect(await screen.findByText('Several times a second')).toBeInTheDocument();
    expect(screen.getByText('English (source)')).toBeInTheDocument();
  });

  it('does not put the English into an edit box until it is asked to', async () => {
    // Editable does not mean edit-by-default. A screen of open textareas
    // invites changes to a document that is an INPUT, and most tasks need
    // none.
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    const boxes = screen.getAllByRole('textbox');
    expect(boxes).toHaveLength(1);
    expect(boxes[0]).toHaveAttribute('id', 'polished');
  });

  it('lets a worker correct the English, and says the French has not followed', async () => {
    // THE WARNING IS THE POINT. Correcting the source is the only repair for
    // a mis-heard name, and a worker who fixes it and submits without
    // re-translating has changed nothing about the document Eagle receives.
    const update = vi.spyOn(segmentsApi, 'update').mockResolvedValue({});
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.click(screen.getAllByRole('button', { name: 'edit' })[0]);
    const box = screen.getByDisplayValue('Several times a second');
    await userEvent.clear(box);
    await userEvent.type(box, 'Several times per second');
    await userEvent.click(screen.getByRole('button', { name: 'Save English' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      'job-1', 's1', { text: 'Several times per second' },
    ));
    expect(await screen.findByText(/French has NOT changed/)).toBeInTheDocument();
  });
});

describe('following the audio', () => {
  it('scrolls the transcript inside its own box, not the page', async () => {
    // THE BUG THIS PREVENTS. `scrollIntoView` scrolls the nearest scrollable
    // ANCESTOR. With no scroll box around the list that ancestor was the
    // page, so following the audio dragged the player and the Preview button
    // off the top of the screen -- and scrolling back down to reach Pause was
    // undone by the next segment a second later. There was no way to stop it
    // except to stop the audio, which was what the scrolling was for.
    render(<TranslationWorkspace task={TASK} />);
    const line = await screen.findByText('Several times a second');

    const box = line.closest('.overflow-y-auto');
    expect(box).not.toBeNull();
    // Bounded, or it is not a scroll box -- it just grows and moves the page.
    expect(box.className).toMatch(/max-h-/);
    // The page must not be dragged along with it.
    expect(box.className).toMatch(/overscroll-contain/);
  });

  it('keeps the column headings visible while it scrolls', async () => {
    render(<TranslationWorkspace task={TASK} />);
    const heading = await screen.findByText('English (source)');

    expect(heading.parentElement.className).toMatch(/sticky/);
  });
});

describe('the polish round trip', () => {
  it('sends the pasted text to the server and reports what was found', async () => {
    const save = vi.spyOn(translation, 'save').mockResolvedValue({
      saved: true,
      words: 6,
      blocking: false,
      findings: [
        { severity: 'high', code: 'entity.invented-reference',
          message: 'Domain(s) in the output that the source never mentions: planethunters.org.' },
      ],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Texte poli');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', 'Texte poli'));
    expect(await screen.findByText(/planethunters\.org/)).toBeInTheDocument();
  });

  it('copies the prompt together with the French, not the French alone', async () => {
    // THE WHOLE POINT OF THE FEATURE. The screen used to name a prompt it
    // did not supply, so each worker brought their own and applied their
    // own standard. If this ever sends the text by itself again, that is
    // silently back.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<TranslationWorkspace task={TASK} />);
    await userEvent.click(await screen.findByRole('button', { name: /Copy prompt \+ translation/i }));

    const [payload] = writeText.mock.calls[0];
    expect(payload).toContain('POLISH-PROMPT');
    expect(payload).toContain('Plusieurs fois par seconde');
  });

  it('the check button sends the reporting prompt, not the polishing one', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<TranslationWorkspace task={TASK} />);
    await userEvent.click(await screen.findByRole('button', { name: /Copy a check instead/i }));

    const [payload] = writeText.mock.calls[0];
    expect(payload).toContain('DO NOT REWRITE');
    expect(payload).not.toContain('POLISH-PROMPT');
  });

  it('says so plainly when nothing was flagged', async () => {
    vi.spyOn(translation, 'save').mockResolvedValue({
      saved: true, words: 6, blocking: false, findings: [],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Texte poli');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    expect(await screen.findByText(/Nothing flagged/)).toBeInTheDocument();
  });

  it('will not save an empty box', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    expect(screen.getByRole('button', { name: 'Save polished version' })).toBeDisabled();
  });
});

describe('submitting', () => {
  it('blocks Submit when the preview was refused outright', async () => {
    vi.spyOn(submission, 'preview').mockRejectedValue(
      Object.assign(new Error('This task has not been translated yet.'), { status: 409 })
    );
    const send = vi.spyOn(submission, 'submit');
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('button', { name: 'Submit to platform' })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it('submits a clean document', async () => {
    vi.spyOn(submission, 'preview').mockResolvedValue({
      content: 'Translation Job: x\n\nTranslation :\n\nBonjour\n\n(Number Of Words: 1)',
      findings: [], blocking: false, word_count: 1,
    });
    const send = vi.spyOn(submission, 'submit')
      .mockResolvedValue({ ...TASK, status: 'submitted' });
    const onTaskChange = vi.fn();

    render(<TranslationWorkspace task={TASK} onTaskChange={onTaskChange} />);
    await screen.findByText('Plusieurs fois par seconde');
    await userEvent.click(screen.getByRole('button', { name: 'Preview submission' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit to platform' }));

    await waitFor(() => expect(send).toHaveBeenCalledWith('task-1'));
    await waitFor(() => expect(onTaskChange).toHaveBeenCalled());
  });
});

describe('when the machine translation is missing', () => {
  // THE BUG THIS PINS. The paste-back box used to be hidden whenever there
  // was no machine draft -- which is exactly when a worker most needs it,
  // because translating the task themselves is the fallback the whole
  // architecture depends on. On the first real submission the box was
  // absent at the only moment it mattered.
  const NO_DRAFT = { ...DRAFT, draft: [], draft_text: '' };

  beforeEach(() => {
    vi.spyOn(translation, 'get').mockResolvedValue(NO_DRAFT);
  });

  it('offers the prompt with the English, not the English alone', async () => {
    // THE BRANCH NOBODY SEES. This card only renders when the machine
    // translation failed, so a regression here would sit unnoticed until
    // the one day a worker actually needed it -- which is the day the
    // fallback the whole architecture depends on stops working.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<TranslationWorkspace task={TASK} />);
    await userEvent.click(await screen.findByRole('button', { name: /Copy prompt \+ English/i }));

    const [payload] = writeText.mock.calls[0];
    expect(payload).toContain('TRANSLATE-PROMPT');
    expect(payload).toContain('Several times a second');
  });

  it('still offers somewhere to paste a translation', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    expect(screen.getByLabelText('Polished French')).toBeInTheDocument();
  });

  it('says the French is not there rather than showing an empty table', async () => {
    render(<TranslationWorkspace task={TASK} />);

    expect(await screen.findByText(/not produced yet/i)).toBeInTheDocument();
  });

  it('still shows the English to translate from', async () => {
    render(<TranslationWorkspace task={TASK} />);

    expect(await screen.findByText('It was nicknamed LGM1')).toBeInTheDocument();
  });

  it('accepts a translation typed from scratch', async () => {
    const save = vi.spyOn(translation, 'save').mockResolvedValue({
      saved: true, words: 2, blocking: false, findings: [],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Bonjour tout');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', 'Bonjour tout'));
  });
});
