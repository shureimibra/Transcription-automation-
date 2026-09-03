import React, { useCallback, useMemo, useRef, useState } from 'react';
import AudioPlayer from './AudioPlayer.jsx';
import DeadlineClock from './DeadlineClock.jsx';
import { segments as segmentsApi, submission, taskMedia, transcript } from '../api/eagle.js';
import Verdict from '../components/Verdict.jsx';
import DeliveredBanner, { isDelivered } from '../components/DeliveredBanner.jsx';
import SubmittedPanel from '../components/SubmittedPanel.jsx';
import { useAsync } from '../hooks/useAsync.js';
import {
  Button, Card, ErrorState, FOCUS, LiveRegion, Skeleton, inputClass,
} from '../components/ui/index.jsx';

/**
 * Where a tasker actually works.
 *
 * NOT THE STUDIO. The studio exists because someone is sourcing their own
 * work: paste a URL, tick diarization, press transcribe, wait. Here the
 * system sourced it and the machine already ran, so the screen is built for
 * one thing — comparing a finished transcript against the audio and fixing
 * what is wrong.
 *
 * SPEAKER LABELS ARE THE JOB. On an Eagle transcription task the graded work
 * is speaker identification, so the two edits that matter are given the least
 * friction: rename a speaker everywhere (a chip), and reassign one turn that
 * diarization misattributed (a dropdown on the line). Words are editable too,
 * but they are rarely the reason a task is rejected.
 *
 * WHAT WILL BE SENT COMES FROM THE SERVER. The preview is fetched, never
 * rendered here. A second formatter in the browser could disagree with the
 * one that actually submits, and the disagreement would surface as a
 * rejection an hour later.
 */

/**
 * Which verbatim standard this transcript follows.
 *
 * WHY THIS IS ON SCREEN AT ALL. The two standards are opposites. Edited
 * verbatim strips fillers and expands contractions; strict verbatim keeps
 * both exactly as spoken. Apply the wrong one and the result is not a
 * slightly worse transcript, it is the wrong document -- wrong in every
 * line, and no amount of care in the words rescues it.
 *
 * Three transcripts the platform accepted, from one transcriber, prove it
 * varies per task: two edited, one strict. The platform decides via the
 * project on the task.
 *
 * AND WHY THE "ASSUMED" STATE IS LOUDER THAN THE KNOWN ONE. We do not yet
 * receive the project label, so the standard shown is usually a default --
 * correct for most work, because most of it is lectures, and silently wrong
 * for the rest. A guess presented as a fact is the dangerous version. This
 * says which it is, in the one place someone is already reading the text
 * closely, so a person can catch what the machine cannot.
 */
const STANDARD_RULES = {
  edited_verbatim: 'Remove fillers and false starts. Expand contractions — "do not", not "don\'t".',
  strict_verbatim: 'Keep every filler, false start, repetition and contraction exactly as spoken.',
};

function StandardBanner({ task }) {
  const standard = task?.transcription_standard;
  if (!standard) return null;

  const rule = STANDARD_RULES[standard.name];
  const assumed = Boolean(standard.assumed);

  return (
    <div
      role={assumed ? 'alert' : undefined}
      className={`rounded-xl2 border px-4 py-3 ${
        assumed
          ? 'border-status-review/40 bg-status-review/10'
          : 'border-edge bg-surface'
      }`}
    >
      <p className={`text-xs font-semibold ${assumed ? 'text-status-review' : 'text-ink'}`}>
        {assumed ? `Assumed: ${standard.label}` : standard.label}
        {task.project ? ` · ${task.project}` : ''}
      </p>
      {rule && <p className="mt-1 text-2xs text-ink-dim">{rule}</p>}
      {assumed && (
        <p className="mt-1 text-2xs text-ink-dim">
          The platform did not tell us which standard this task uses, so this is
          the usual one for lectures. If the audio is a legal dictation, a
          hearing or a law class, it should be strict verbatim instead — check
          before you submit.
        </p>
      )}
    </div>
  );
}

function timestamp(seconds) {
  if (!Number.isFinite(seconds)) return '0:00';
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export default function ReviewWorkspace({ task, onTaskChange }) {
  const playerRef = useRef(null);
  const [playhead, setPlayhead] = useState(0);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showPreview, setShowPreview] = useState(false);

  const jobId = task?.job;
  const load = useCallback(
    () => (jobId ? transcript.get(jobId) : Promise.resolve(null)),
    [jobId],
  );
  const { data: job, error: loadError, loading, refresh, setData } = useAsync(load, [jobId]);

  const rows = useMemo(
    () => [...(job?.segments || [])].sort((a, b) => a.start_time - b.start_time),
    [job?.segments],
  );

  /**
   * A speaker's name as it will actually be submitted.
   *
   * RENAMING NEVER TOUCHES THE SEGMENTS. It writes a job-level alias map,
   * which every export applies at render time -- that is what makes a rename
   * instant everywhere and trivially reversible. The catch is that THIS
   * screen was not applying it: a worker renamed Female_1 to Male_1, the map
   * was written, the export would have honoured it, and the transcript in
   * front of them did not change by a single character. Saving something
   * and showing no evidence of it is indistinguishable from being broken,
   * and the natural next move is to do it again.
   *
   * The RAW label stays the key for every write. Renaming and reassigning
   * both address the underlying value, so passing a display name back would
   * alias the alias.
   */
  const aliases = job?.speaker_aliases || {};
  const displayName = useCallback(
    (label) => (label ? (aliases[label] || label) : ''),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(aliases)],
  );

  // SPEAKERS ARE A TRANSCRIPTION CONCERN ONLY.
  //
  // Eagle's translation deliverable is prose: no speaker labels, no
  // timestamps -- verified against a submission the platform accepted. So
  // warming does not run diarization on a translation task, every label is
  // blank, and showing the speaker controls there offers a tasker work that
  // is thrown away at render time. Worse, a screen full of empty dropdowns
  // reads as broken rather than as deliberate.
  const labelsSpeakers = task.kind !== 'translation';

  // DELIVERED WORK IS A RECORD, NOT A DRAFT.
  //
  // Once the platform has the document, editing a segment changes nothing
  // about what was sent -- it quietly edits our copy so the two disagree,
  // and the next person to open the task reads something Eagle never saw.
  // The transcript stays fully readable, because "what did I send?" is a
  // question worth answering; it just stops pretending to be editable.
  const readOnly = isDelivered(task);

  // Speakers the diarizer flagged as decided on weak evidence.
  const uncertainSpeakers = useMemo(
    () => job?.document_metadata?.uncertain_speakers || [],
    [job?.document_metadata]
  );

  /**
   * Male_2 <-> Female_2, everywhere at once.
   *
   * THE NUMBER IS KEPT. It identifies WHICH person; only the gender was
   * ever in doubt. Renaming Male_2 to Female_1 would silently merge two
   * speakers, so the flip changes exactly the thing that is wrong.
   */
  const flipGender = async (label) => {
    // FLIP WHAT IS SHOWN, KEY ON WHAT IS STORED. An alias already in force
    // is the current answer, so flipping has to start from it -- deriving
    // the new value from the raw label instead recomputed the alias that
    // was already set and the button did nothing at all.
    const current = displayName(label) || label;
    const flipped = current.startsWith('Male_')
      ? current.replace('Male_', 'Female_')
      : current.replace('Female_', 'Male_');
    if (flipped !== current) await renameSpeakerEverywhere(label, flipped);
  };

  /** Distinct speakers, with how much each one talks — the review overview. */
  const speakers = useMemo(() => {
    const tally = new Map();
    rows.forEach((row) => {
      const label = row.speaker_label || 'Unlabelled';
      const entry = tally.get(label)
        || { label, display: displayName(label) || label, turns: 0, seconds: 0 };
      entry.turns += 1;
      entry.seconds += Math.max(0, (row.end_time || 0) - (row.start_time || 0));
      tally.set(label, entry);
    });
    return [...tally.values()].sort((a, b) => b.seconds - a.seconds);
  }, [rows, displayName]);

  const patchSegment = async (segment, changes) => {
    setSaving(segment.id);
    setError('');
    try {
      const updated = await segmentsApi.update(jobId, segment.id, changes);
      setData((current) => (current ? {
        ...current,
        segments: current.segments.map((s) => (s.id === segment.id ? { ...s, ...updated } : s)),
      } : current));
      setNotice('Saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(null);
      setEditing(null);
    }
  };

  const renameSpeakerEverywhere = async (from, to) => {
    const label = (to || '').trim();
    if (!label) return;

    // RENAMING BACK TO THE ORIGINAL MEANS "UNDO", NOT "DO NOTHING".
    //
    // `from` is the RAW label on the segments; `label` is what the worker
    // wants shown. When they are the same, the worker is asking to remove an
    // alias they applied earlier -- which is exactly what happens after an
    // accidental click: Male_1 becomes Female_1, and every attempt to put it
    // back hits `label === from` and returns silently. The chip goes on
    // saying Female_1 and nothing explains why.
    //
    // The endpoint already understands this: mapping a label to an empty
    // string reverts that one speaker to its raw label. We just never asked.
    if (label === from) {
      setError('');
      try {
        await transcript.setSpeakerAliases(jobId, { [from]: '' });
        await refresh({ silent: true });
        setNotice(`${from} is back to its original label.`);
      } catch (err) {
        setError(err.message);
      }
      return;
    }
    setError('');
    try {
      // One call renames every occurrence — the right tool when diarization
      // got the identity right and only the NAME is wrong.
      await transcript.setSpeakerAliases(jobId, { [from]: label });
      await refresh({ silent: true });
      setNotice(`${from} is now ${label} throughout.`);
    } catch (err) {
      setError(err.message);
    }
  };

  if (!jobId) {
    return (
      <Card title="Transcript">
        <p className="text-xs text-ink-mute">
          This task has no transcript yet. It will appear once pre-processing finishes.
        </p>
      </Card>
    );
  }

  if (loading && !job) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-16 rounded-xl2" />
        <Skeleton className="h-64 rounded-xl2" />
      </div>
    );
  }

  if (loadError && !job) {
    return <ErrorState message={loadError.message} onRetry={() => refresh()} />;
  }

  return (
    <div className="space-y-4">
      <DeadlineClock task={task} onExpiring={() => setNotice('Deadline approaching.')} />

      <AudioPlayer
        ref={playerRef}
        src={taskMedia.url(task.id)}
        segments={rows}
        onTimeUpdate={setPlayhead}
      />

      {error && <ErrorState message={error} onRetry={() => setError('')} compact />}
      <LiveRegion message={notice} />

      <DeliveredBanner task={task} />
      <StandardBanner task={task} />

      {/* THE MACHINE'S DOUBT, SAID OUT LOUD. Male and female F0 ranges
          overlap, so a speaker near the boundary is a coin flip. Measured
          on a real lecture: a man read 178 Hz against a 165 Hz boundary and
          went out as Female_1 on all 432 lines, with the doubt recorded
          only in a log nobody reads. Five seconds of listening settles it,
          but only if someone is told there is something to settle. */}
      {labelsSpeakers && uncertainSpeakers.length > 0 && (
        <div role="alert"
             className="rounded-xl2 border border-status-review/40 bg-status-review/10 px-4 py-3">
          <p className="text-xs font-medium text-status-review">
            Check {uncertainSpeakers.length === 1 ? 'this speaker' : 'these speakers'} before you start
          </p>
          <p className="mt-1 text-2xs text-ink-dim">
            The machine could not tell male from female with confidence for{' '}
            {uncertainSpeakers.map((u) => u.label).join(', ')}. Play a few
            seconds and use the toggle below — it applies to every line at
            once, so fixing it now costs nothing and fixing it later costs
            the whole transcript.
          </p>
        </div>
      )}

      {labelsSpeakers && (
        <Card
          title="Speakers"
          subtitle="Rename one everywhere, or fix a single turn below"
        >
          <div className="flex flex-wrap gap-2">
            {speakers.map((speaker) => (
              <SpeakerChip
                key={speaker.label}
                speaker={speaker}
                uncertain={uncertainSpeakers.some((u) => u.label === speaker.label)}
                // Delivered work keeps the chip -- who spoke is worth
                // reading back -- but drops the controls that would edit it.
                onFlip={readOnly ? null : () => flipGender(speaker.label)}
                onRename={readOnly ? null : (next) => renameSpeakerEverywhere(speaker.label, next)}
              />
            ))}
            {speakers.length === 0 && (
              <p className="text-xs text-ink-mute">No speaker labels on this transcript.</p>
            )}
          </div>
        </Card>
      )}

      <Card
        title="Transcript"
        subtitle={`${rows.length} turns · click any line to hear it`}
        actions={
          <Button size="sm" onClick={() => setShowPreview(true)}>
            {/* Delivered work can still be READ -- a coach asks what was sent
                often enough -- but it is never offered a submit path. */}
            {isDelivered(task) ? 'View what was submitted' : 'Preview submission'}
          </Button>
        }
        padded={false}
      >
        <ol className="divide-y divide-edge/60">
          {rows.map((row) => {
            const active = playhead >= row.start_time && playhead < row.end_time;
            return (
              <li
                key={row.id}
                className={`px-4 py-3 transition-colors ${active ? 'bg-accent-soft' : ''}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  {labelsSpeakers && (
                    <select
                      aria-label={`Speaker for the turn at ${timestamp(row.start_time)}`}
                      value={row.speaker_label || ''}
                      disabled={readOnly || saving === row.id}
                      onChange={(e) => patchSegment(row, { speaker_label: e.target.value })}
                      className="rounded border border-edge bg-canvas px-1.5 py-0.5 text-2xs text-ink"
                    >
                      {row.speaker_label && !speakers.some((s) => s.label === row.speaker_label) && (
                        <option value={row.speaker_label}>{displayName(row.speaker_label)}</option>
                      )}
                      {speakers.map((s) => (
                        <option key={s.label} value={s.label}>{s.display || s.label}</option>
                      ))}
                    </select>
                  )}

                  <button
                    onClick={() => playerRef.current?.seekTo(row.start_time)}
                    className={`rounded font-mono text-2xs tabular-nums text-accent hover:text-accent-strong ${FOCUS}`}
                    title="Jump to this moment"
                  >
                    {timestamp(row.start_time)} – {timestamp(row.end_time)}
                  </button>
                </div>

                {editing === row.id ? (
                  <div className="mt-2 space-y-2">
                    <textarea
                      autoFocus
                      rows={3}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setEditing(null);
                        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                          patchSegment(row, { text: draft });
                        }
                      }}
                      className={`${inputClass} resize-y font-normal`}
                    />
                    <div className="flex gap-2">
                      <Button size="sm" variant="primary" loading={saving === row.id}
                              onClick={() => patchSegment(row, { text: draft })}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                      <span className="self-center text-2xs text-ink-mute">Ctrl+Enter saves</span>
                    </div>
                  </div>
                ) : (
                  <p
                    role={readOnly ? undefined : 'button'}
                    tabIndex={readOnly ? undefined : 0}
                    onClick={readOnly ? undefined
                      : () => { setEditing(row.id); setDraft(row.text || ''); }}
                    onKeyDown={readOnly ? undefined : (e) => {
                      if (e.key === 'Enter') { setEditing(row.id); setDraft(row.text || ''); }
                    }}
                    className={`mt-1 rounded text-sm leading-relaxed text-ink
                      ${readOnly ? '' : `cursor-text ${FOCUS}`}`}
                  >
                    {/* Follow the audio sentence by sentence while it plays.
                        Only the segment being spoken is broken up -- doing it
                        to all 400 would cost a lot of DOM for nothing. */}
                    {active
                      ? sentencesWithActive(
                          row.text, row.start_time, row.end_time, playhead,
                        ).map((part, i) => (
                          <span
                            key={i}
                            className={part.active
                              ? 'rounded bg-accent/25 box-decoration-clone px-0.5'
                              : undefined}
                          >
                            {part.text}{' '}
                          </span>
                        ))
                      : row.text}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      </Card>

      {showPreview && (
        <PreviewDialog
          task={task}
          onClose={() => setShowPreview(false)}
          onSubmitted={(updated) => {
            // NOT setShowPreview(false). The dialog stays open to show the
            // confirmation; closing it here is what made a successful
            // submission indistinguishable from a dismissed one.
            onTaskChange?.(updated);
          }}
        />
      )}
    </div>
  );
}

/**
 * A segment's text as sentences, with the one currently being spoken.
 *
 * WHY SENTENCES AND NOT WORDS. Word-level timing would need the ASR to
 * return per-word offsets, which the pipeline does not store. Sentences can
 * be placed from what we already have: a segment knows when it starts and
 * ends, and a sentence's share of the segment's characters is a good
 * estimate of its share of the segment's time. Speech rate is near enough
 * to constant inside one breath group for this to track.
 *
 * WHY IT MATTERS. Following along by ear while reading is how proofreading
 * actually gets done -- it is what the commercial tools give you, and
 * without it a reviewer has to keep finding their place after every pause.
 * A whole highlighted segment does not help when the segment is four
 * sentences long.
 *
 * Falls back to the whole segment when there is only one sentence, which is
 * most of them.
 */
function sentencesWithActive(text, start, end, playhead) {
  // Split AFTER terminal punctuation, keeping it attached. The lookbehind
  // avoids breaking on "Rule 12(b)(6)." mid-citation by requiring the
  // following character to be a space and a capital or a quote.
  const parts = String(text || '')
    .split(/(?<=[.!?])\s+(?=[A-ZÀ-Þ"«'])/)
    .filter((p) => p.length);

  if (parts.length <= 1) return [{ text: text || '', active: true }];

  const total = parts.reduce((sum, p) => sum + p.length, 0) || 1;
  const span = Math.max(end - start, 0.001);
  const elapsed = Math.min(Math.max(playhead - start, 0), span);
  const target = (elapsed / span) * total;

  let seen = 0;
  return parts.map((part) => {
    const from = seen;
    seen += part.length;
    return { text: part, active: target >= from && target < seen };
  });
}

/**
 * One speaker: renameable everywhere, and flippable in one click.
 *
 * THE FLIP EXISTS BECAUSE GENDER IS THE ONLY PART THE MACHINE GUESSES.
 * Clustering decides how many people there are and which lines are whose;
 * that part is measured and reliable. Male-or-female comes from pitch
 * against a fixed boundary, and adult ranges genuinely overlap -- a
 * lecturer measured at 178 Hz against a 165 Hz boundary went out as
 * Female_1 on all 432 lines.
 *
 * A worker hears the answer in five seconds. Making them type "Male_1" into
 * a rename box to say so is friction on the one correction they will make
 * most often.
 */
function SpeakerChip({ speaker, uncertain = false, onFlip, onRename }) {
  const [editing, setEditing] = useState(false);
  // Seeded with the CURRENT display name: reopening a rename should show
  // what the speaker is called now, not what it was called originally.
  const [value, setValue] = useState(speaker.display || speaker.label);

  const minutes = Math.round(speaker.seconds / 60);

  if (editing) {
    return (
      <form
        className="flex items-center gap-1"
        onSubmit={(e) => { e.preventDefault(); onRename(value); setEditing(false); }}
      >
        <input
          autoFocus
          // Labelled because a bare text box announces nothing to a screen
          // reader, and "rename" is ambiguous when several speakers are on
          // screen at once.
          aria-label={`New name for ${speaker.display || speaker.label}`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); }}
          className="w-28 rounded border border-edge bg-canvas px-2 py-1 text-2xs text-ink"
        />
        <Button size="sm" variant="primary" type="submit">Rename</Button>
      </form>
    );
  }

  // The EFFECTIVE name decides the direction. Computing this from the raw
  // label meant that flipping an already-renamed speaker recomputed the
  // same alias and changed nothing on screen -- a dead button.
  const shown = speaker.display || speaker.label;
  const isMale = shown.startsWith('Male_');
  const other = isMale ? 'Female' : 'Male';

  return (
    <div
      className={`flex items-center gap-1 rounded-lg border bg-canvas px-1 py-0.5 ${
        uncertain ? 'border-status-review/50' : 'border-edge'
      }`}
    >
      {/* No `onRename` means the work is delivered: the chip becomes a
          label. It still says who spoke, because reading that back is
          useful; it just no longer offers to change a document that has
          already gone. */}
      <button
        onClick={onRename ? () => setEditing(true) : undefined}
        disabled={!onRename}
        className={`rounded px-1.5 py-1 text-left ${FOCUS}
          ${onRename ? 'hover:bg-overlay/60' : 'cursor-default'}`}
        title={onRename ? 'Rename this speaker everywhere' : undefined}
      >
        <span className="block text-xs font-medium text-ink">
          {shown}
          {uncertain && (
            <span
              className="ml-1 text-status-review"
              title="The machine was not confident about male or female here"
              aria-label="gender not confirmed"
            >
              ?
            </span>
          )}
        </span>
        <span className="block text-2xs text-ink-mute">
          {speaker.turns} turns · {minutes} min
        </span>
      </button>
      {onFlip && (
        <button
          onClick={onFlip}
          className={`shrink-0 rounded px-1.5 py-1 text-2xs font-medium
                      text-accent hover:bg-overlay/60 hover:text-accent-strong ${FOCUS}`}
          title={`Change to ${other} everywhere — the number stays, so this
                  does not merge two speakers`}
        >
          → {other}
        </button>
      )}
    </div>
  );
}

/**
 * The last thing between a worker and a rejection.
 *
 * Shows the exact document the backend will send. Given that a rejection
 * costs the platform minutes and the worker's hour, five seconds of a human
 * looking at the real thing is the cheapest check available.
 */
function PreviewDialog({ task, onClose, onSubmitted }) {
  const load = useCallback(() => submission.preview(task.id), [task.id]);
  const { data, error, loading } = useAsync(load, [task.id]);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState('');
  const [sent, setSent] = useState(null);

  const send = async () => {
    setSubmitting(true);
    setFailure('');
    try {
      const updated = await submission.submit(task.id);
      setSent(updated || true);
      onSubmitted(updated);
    } catch (err) {
      setFailure(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  // NO PREVIEW MEANS NO SUBMIT.
  //
  // `blocking` only exists on a preview the server managed to RENDER. When
  // it refuses outright -- a translation task with nothing translated, a
  // task not yet in a submittable state -- the response is an error with no
  // `blocking` field at all, and reading it as false left the Submit button
  // blue and inviting underneath the very message explaining why the work
  // cannot be sent. The server refuses again, so nothing broken reaches the
  // platform, but the button was lying about what would happen.
  //
  // An error is therefore blocking in its own right: if we could not even
  // produce the document, there is nothing to send.
  // ALREADY DELIVERED MEANS NO SUBMIT BUTTON AT ALL.
  //
  // The server refuses a second submission, so nothing breaks -- but a live
  // button on a task that has already gone invites the click and answers it
  // with a red error for doing the reasonable thing. Read-only is the honest
  // shape for work that is out of your hands.
  const delivered = isDelivered(task);
  const blocking = delivered || Boolean(error) || Boolean(data?.blocking);

  // DELIVERED. The dialog stays, and says so.
  if (sent) {
    return (
      <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
        <div
          role="dialog" aria-modal="true" aria-label="Submitted"
          className="w-full max-w-lg rounded-xl2 border border-edge bg-surface shadow-panel"
        >
          <SubmittedPanel task={task} onClose={onClose} />
        </div>
      </div>
    );
  }


  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog" aria-modal="true" aria-labelledby="preview-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <header className="border-b border-edge px-5 py-3">
          <h2 id="preview-title" className="text-sm font-semibold text-ink">
            What will be submitted
          </h2>
          <p className="mt-0.5 text-xs text-ink-mute">
            Rendered by the server — this is exactly what the platform receives.
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading && <Skeleton className="h-64" />}
          {error && <ErrorState message={error.message} />}
          {failure && <ErrorState message={failure} />}

          {data && <Verdict findings={data.findings || []} blocking={Boolean(data.blocking)} />}

          {data && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-canvas p-3 font-mono text-2xs leading-relaxed text-ink-dim">
              {data.content}
            </pre>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-edge px-5 py-3">
          <span className="text-2xs text-ink-mute">
            {data ? `${data.words} words · ${data.characters} characters` : ''}
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={submitting}>Back</Button>
            <Button
              variant="primary"
              loading={submitting}
              disabled={blocking || loading}
              onClick={send}
              title={
                delivered
                  ? 'This work has already been submitted'
                  : error
                    ? 'There is nothing to submit until this is resolved'
                    : blocking
                      ? 'Fix the critical findings first'
                      : undefined
              }
            >
              Submit to platform
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
