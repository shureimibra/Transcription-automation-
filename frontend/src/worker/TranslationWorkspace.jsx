import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  aiRevisions, segments as segmentsApi, submission, taskMedia, translation,
} from '../api/eagle.js';
import { useAsync } from '../hooks/useAsync.js';
import AudioPlayer from './AudioPlayer.jsx';
import Verdict from '../components/Verdict.jsx';
import DeliveredBanner, { isDelivered } from '../components/DeliveredBanner.jsx';
import SubmittedPanel from '../components/SubmittedPanel.jsx';
import DeadlineClock from './DeadlineClock.jsx';
import {
  Button, Card, ErrorState, FOCUS, LiveRegion, Skeleton,
} from '../components/ui/index.jsx';

/**
 * Reviewing a translation.
 *
 * WHY THIS IS NOT ReviewWorkspace. That screen shows `job.segments`, which
 * on a translation task are the ENGLISH source -- while the submitter
 * renders the FRENCH. A worker there looked at one document, edited it, and
 * submitted another; the edits went nowhere and nothing said so.
 *
 * THE WORKER IS NOT EXPECTED TO READ FRENCH. So the screen is built around
 * the three things they can actually do: hear the audio, run the polish
 * step, and act on findings that are mechanical rather than linguistic --
 * a number that changed, a reference that appeared, a word from the wrong
 * language.
 *
 * THE SOURCE IS SHOWN, READ-ONLY. Not to be edited: on a translation task
 * the English is an input, and correcting it changes nothing about what
 * gets submitted unless the whole thing is translated again. Showing it
 * editable would repeat the bug this screen exists to fix.
 */
export default function TranslationWorkspace({ task, onTaskChange }) {
  const load = useCallback(() => translation.get(task.id), [task.id]);
  const { data, error, loading, refresh } = useAsync(load, [task.id]);

  const [pasted, setPasted] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [notice, setNotice] = useState('');
  const [copied, setCopied] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  // FOLLOWING THE AUDIO WAS THE MISSING HALF. This screen had a player and a
  // transcript and no connection between them: no highlight, no click to
  // jump. Checking whether line 90 matched the audio meant scrubbing by eye.
  const playerRef = useRef(null);
  const activeRow = useRef(null);
  const listBox = useRef(null);
  //: Whether the transcript follows the audio. Off while the reader has
  //: scrolled away, on again once the spoken line is back in view.
  const [following, setFollowing] = useState(true);
  const [playhead, setPlayhead] = useState(0);
  const [editing, setEditing] = useState(null);   // segment id being edited
  const [draftText, setDraftText] = useState('');
  // Keyed by row.key. `retranslating` is the one in flight (there can only
  // ever be one -- the server refuses a second AI revision on the same job
  // while one is running), `retranslated` holds finished results so they
  // survive switching to a different row, `retranslateError` is per-row so
  // one failure does not blank out an unrelated result sitting above it.
  const [retranslating, setRetranslating] = useState(null);
  const [retranslated, setRetranslated] = useState({});
  const [retranslateError, setRetranslateError] = useState({});

  const rows = useMemo(() => {
    const source = data?.source || [];
    const draft = data?.draft || [];
    // Same transcript, so segment N of one is segment N of the other. The
    // pairing is structural, not a guess.
    return source.map((s, i) => ({
      key: i,
      id: s.id,
      start: s.start_time,
      end: s.end_time,
      english: s.text,
      french: draft[i]?.text || '',
    }));
  }, [data]);

  // WHICH LINE THE AUDIO IS ON. Derived rather than stored, so it cannot
  // drift out of step with the player.
  const activeKey = useMemo(() => {
    const row = rows.find((r) => playhead >= r.start && playhead < (r.end ?? r.start));
    return row ? row.key : null;
  }, [rows, playhead]);

  // KEEP IT IN VIEW, AND ONLY WHEN IT CHANGES. Scrolling on every time
  // update -- four times a second -- would fight the reader for control of
  // the page. Once per turn is following along; more than that is a
  // wrestling match.
  //
  // AND THE READER WINS WHENEVER THEY REACH FOR IT. Following was
  // unconditional, so scrolling back to read an earlier line -- or to reach
  // the Pause button -- was undone by the next segment a second later.
  // There was no way to stop it short of stopping the audio, which is
  // exactly what somebody scrolling is usually trying to do.
  //
  // Scrolling away turns following OFF. It comes back on when the line
  // being spoken is on screen again, so a reader who scrolls back to the
  // playhead resumes without having to find a control for it.
  useEffect(() => {
    if (activeKey === null || !following) return;
    activeRow.current?.scrollIntoView?.({ block: 'nearest' });
  }, [activeKey, following]);

  const onListScroll = () => {
    const box = listBox.current;
    const row = activeRow.current;
    if (!box || !row) return;
    // Programmatic scrolls fire this too, so "did the user move away" is
    // answered by geometry rather than by trying to tell the events apart.
    const boxTop = box.getBoundingClientRect().top;
    const rowTop = row.getBoundingClientRect().top;
    const offset = rowTop - boxTop;
    setFollowing(offset >= -8 && offset <= box.clientHeight);
  };

  const body = data?.polished_text || data?.draft_text || '';

  // NOT TRANSLATED YET IS A STATE, NOT AN EMPTY TABLE.
  //
  // A translation task whose translate pass has not finished has a full
  // English transcript and no French at all. Rendering that as a column of
  // em-dashes beside 275 English lines reads as a broken screen -- there is
  // nothing to tell the worker whether the machine failed, is still
  // working, or produced an empty document. Say which.
  const notTranslatedYet = !loading && (data?.draft || []).length === 0;

  // THE PROMPT AND THE TEXT TRAVEL TOGETHER.
  //
  // This screen used to say "paste it under the polish prompt" and hand
  // over the text alone, so every worker supplied their own prompt to their
  // own assistant -- one standard per worker, and nobody found out whose
  // was wrong until the reviews came back a day later. One click now yields
  // the whole thing, ready to paste, with the rules the submission gate
  // actually enforces stated in it.
  const copyFor = async (kind) => {
    const prompt = data?.prompts?.[kind] || '';
    const text = kind === 'translate'
      ? (data?.source || []).map((row) => row.text).filter(Boolean).join('\n\n')
      : body;
    // `review` already carries the English inside it, so it needs only the
    // French appended -- pasting two blocks in the right order by hand is a
    // step that goes wrong.
    const payload = prompt ? `${prompt}${text}` : text;
    if (!payload.trim()) {
      setNotice('There is nothing to copy yet.');
      return;
    }
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(kind);
      setNotice(kind === 'review'
        ? 'Prompt and both versions copied. Your assistant will reply CLEAN if it finds nothing.'
        : 'Prompt and text copied. Paste it straight into your assistant.');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard can be refused. The text is on screen and selectable, so
      // there is nothing to apologise for -- just do not claim success.
      setNotice('Could not copy automatically — select the text and copy it.');
    }
  };

  /**
   * Correct a line of the ENGLISH source.
   *
   * WHY THIS IS ALLOWED NOW, HAVING BEEN FORBIDDEN. The rule was that the
   * English is an input and editing it changes nothing about what is
   * submitted, which is true and was the wrong conclusion. When the machine
   * mis-hears a name or a number, the French inherits the mistake and says
   * something false about the world -- and polishing the French cannot
   * repair it, because the assistant doing the polishing only ever sees the
   * French and has no way to know it is wrong.
   *
   * The repair has to happen upstream. So the line is editable, and the
   * notice below says the part a worker would otherwise get wrong: the
   * French does NOT follow automatically.
   */
  const saveEnglish = async (row) => {
    const text = draftText.trim();
    if (!row.id || !text || text === row.english) { setEditing(null); return; }
    try {
      await segmentsApi.update(task.job, row.id, { text });
      await refresh({ silent: true });
      setNotice(
        'English updated. The French has NOT changed — translate the corrected '
        + 'English again in your assistant and paste it below.'
      );
    } catch (err) {
      setNotice(`Could not save that line: ${err.message}`);
    } finally {
      setEditing(null);
    }
  };

  /**
   * Retranslate ONE line through our own AI, instead of the whole document
   * through the worker's own assistant.
   *
   * SCOPED TO EXACTLY ONE SEGMENT, ON PURPOSE. `ai_revisions` accepts a
   * range; this always passes a range of one. A wider range costs more per
   * click for no benefit here -- the worker already knows which line is
   * wrong, this is not a "revalidate the whole document" action.
   *
   * quality_profile: 'none' IS LOAD-BEARING, NOT A DEFAULT LEFT OFF. Leaving
   * it unset lets the server fall back to AI_QUALITY_DEFAULT_PROFILE, which
   * in production resolves 'translate' to the 'volume' profile -- three
   * requests instead of one, meant for unsupervised warming. This action is
   * the opposite: a worker triggers it and reads the result before doing
   * anything with it, so paying for an audit pass nobody needed defeats the
   * reason this was built as a single-request action in the first place.
   *
   * DOES NOT TOUCH THE POLISH TEXTAREA. `body` is free prose the worker may
   * already have reworded and merged; splicing a fresh sentence into it
   * programmatically would be a guess about where it belongs. The result is
   * shown for the worker to read and copy in by hand -- the same "worker
   * decides" shape as everything else on this screen.
   */
  const retranslateLine = async (row) => {
    if (retranslating) return;
    setRetranslating(row.key);
    setRetranslateError((prev) => ({ ...prev, [row.key]: '' }));
    try {
      let revision = await aiRevisions.request(task.job, {
        operation: 'translate',
        target_language: data?.target_language || '',
        segment_start_index: row.key,
        segment_end_index: row.key + 1,
        quality_profile: 'none',
      });

      // Small ranges usually finish inside the request, but the pipeline
      // always dispatches to a background thread -- so 'queued'/'processing'
      // is a real, expected outcome here, not just the bulk-document case.
      let attempts = 0;
      while (['queued', 'processing'].includes(revision.status) && attempts < 40) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        revision = await aiRevisions.get(revision.id);
        attempts += 1;
      }

      if (revision.status === 'completed') {
        const text = revision.segments?.[0]?.effective_text || '';
        setRetranslated((prev) => ({ ...prev, [row.key]: text }));
      } else if (['queued', 'processing'].includes(revision.status)) {
        setRetranslateError((prev) => ({
          ...prev,
          [row.key]: 'Still working after a minute — check back in a moment.',
        }));
      } else {
        setRetranslateError((prev) => ({
          ...prev,
          [row.key]: revision.error_message || `Did not finish (${revision.status}).`,
        }));
      }
    } catch (err) {
      setRetranslateError((prev) => ({
        ...prev,
        [row.key]: err.isConflict
          ? 'An AI job is already running for this task — wait for it to finish and try again.'
          : err.message,
      }));
    } finally {
      setRetranslating(null);
    }
  };

  const copyRetranslated = async (row) => {
    try {
      await navigator.clipboard.writeText(retranslated[row.key] || '');
      setNotice('Copied. Paste it into the polished French below where it belongs.');
    } catch {
      setNotice('Could not copy automatically — select the text and copy it.');
    }
  };

  const save = async () => {
    setSaving(true);
    setNotice('');
    try {
      const outcome = await translation.save(task.id, pasted);
      setResult(outcome);
      setNotice(`Saved ${outcome.words} words. ${outcome.findings.length} findings.`);
      refresh({ silent: true });
    } catch (err) {
      setNotice(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading && !data) {
    return <Skeleton className="h-64 rounded-xl2" />;
  }

  return (
    <div className="space-y-4">
      <DeadlineClock task={task} />
      <AudioPlayer
        ref={playerRef}
        src={taskMedia.url(task.id)}
        segments={data?.source || []}
        onTimeUpdate={setPlayhead}
      />
      <DeliveredBanner task={task} />
      <LiveRegion message={notice} />
      {error && <ErrorState message={error.message} onRetry={() => refresh()} />}

      {/* THE BOX IS ALWAYS HERE.
          It used to be hidden whenever the machine draft was missing, which
          is exactly backwards: no draft is when a worker most needs
          somewhere to put a translation they made themselves. On the first
          real submission the box was absent at the only moment it mattered,
          and the text had to go in through the API instead. */}
      <Card
        title={notTranslatedYet ? 'Translate this task' : 'Polish the translation'}
        subtitle={
          notTranslatedYet
            ? 'The machine translation is not available — translate it yourself'
            : 'Three steps. You do not need to read French.'
        }
      >
        {notTranslatedYet ? (
          <ol className="space-y-3 text-xs text-ink-dim">
            <li className="flex gap-2">
              <Step n={1} />
              <div className="flex-1">
                <p>
                  Copy the prompt and the{' '}
                  <span className="font-medium text-ink">English</span>{' '}
                  together and paste them into your own assistant. Split it in
                  half at a paragraph break if it is long — never mid-sentence.
                </p>
                <Button size="sm" variant="primary" className="mt-2"
                        onClick={() => copyFor('translate')}>
                  {copied === 'translate' ? 'Copied' : 'Copy prompt + English'}
                </Button>
              </div>
            </li>
            <li className="flex gap-2">
              <Step n={2} />
              <p className="flex-1">
                Paste the French back into the box below and save. Text only:
                if your assistant added “Here is the translation”, leave it
                out.
              </p>
            </li>
            <li className="flex gap-2">
              <Step n={3} />
              <p className="flex-1">
                Fix anything flagged, then preview and submit.
              </p>
            </li>
          </ol>
        ) : (
        <ol className="space-y-3 text-xs text-ink-dim">
          <li className="flex gap-2">
            <Step n={1} />
            <div className="flex-1">
              <p>
                Copy the prompt and the French together, and paste them into
                your own assistant. The prompt is included — you do not need
                one of your own.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button size="sm" variant="primary" onClick={() => copyFor('polish')}>
                  {copied === 'polish' ? 'Copied' : 'Copy prompt + translation'}
                </Button>
                {/* CHECKING IS NOT THE SAME JOB AS FIXING. When the draft
                    already looks clean, asking an assistant to "polish" it
                    invites changes for the sake of changes; asking it to
                    report gives a second opinion that can be compared
                    against what the gate found. */}
                <Button size="sm" variant="secondary" onClick={() => copyFor('review')}>
                  {copied === 'review' ? 'Copied' : 'Copy a check instead'}
                </Button>
              </div>
              <p className="mt-2 text-2xs text-ink-mute">
                If the draft already looks right, use{' '}
                <span className="font-medium text-ink">Copy a check
                instead</span> — it asks your assistant to compare the
                French against the English and answer{' '}
                <span className="font-medium text-ink">CLEAN</span> if it finds
                nothing. It reports; it does not rewrite.
              </p>
            </div>
          </li>
          <li className="flex gap-2">
            <Step n={2} />
            <p className="flex-1">
              Paste what comes back into the box below and save. Paste the
              text only — if your assistant added a line like “Here is the
              corrected version”, leave it out.
            </p>
          </li>
          <li className="flex gap-2">
            <Step n={3} />
            <p className="flex-1">
              Fix anything flagged, then preview and submit.
            </p>
          </li>
        </ol>
        )}

        <div className="mt-4">
          <label htmlFor="polished" className="text-2xs font-medium text-ink-mute">
            Polished French
          </label>
          <textarea
            id="polished"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            rows={8}
            placeholder="Paste the polished translation here…"
            className="mt-1 w-full rounded-lg border border-edge bg-canvas px-3 py-2
                       font-mono text-xs text-ink placeholder:text-ink-mute"
          />
          <div className="mt-2 flex items-center gap-3">
            <Button
              size="sm"
              variant="primary"
              onClick={save}
              loading={saving}
              disabled={!pasted.trim()}
            >
              Save polished version
            </Button>
            <span className="text-2xs text-ink-mute tabular-nums">
              {pasted.trim() ? `${pasted.trim().split(/\s+/).length} words` : ''}
              {data?.polished_at ? ' · a polished version is already saved' : ''}
            </span>
          </div>
        </div>

        {result && <Findings result={result} />}
      </Card>

      <Card
        title={notTranslatedYet ? 'Transcript' : 'Source and translation'}
        subtitle={
          notTranslatedYet
            ? `${rows.length} segments · the French has not been produced yet`
            : `${rows.length} segments · English is read-only`
        }
        actions={
          <Button
            size="sm"
            onClick={() => setShowPreview(true)}
            disabled={notTranslatedYet && !data?.polished_text}
            title={notTranslatedYet && !data?.polished_text
              ? 'Save a translation first'
              : undefined}
          >
            {isDelivered(task) ? 'View what was submitted' : 'Preview submission'}
          </Button>
        }
        padded={false}
      >
      {/* ITS OWN SCROLL BOX, WHICH IS THE WHOLE FIX.
          `scrollIntoView` scrolls the nearest scrollable ANCESTOR. With no
          scroll box here that ancestor was the page, so following the audio
          dragged the player and the Preview button off the top of the
          screen -- and a worker who scrolled back down to reach Pause was
          hauled up again by the next segment. Bounding the list keeps the
          movement inside this panel and the controls where they were put. */}
      <div ref={listBox} onScroll={onListScroll}
           className="max-h-[60vh] overflow-y-auto overscroll-contain">
        <div className="sticky top-0 z-10 grid grid-cols-2 gap-px bg-edge/60 text-2xs font-medium text-ink-mute">
          <div className="bg-surface px-4 py-2">English (source)</div>
          <div className="bg-surface px-4 py-2">
            {notTranslatedYet
              ? 'French — not produced yet'
              : data?.polished_at ? 'French (machine draft)' : 'French'}
          </div>
        </div>
        <ol className="divide-y divide-edge/60">
          {rows.map((row) => {
            const active = playhead >= row.start && playhead < (row.end ?? row.start);
            return (
              <li
                key={row.key}
                ref={active ? activeRow : null}
                // THE WHOLE ROW SEEKS. Both halves say the same thing, so
                // either is a fair handle for "play this bit" -- and the
                // pair is what a reviewer is actually comparing.
                onClick={() => playerRef.current?.seekTo(row.start)}
                className={`grid cursor-pointer grid-cols-2 gap-4 px-4 py-2.5 transition-colors ${
                  active ? 'bg-accent-soft' : 'hover:bg-overlay/60'
                }`}
              >
                {editing === row.id ? (
                  <div onClick={(e) => e.stopPropagation()}>
                    <textarea
                      autoFocus
                      value={draftText}
                      onChange={(e) => setDraftText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setEditing(null);
                        // Enter saves; Shift+Enter is a newline, because a
                        // turn occasionally needs one.
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          saveEnglish(row);
                        }
                      }}
                      rows={3}
                      className={`w-full rounded-lg border border-edge bg-surface p-2 text-xs
                                  text-ink ${FOCUS}`}
                    />
                    <div className="mt-1 flex gap-2">
                      <Button size="sm" variant="primary" onClick={() => saveEnglish(row)}>
                        Save English
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className={`text-xs ${active ? 'text-accent-ink' : 'text-ink-dim'}`}>
                    <p>{row.english}</p>
                    {/* ALWAYS VISIBLE, NOT REVEALED ON HOVER. A hover-only
                        link was invisible on a touch device and easy to
                        miss even with a mouse -- a worker scanning a long
                        transcript for a mishearing has no reason to hover
                        every line on the chance a control is hiding there. */}
                    {row.id && (
                      <Button
                        size="sm" variant="ghost" className="mt-1"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDraftText(row.english);
                          setEditing(row.id);
                        }}
                      >
                        Edit
                      </Button>
                    )}
                  </div>
                )}
                <div className={`text-xs ${active ? 'text-accent-ink' : 'text-ink'}`}>
                  {/* THE ROW SHOWS THE CORRECT TEXT, NOT A COPY OF IT
                      SITTING NEXT TO THE STALE ONE. A separate "fresh
                      translation" box below the original reads as though
                      nothing happened -- the worker has to notice a new box
                      appeared and manually judge which text is now true.
                      Once a retranslate finishes, its result IS this row's
                      French from then on. */}
                  <p className="flex items-start gap-1.5">
                    <span>{retranslated[row.key] || row.french || '—'}</span>
                    {retranslated[row.key] && (
                      <span
                        title="This line was retranslated after the English was corrected"
                        className="mt-0.5 shrink-0 rounded-full bg-accent/15 px-1.5 py-0.5
                                   text-2xs font-medium uppercase tracking-wide text-accent"
                      >
                        updated
                      </span>
                    )}
                  </p>
                  {!notTranslatedYet && (
                    <div className="mt-1 flex items-center gap-2">
                      <Button
                        size="sm" variant="ghost"
                        loading={retranslating === row.key}
                        onClick={(e) => {
                          e.stopPropagation();
                          retranslateLine(row);
                        }}
                      >
                        Retranslate this line
                      </Button>
                      {/* Still needed: this table is a preview, not the
                          document. The polish textarea below is what gets
                          submitted, and it is free prose the worker may
                          have already reworded -- splicing a fresh sentence
                          into it by position would be a guess, not a fix.
                          Copy hands the worker exactly what changed so they
                          can place it themselves. */}
                      {retranslated[row.key] && (
                        <Button
                          size="sm" variant="ghost"
                          onClick={(e) => { e.stopPropagation(); copyRetranslated(row); }}
                        >
                          Copy
                        </Button>
                      )}
                    </div>
                  )}
                  {retranslateError[row.key] && (
                    <p
                      onClick={(e) => e.stopPropagation()}
                      role="alert"
                      className="mt-1 text-2xs text-status-rejected"
                    >
                      {retranslateError[row.key]}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
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

function Step({ n }) {
  return (
    <span
      aria-hidden="true"
      className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full
                 bg-overlay text-2xs font-medium text-ink"
    >
      {n}
    </span>
  );
}

/**
 * What the checks found.
 *
 * Ordered by severity, because a worker with ten minutes left needs to know
 * what to fix first -- and because the mechanical findings are the ones
 * they can act on without reading the language.
 */
const ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const TONE = {
  critical: 'border-status-rejected/40 bg-status-rejected/10 text-status-rejected',
  high: 'border-status-review/40 bg-status-review/10 text-status-review',
  medium: 'border-edge bg-raised text-ink-dim',
  low: 'border-edge bg-raised text-ink-mute',
};

function Findings({ result }) {
  const sorted = [...(result.findings || [])].sort(
    (a, b) => (ORDER[a.severity] ?? 9) - (ORDER[b.severity] ?? 9)
  );

  if (sorted.length === 0) {
    return (
      <p className="mt-4 rounded-lg border border-status-ready/30 bg-status-ready/10 px-3 py-2 text-xs text-ink">
        Saved. Nothing flagged — preview and submit when you are ready.
      </p>
    );
  }

  return (
    <ul className="mt-4 space-y-2">
      {sorted.map((f, i) => (
        <li key={i} className={`rounded-lg border px-3 py-2 text-2xs ${TONE[f.severity] || TONE.low}`}>
          <span className="font-medium uppercase">{f.severity}</span>
          <span className="mx-1.5" aria-hidden="true">·</span>
          <span className="text-ink">{f.message}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The document, as the server will send it. Fetched, never built here --
 * a second formatter in the browser could disagree with the one that
 * submits, and the disagreement would arrive as a rejection an hour later.
 */
function PreviewDialog({ task, onClose, onSubmitted }) {
  const load = useCallback(() => submission.preview(task.id), [task.id]);
  const { data, error, loading } = useAsync(load, [task.id]);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState('');
  const [sent, setSent] = useState(null);

  // A refused preview is blocking in its own right: if the document could
  // not be rendered, there is nothing to send.
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

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="tpreview-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <div className="border-b border-edge px-5 py-4">
          <h2 id="tpreview-title" className="text-sm font-semibold text-ink">
            What will be submitted
          </h2>
          <p className="mt-0.5 text-2xs text-ink-mute">
            Rendered by the server — this is exactly what the platform receives.
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {loading && <Skeleton className="h-40 rounded-lg" />}
          {error && <ErrorState message={error.message} compact />}
          {data && (
            <>
              <Verdict
                findings={data.findings || []}
                blocking={Boolean(data.blocking)}
                className="mb-4"
              />
              <pre className="whitespace-pre-wrap rounded-lg border border-edge bg-canvas p-3 font-mono text-2xs text-ink">
                {data.content}
              </pre>
            </>
          )}
          {failure && (
            <p role="alert" className="mt-3 text-2xs text-status-rejected">{failure}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-edge px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>Back</Button>
          <Button
            variant="primary"
            onClick={send}
            loading={submitting}
            disabled={blocking || loading}
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
      </div>
    </div>
  );
}
