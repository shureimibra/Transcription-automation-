import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TranslationWorkspace from '../worker/TranslationWorkspace.jsx';
import {
  aiRevisions, segments as segmentsApi, submission, translation,
} from '../api/eagle.js';
import { ApiError } from '../api/client.js';

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

    await userEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    const box = screen.getByDisplayValue('Several times a second');
    await userEvent.clear(box);
    await userEvent.type(box, 'Several times per second');
    await userEvent.click(screen.getByRole('button', { name: 'Save English' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      'job-1', 's1', { text: 'Several times per second' },
    ));
    expect(await screen.findByText(/French has NOT changed/)).toBeInTheDocument();
  });

  it('does not offer Revert on a line that was never edited', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    expect(screen.queryByRole('button', { name: 'Revert to original' })).not.toBeInTheDocument();
    expect(screen.queryByText('edited')).not.toBeInTheDocument();
  });
});

describe('searching the English', () => {
  // Searches the English, not the French: that is the side a worker
  // actually reads against the audio, and what a mishearing would turn
  // up in. "LGM1" is unique to the second row; "second" only in the
  // first.

  it('is closed by default', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    expect(screen.queryByPlaceholderText('Search the English…')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Search/ })).toBeInTheDocument();
  });

  it('narrows to the matching row and dims the other one', async () => {
    const { container } = render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.click(screen.getByRole('button', { name: /Search/ }));
    await userEvent.type(
      screen.getByPlaceholderText('Search the English…'), 'LGM1');

    expect(await screen.findByText('1 of 1')).toBeInTheDocument();
    const rows = [...container.querySelectorAll('li')];
    const firstRow = rows.find((li) => li.textContent.includes('Several times a second'));
    const secondRow = rows.find((li) => li.textContent.includes('nicknamed'));
    expect(firstRow.className).toMatch(/opacity-40/);
    expect(secondRow.className).not.toMatch(/opacity-40/);
  });

  it('reports no matches rather than an empty count', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.click(screen.getByRole('button', { name: /Search/ }));
    await userEvent.type(
      screen.getByPlaceholderText('Search the English…'), 'nonexistent');

    expect(await screen.findByText('No matches')).toBeInTheDocument();
  });
});

describe('reverting an edited line', () => {
  const EDITED_DRAFT = {
    ...DRAFT,
    source: [
      { ...DRAFT.source[0], is_edited: true },
      DRAFT.source[1],
    ],
  };

  it('marks an edited line and offers a way back', async () => {
    vi.spyOn(translation, 'get').mockResolvedValue(EDITED_DRAFT);
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    expect(screen.getByText('edited')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revert to original' })).toBeInTheDocument();
  });

  it('sends the revert request for that line and refreshes', async () => {
    vi.spyOn(translation, 'get').mockResolvedValue(EDITED_DRAFT);
    const revert = vi.spyOn(segmentsApi, 'revert').mockResolvedValue({});
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.click(screen.getByRole('button', { name: 'Revert to original' }));

    await waitFor(() => expect(revert).toHaveBeenCalledWith('job-1', 's1'));
    // A silent refresh means a second call to the same loader.
    await waitFor(() => expect(translation.get).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/Reverted to the original English/)).toBeInTheDocument();
  });

  it('surfaces a server refusal rather than pretending it worked', async () => {
    vi.spyOn(translation, 'get').mockResolvedValue(EDITED_DRAFT);
    vi.spyOn(segmentsApi, 'revert').mockRejectedValue(
      new ApiError('This line has not been edited.', { status: 400 })
    );
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    await userEvent.click(screen.getByRole('button', { name: 'Revert to original' }));

    expect(await screen.findByText(/Could not revert that line/)).toBeInTheDocument();
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

    // Sticky on the wrapper that also holds the search bar now, not the
    // heading row's own immediate parent -- one sticky unit, not two
    // competing for the same top edge.
    expect(heading.closest('.sticky')).not.toBeNull();
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

  it('shows a failed save visibly, not only to a screen reader', async () => {
    // THE EXACT BUG THIS PROVES FIXED, from a real live incident: a failed
    // save used to land only in an sr-only live region -- present in the
    // DOM, so a query for the text would already have passed, but never
    // seen by a sighted worker. A worker whose task was reclaimed mid-edit
    // clicked Save repeatedly and saw no error at all, only a Preview
    // button that stayed disabled with no visible reason why.
    vi.spyOn(translation, 'save').mockRejectedValue(new Error('Not found.'));
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Texte poli');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    const failure = await screen.findByText('Not found.');
    expect(failure).not.toHaveClass('sr-only');
    expect(failure.className).toMatch(/status-rejected/);
  });

  it('surfaces a VERIFY AGAINST AUDIO note visibly after saving, since the paste no longer carries it', async () => {
    // The server now strips this out of the saved document itself (see
    // worker_prompts.strip_reply_scaffolding) -- a worker pasting the
    // whole reply never sees this line in their own textarea, so it has
    // to appear here or the note is lost, not just relocated.
    //
    // NO QUOTED PHRASE IN THIS ONE, DELIBERATELY -- an older or malformed
    // reply might still not follow the exact quoted shape the prompt now
    // asks for. The note must still be shown, just without a Find button,
    // rather than the whole item being silently dropped.
    vi.spyOn(translation, 'save').mockResolvedValue({
      saved: true, words: 6, blocking: false, findings: [],
      verify_against_audio: 'the judge\'s exact title in paragraph 4.',
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Texte poli');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    const note = await screen.findByText(/judge's exact title/);
    expect(note).not.toHaveClass('sr-only');
    expect(note.closest('li').className).toMatch(/status-review/);
    expect(screen.queryByRole('button', { name: 'Find' })).not.toBeInTheDocument();
  });

  it('gives a quoted VERIFY AGAINST AUDIO item a Find button that jumps to the matching English row', async () => {
    // THE ACTUAL FEATURE: the French output has no timestamps at all (it
    // is prose, not segments), so "find and jump" has to work against the
    // English source instead -- reusing the same search-and-seek the
    // manual "⌕ Search" button already drives.
    vi.spyOn(translation, 'save').mockResolvedValue({
      saved: true, words: 6, blocking: false, findings: [],
      verify_against_audio: '"nicknamed LGM1" -- unsure if this is the official designation',
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.type(screen.getByLabelText('Polished French'), 'Texte poli');
    await userEvent.click(screen.getByRole('button', { name: 'Save polished version' }));

    await screen.findByText(/unsure if this is the official designation/);
    expect(screen.getByText('“nicknamed LGM1”')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Find' }));

    // Opening the search and setting the query is the whole job -- the
    // search bar's own effect does the seek and the scroll once it is
    // mounted with a query that has a current match.
    expect(screen.getByPlaceholderText('Search the English…')).toHaveValue('nicknamed LGM1');
    expect(screen.getByText('1 of 1')).toBeInTheDocument();
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

describe('retranslating a single line', () => {
  // WHY THIS EXISTS. Editing the English used to leave the worker with only
  // the whole-document, copy-into-your-own-assistant path to catch it back
  // up -- expensive in worker time for a one-word fix. This is the cheap,
  // one-click alternative: a real request, scoped to exactly one segment,
  // through our own AI instead of an external one.

  it('requests exactly one segment, ungated, in the target language', async () => {
    // UNGATED IS LOAD-BEARING. Leaving quality_profile unset would let the
    // server fall back to the production default (the 'volume' profile --
    // three requests instead of one), which is priced for unsupervised
    // warming, not a worker-triggered, worker-reviewed single line.
    vi.spyOn(aiRevisions, 'request').mockResolvedValue({
      id: 'rev-1', status: 'completed',
      segments: [{ effective_text: 'Plusieurs fois par seconde, corrigé' }],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.click(screen.getAllByRole('button', { name: 'Retranslate this line' })[0]);

    await waitFor(() => expect(aiRevisions.request).toHaveBeenCalledWith('job-1', {
      operation: 'translate',
      target_language: 'fr',
      segment_start_index: 0,
      segment_end_index: 1,
      quality_profile: 'none',
    }));
  });

  it('replaces the row with the fresh translation, not a copy sitting beside it', async () => {
    // A SEPARATE BOX READS AS "NOTHING HAPPENED". The row itself must show
    // the corrected text, marked as updated, rather than leaving the stale
    // original in place next to a box the worker has to notice and judge.
    vi.spyOn(aiRevisions, 'request').mockResolvedValue({
      id: 'rev-1', status: 'completed',
      segments: [{ effective_text: 'Plusieurs fois par seconde, corrigé' }],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.click(screen.getAllByRole('button', { name: 'Retranslate this line' })[0]);

    expect(await screen.findByText('Plusieurs fois par seconde, corrigé')).toBeInTheDocument();
    expect(screen.getByText('updated')).toBeInTheDocument();
    expect(screen.queryByText('Plusieurs fois par seconde')).not.toBeInTheDocument();
  });

  it('polls until the background pipeline finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(aiRevisions, 'request').mockResolvedValue({ id: 'rev-1', status: 'processing' });
    vi.spyOn(aiRevisions, 'get').mockResolvedValue({
      id: 'rev-1', status: 'completed',
      segments: [{ effective_text: 'Plusieurs fois par seconde, corrigé' }],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.click(screen.getAllByRole('button', { name: 'Retranslate this line' })[0]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });

    expect(await screen.findByText('Plusieurs fois par seconde, corrigé')).toBeInTheDocument();
    expect(aiRevisions.get).toHaveBeenCalledWith('rev-1');
    vi.useRealTimers();
  });

  it('explains a 409 as another job already running, not a generic failure', async () => {
    vi.spyOn(aiRevisions, 'request').mockRejectedValue(
      new ApiError('conflict', { status: 409 })
    );
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    await userEvent.click(screen.getAllByRole('button', { name: 'Retranslate this line' })[0]);

    expect(await screen.findByText(/already running for this task/)).toBeInTheDocument();
  });

  it('copies just that line, not the whole document', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    vi.spyOn(aiRevisions, 'request').mockResolvedValue({
      id: 'rev-1', status: 'completed',
      segments: [{ effective_text: 'Plusieurs fois par seconde, corrigé' }],
    });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');
    await userEvent.click(screen.getAllByRole('button', { name: 'Retranslate this line' })[0]);
    await screen.findByText('Plusieurs fois par seconde, corrigé');

    await userEvent.click(screen.getByRole('button', { name: 'Copy' }));

    expect(writeText).toHaveBeenCalledWith('Plusieurs fois par seconde, corrigé');
  });

  it('does not offer it when there is no machine draft yet', async () => {
    // A range-scoped fix only makes sense once there is a document with
    // lines in it. The whole-document path (Copy prompt + English) is the
    // right tool for a translation that does not exist yet.
    vi.spyOn(translation, 'get').mockResolvedValue({ ...DRAFT, draft: [], draft_text: '' });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Several times a second');

    expect(screen.queryByRole('button', { name: 'Retranslate this line' })).not.toBeInTheDocument();
  });
});

describe('the full guide', () => {
  it('opens on request and closes again', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');
    expect(screen.queryByRole('dialog', { name: /Translation job guide/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Full guide' }));
    const dialog = await screen.findByRole('dialog', { name: /Translation job guide/i });
    expect(within(dialog).getByRole('heading', {
      name: /What the checks still miss/i, level: 3,
    })).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: /Translation job guide/i })).not.toBeInTheDocument();
  });
});

describe('the self-check is required before preview', () => {
  // See run_quality_gate's 'self-check-not-completed' finding: the same
  // requirement the server now enforces is surfaced earlier here, as a
  // disabled button rather than a dialog the worker has to open just to
  // be told why they cannot submit.

  it('disables Preview submission until a polished version is saved', async () => {
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    expect(screen.getByRole('button', { name: 'Preview submission' })).toBeDisabled();
  });

  it('enables it once the paste-back has been saved', async () => {
    vi.spyOn(translation, 'get').mockResolvedValue(
      { ...DRAFT, polished_at: '2026-01-01T00:00:00Z', polished_by: 'W-01' });
    render(<TranslationWorkspace task={TASK} />);
    await screen.findByText('Plusieurs fois par seconde');

    expect(screen.getByRole('button', { name: 'Preview submission' })).not.toBeDisabled();
  });

  it('leaves the view-only button enabled on delivered work regardless', async () => {
    render(<TranslationWorkspace
      task={{ ...TASK, status: 'submitted', submitted_at: '2026-01-01T00:00:00Z' }} />);
    await screen.findByText('Plusieurs fois par seconde');

    expect(screen.getByRole('button', { name: 'View what was submitted' }))
      .not.toBeDisabled();
  });
});

describe('submitting', () => {
  it('blocks Submit when the preview was refused outright', async () => {
    // Not what this test is about -- the Preview button itself is now also
    // disabled without a saved self-check (see the describe block below),
    // and this test wants the dialog open regardless to check what happens
    // once Eagle's own preview is refused.
    vi.spyOn(translation, 'get').mockResolvedValue(
      { ...DRAFT, polished_at: '2026-01-01T00:00:00Z' });
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
    // See the previous test for why this is needed to reach the dialog.
    vi.spyOn(translation, 'get').mockResolvedValue(
      { ...DRAFT, polished_at: '2026-01-01T00:00:00Z' });
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
