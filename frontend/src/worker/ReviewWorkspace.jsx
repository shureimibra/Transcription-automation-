import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AudioPlayer from './AudioPlayer.jsx';
import DeadlineClock from './DeadlineClock.jsx';
import { useWorkspaceApi } from './workspaceApi.js';
import FinalDraftDialog from '../components/FinalDraftDialog.jsx';
import { ANNOTATION_TAGS, insertTagAtPosition } from '../lib/annotations.js';
import { locatePhrase, parseFindings, primaryPhrase, replaceFirst } from '../lib/accuracyFindings.js';
import DeliveredBanner, { isDelivered } from '../components/DeliveredBanner.jsx';
import AccuracyChecklist from '../components/AccuracyChecklist.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { useSegmentSearch, splitOnMatch } from '../hooks/useSegmentSearch.js';
import SegmentSearchBar from '../components/SegmentSearchBar.jsx';
import {
  Button, Card, ErrorState, FOCUS, Notice, Skeleton, inputClass,
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

/** A numbered step marker, matching TranslationWorkspace's own Step. */
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

export default function ReviewWorkspace({ task, onTaskChange }) {
  // Your workers' shared draft, or an Eagle tasker's private copy -- see
  // workspaceApi.js. `eagle` also swaps Submit for Copy below.
  const workspaceApi = useWorkspaceApi();
  const {
    mode, segments: segmentsApi, submission, taskMedia, transcript, tasks: tasksApi,
  } = workspaceApi;
  const eagleMode = mode === 'desk';
  // Workers' Preview + Submit dialog. Supplied by workerApi.js, absent for
  // Eagle taskers, whose bundle never contains it.
  const SubmitDialog = workspaceApi.dialogs?.review;
  // The workers' guide describes their submit process; taskers do not get it.
  const GuideModal = workspaceApi.dialogs?.reviewGuide;
  const playerRef = useRef(null);
  // FOLLOWING THE AUDIO WAS THE MISSING HALF, same as TranslationWorkspace's
  // own screen had before this: the highlight moved with the playhead, but
  // nothing scrolled to keep it in view -- on a long transcript the reader
  // has to go hunting for wherever it wandered off to. Ported from that
  // screen rather than reinvented, so both work the same way.
  const activeRow = useRef(null);
  const listBox = useRef(null);
  //: Whether the transcript follows the audio. Off while the reader has
  //: scrolled away, on again once the spoken line is back in view.
  const [following, setFollowing] = useState(true);
  const [playhead, setPlayhead] = useState(0);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');
  // Only one row is ever in edit mode here (unlike the studio, where every
  // segment has its own always-visible textarea), so one remembered cursor
  // position is enough -- reset implicitly whenever `editing` changes, since
  // nothing reads it until the annotate dropdown is used again.
  const draftCursorRef = useRef(null);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const [copyingCheck, setCopyingCheck] = useState(false);
  const [checkCopied, setCheckCopied] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const [checkNotesInput, setCheckNotesInput] = useState('');
  const [savingCheckNotes, setSavingCheckNotes] = useState(false);
  // A NEW SPEAKER, DECLARED BEFORE ANY ROW USES IT.
  //
  // THE GAP THIS CLOSES: every dropdown here only ever offered labels
  // already present on some row, because `speakers` (below) is derived
  // from the rows themselves. A REAL INCIDENT: diarization heard only one
  // speaker for a whole medical lecture -- a brief introduction, then a
  // second person who gave 90% of the talk -- and there was no way to add
  // "Male_2" as an option at all, because no row had ever used it. The
  // worker had to edit the database directly and submit around this
  // platform.
  //
  // PURELY LOCAL, NOT PERSISTED. The moment any row is actually
  // reassigned to a pending label it becomes a real one, indistinguishable
  // from a diarization-assigned label from then on -- so there is nothing
  // this needs to remember past that point, and a lost tab before then
  // costs one re-click, not lost work.
  const [pendingSpeakers, setPendingSpeakers] = useState([]);
  const [addingGender, setAddingGender] = useState('Male');
  // Which row is mid-split, and who the second half belongs to. Separate
  // from `editing` (which row's TEXT is open) because a worker splits from
  // inside that same edit box -- see the Split control below.
  const [splitting, setSplitting] = useState(false);
  const [splitSpeaker, setSplitSpeaker] = useState('');

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

  // WHICH LINE THE AUDIO IS ON. Derived rather than stored, so it cannot
  // drift out of step with the player -- same reasoning as
  // TranslationWorkspace's identical activeKey.
  const activeKey = useMemo(() => {
    const row = rows.find((r) => playhead >= r.start_time && playhead < r.end_time);
    return row ? row.id : null;
  }, [rows, playhead]);

  // KEEP IT IN VIEW, AND ONLY WHEN IT CHANGES. Scrolling on every playhead
  // tick would fight the reader for control of the page; once per turn is
  // following along, more than that is a wrestling match. Scrolling away
  // turns following OFF, and it resumes on its own once the line being
  // spoken is back on screen -- so reaching for Pause or reading back
  // never gets undone by the next segment a second later.
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

  // FIND EVERY SEGMENT MENTIONING A WORD, AND STEP THROUGH JUST THOSE.
  // See useSegmentSearch's own docstring for the scenario this exists
  // for: a name spelled three different wrong ways across a long
  // transcript, findable in seconds instead of by reading past every
  // segment that was never the problem.
  const search = useSegmentSearch(rows, (row) => row.text);
  const matchingIds = useMemo(
    () => new Set(search.matches.map((m) => m.row.id)),
    [search.matches],
  );
  // A SEPARATE REF MAP FROM activeRow. That one tracks whichever segment
  // the AUDIO is currently on, for the follow-while-playing behaviour
  // above; a search jump moves to whatever segment MATCHED, which is
  // usually a different one and must scroll on its own, not wait for the
  // playhead to agree.
  const searchRowRefs = useRef({});

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

  // WHETHER THE ACCURACY CHECK HAS BEEN RUN, across a reload. Fetched
  // alongside the prompt itself rather than only in copyAccuracyCheck's
  // fire-and-forget call, because `preview`/`submit` now REQUIRE this step
  // -- a worker returning to a task needs to see it is already done, not
  // rediscover that by hitting a blocked submit.
  const checkLoad = useCallback(
    () => (labelsSpeakers ? submission.checkPrompt(task.id) : Promise.resolve(null)),
    [labelsSpeakers, task.id],
  );
  const { data: checkStatus, refresh: refreshCheckStatus } = useAsync(
    checkLoad, [task.id, labelsSpeakers],
  );

  // REHYDRATE THE BOX FROM WHAT WAS ALREADY SAVED. checkPrompt's own
  // docstring promises "the screen can show the step as done across a
  // reload" -- but showing `accuracy_check_by` is not the same as showing
  // the actual checklist, and the checklist is driven entirely by
  // `checkNotesInput`, which starts every mount as ''. Without this, the
  // one thing a worker actually needs after a reload or a save -- the
  // clickable Find it / Apply list -- was invisible even though the text
  // that would produce it was sitting in `checkStatus` the whole time.
  // Fires once per mount, and only into an empty box, so a worker who has
  // deliberately cleared it to paste a fresh reply is never fought.
  const hydratedCheckNotes = useRef(false);
  useEffect(() => {
    if (hydratedCheckNotes.current || !checkStatus) return;
    if (checkStatus.accuracy_check_notes) {
      setCheckNotesInput(checkStatus.accuracy_check_notes);
    }
    hydratedCheckNotes.current = true;
  }, [checkStatus]);

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

  /**
   * Every label a dropdown may offer: the real ones (already on some row)
   * plus any just-added, not-yet-used ones. Filtered against `speakers` so
   * a pending label that HAS since been used does not show up twice —
   * once for real, once as a stale pending entry.
   */
  const speakerOptions = useMemo(() => {
    const known = new Set(speakers.map((s) => s.label));
    const extra = pendingSpeakers
      .filter((label) => !known.has(label))
      .map((label) => ({ label, display: label }));
    return [...speakers, ...extra];
  }, [speakers, pendingSpeakers]);

  /**
   * The next free number for a gender, so "Add speaker" never offers a
   * label that collides with one already in use — real or pending.
   */
  const nextSpeakerNumber = (gender) => {
    const prefix = `${gender}_`;
    const used = new Set(
      [...speakers.map((s) => s.label), ...pendingSpeakers]
        .filter((label) => label.startsWith(prefix))
        .map((label) => parseInt(label.slice(prefix.length), 10))
        .filter((n) => Number.isFinite(n))
    );
    let n = 1;
    while (used.has(n)) n += 1;
    return n;
  };

  const addSpeaker = () => {
    const label = `${addingGender}_${nextSpeakerNumber(addingGender)}`;
    setPendingSpeakers((prev) => (prev.includes(label) ? prev : [...prev, label]));
    setNotice(`${label} added — select it from any segment's speaker dropdown.`);
  };

  /**
   * One segment, spoken by two people, split at `splitAt` (a character
   * offset into the CURRENT DRAFT — a worker may have already fixed a
   * typo before noticing the turn change, and the split has to act on
   * what they are actually looking at, not the stale saved text).
   *
   * Replaces the one row with the two the server returns rather than
   * refetching the whole job: `first` keeps this segment's id and shrinks
   * to end at the new boundary, `second` is newly created. `rows`
   * re-sorts by start_time on its own, so nothing else has to place it.
   */
  const splitSegment = async (segment, splitAt, secondSpeaker) => {
    setSaving(segment.id);
    setError('');
    try {
      const { first, second } = await segmentsApi.split(jobId, segment.id, splitAt, secondSpeaker);
      setData((current) => (current ? {
        ...current,
        segments: current.segments.flatMap((s) => (s.id === segment.id ? [first, second] : [s])),
      } : current));
      setNotice(`Split into two turns — the second is ${displayName(secondSpeaker) || secondSpeaker}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(null);
      setEditing(null);
      setSplitting(false);
    }
  };

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

  // TURNING THE PASTED REPORT INTO A CHECKLIST, NOT JUST A RECORD.
  //
  // See AccuracyChecklist's own docstring for why "Apply" writes straight
  // to the segment rather than opening a second editable draft, and why
  // there is deliberately no "apply everything" shortcut.
  //
  // `appliedPhrases` comes from real, permanent history
  // (ACCURACY_CHECK_FINDING_APPLIED events), NOT from accuracy_check_notes
  // -- that field holds only the latest pasted report, so it alone could
  // never tell a re-flagged phrase from a first-flagged one once a second
  // check overwrites the first. See the event's own docstring on the
  // model for the full reasoning.
  const { data: taskEvents, refresh: refreshEvents } = useAsync(
    () => tasksApi.events(task.id), [task.id],
  );
  const appliedAt = useMemo(() => {
    const map = new Map();
    for (const event of taskEvents || []) {
      if (event.event_type !== 'ACCURACY_CHECK_FINDING_APPLIED') continue;
      const phrase = (event.metadata?.phrase || '').toLowerCase();
      // Events come back newest-first; the first one seen per phrase is
      // therefore the most recent application of it.
      if (phrase && !map.has(phrase)) map.set(phrase, event.occurred_at);
    }
    return map;
  }, [taskEvents]);

  const findings = useMemo(() => parseFindings(checkNotesInput), [checkNotesInput]);
  const enrichedFindings = useMemo(() => findings.map((finding) => {
    // ONE MATCHABLE STRING, NOT THE CARD'S FULL DISPLAY TEXT. A rule-2
    // finding's `phrase` legitimately lists several spellings ("X" / "Y" /
    // "Z") -- real, valuable evidence, and left alone below so the card
    // still shows all of it -- but no single transcript segment ever
    // contains that whole joined string. CONFIRMED LIVE: every such
    // finding showed "Not found in the transcript" regardless of whether
    // it had been, because this used to search for the joined text
    // itself. `primaryPhrase` is the one alternative actually worth
    // searching for, quote-normalised the same way `containsPhrase` below
    // normalises both sides -- see accuracyFindings.js for the other half
    // of this (a curly apostrophe copied out of a chat UI silently
    // defeating a match against straight-quoted ASR text).
    const key = primaryPhrase(finding.phrase);
    const when = appliedAt.get(key.toLowerCase());
    const located = locatePhrase(rows, key);
    return {
      ...finding,
      applied: Boolean(when),
      appliedAt: when ? new Date(when).toLocaleDateString() : null,
      found: Boolean(located),
      // A SHORT-SEGMENT TASK MAKES THIS COMMON, NOT RARE. Confirmed live:
      // "power limit matrices" was "...the power limit" at the end of one
      // segment and "matrices is..." at the start of the next -- every
      // word untouched, but no single row ever contained the whole
      // phrase. Kept as its own flag rather than folded into `found`
      // because Apply below has to treat the two cases differently: one
      // segment is a safe, ordinary rewrite; two is not something
      // replaceFirst can do at all.
      spansSegments: Boolean(located && located.rows.length > 1),
    };
  }), [findings, appliedAt, rows]);

  const [applyingPhrase, setApplyingPhrase] = useState(null);

  const findFinding = (finding) => {
    const key = primaryPhrase(finding.phrase);
    const located = locatePhrase(rows, key);
    if (located && located.rows.length > 1) {
      // The ordinary text search matches one row at a time, same as
      // containsPhrase -- it would come back empty for exactly the
      // phrase this button exists to help find. locatePhrase already
      // knows exactly where it starts; jump straight there instead of
      // asking the search bar to fail at the same boundary.
      playerRef.current?.seekTo(located.rows[0].start_time);
      searchRowRefs.current[located.rows[0].id]?.scrollIntoView?.({ block: 'center' });
      return;
    }
    search.setQuery(key);
    if (!search.open) search.openSearch();
  };

  const applyFinding = async (finding) => {
    const key = primaryPhrase(finding.phrase);
    const located = locatePhrase(rows, key);
    if (!located) {
      // CONFIRMED LIVE: this returned silently, with no error and no
      // notice, so clicking Apply on a finding whose phrase the checklist
      // itself had just said WAS found (the button only renders when
      // `finding.found` is true) looked like the click did nothing at
      // all. It can still legitimately happen -- a segment edited or
      // reverted between the checklist rendering and the click -- but a
      // worker needs to be told that, not left to guess.
      setError(
        `Could not find "${finding.phrase}" in the transcript anymore -- `
        + 'it may have already been edited. Refresh the checklist and try again.'
      );
      return;
    }
    if (located.rows.length > 1) {
      // NOT SOMETHING TO GUESS AT. replaceFirst rewrites one segment's
      // text; splitting a replacement across two would mean deciding
      // where the cut falls, and getting that wrong risks corrupting
      // text right at a boundary Eagle grades on timing. Told plainly
      // rather than attempted automatically or failed silently.
      setError(
        `"${finding.phrase}" spans two lines in the transcript, starting `
        + `around ${timestamp(located.rows[0].start_time)} -- edit both `
        + 'lines by hand instead of using Apply here.'
      );
      return;
    }
    const row = located.rows[0];
    setApplyingPhrase(finding.phrase);
    setError('');
    try {
      const newText = replaceFirst(row.text, key, finding.suggestion);
      const updated = await segmentsApi.update(jobId, row.id, { text: newText });
      setData((current) => (current ? {
        ...current,
        segments: current.segments.map((s) => (s.id === row.id ? { ...s, ...updated } : s)),
      } : current));
      // Recorded only once the segment write actually succeeded -- the
      // audit trail must never claim a correction was applied when it
      // was not. Recorded under `key`, not the full display phrase, so a
      // future reload's `appliedAt` lookup above (which also keys on
      // `primaryPhrase`) finds it.
      await tasksApi.applyFinding(task.id, key, finding.suggestion);
      await refreshEvents({ silent: true });
      setNotice(`Applied: "${finding.phrase}" → "${finding.suggestion}"`);
    } catch (err) {
      setError(err.message);
    } finally {
      setApplyingPhrase(null);
    }
  };

  /**
   * Same tag vocabulary and the same fixed insertion behaviour as the
   * studio (views/Dashboard.jsx / lib/annotations.js): lands at the last
   * REMEMBERED cursor position, not wherever `document.activeElement`
   * happens to be when this runs -- which is never the textarea, since
   * clicking the dropdown itself already moved focus off it.
   *
   * Does not save on its own. It updates `draft` the same way typing
   * would, so the existing Save/Cancel/Ctrl+Enter flow is exactly what
   * commits it -- one save path, not two.
   */
  const handleInsertAnnotation = (tag) => {
    const resolvedTag = tag === 'Custom tag...' ? window.prompt('Enter a custom tag (e.g. [whispering]):') : tag;
    if (!resolvedTag) return;
    const { text: newDraft, cursor } = insertTagAtPosition(draft, draftCursorRef.current, resolvedTag);
    draftCursorRef.current = cursor;
    setDraft(newDraft);
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

  /**
   * The transcription-side counterpart to TranslationWorkspace's
   * "Copy a check instead" -- see TranscriptCheckPromptView's own
   * docstring for why this is the only variant: a worker's assistant has
   * never heard the audio, so it hands back leads to go verify, not a
   * document to paste over the transcript.
   *
   * REUSES THE SAME DOCUMENT THE PREVIEW DIALOG SHOWS, rather than
   * rendering a second copy of it here -- `submission.preview` is already
   * the one formatter this screen trusts to say what Eagle will receive.
   */
  const copyAccuracyCheck = async () => {
    setCopyingCheck(true);
    setError('');
    try {
      const [{ prompt }, preview] = await Promise.all([
        submission.checkPrompt(task.id),
        submission.preview(task.id),
      ]);
      const payload = `${prompt || ''}${preview?.content || ''}`;
      if (!payload.trim()) {
        setNotice('There is nothing to copy yet.');
        return;
      }
      await navigator.clipboard.writeText(payload);
      setCheckCopied(true);
      setNotice(
        'Prompt and transcript copied. Paste it into your assistant, then '
        + 'check anything it flags against the audio before you edit -- it '
        + 'has not heard this recording.'
      );
      setTimeout(() => setCheckCopied(false), 2500);
    } catch (err) {
      setError(err.message || 'Could not build the check prompt.');
    } finally {
      setCopyingCheck(false);
    }
  };

  /**
   * Record what the worker's own assistant found. AN AUDIT TRAIL, NOT A
   * REWRITE -- this never touches the transcript itself; see
   * TranscriptCheckPromptView's own docstring for why a paste-back that
   * overwrites the document was deliberately not built for transcription
   * the way it was for translation. What this unlocks is `preview`/
   * `submit`: the gate now refuses a transcription task until this has run
   * once, so saving hands back the gate's fresh verdict immediately rather
   * than leaving the worker to discover it only inside the preview dialog.
   *
   * THE BOX IS LEFT ALONE ON SUCCESS, DELIBERATELY. Clearing it here used
   * to wipe `checkNotesInput` the instant a save succeeded -- and the
   * checklist below (Find it / Apply on each finding) is parsed from that
   * same state, so the one moment a worker most needed it, right after
   * pasting and saving, was exactly when it vanished, leaving nothing but
   * the plain "Already saved by ..." line. What was pasted stays visible
   * and actionable; a worker who wants a clean box for a fresh reply
   * clears it themselves.
   */
  const saveAccuracyCheckNotes = async () => {
    const notes = checkNotesInput.trim();
    if (!notes) return;
    setSavingCheckNotes(true);
    setError('');
    try {
      const result = await submission.saveAccuracyCheck(task.id, notes);
      await refreshCheckStatus();
      const count = result.findings?.length || 0;
      setNotice(
        `Saved. ${count} finding(s) from the automated checks`
        + `${result.blocking ? ' -- something still blocks submission.' : '.'}`
      );
    } catch (err) {
      setError(err.message || 'Could not save the accuracy-check reply.');
    } finally {
      setSavingCheckNotes(false);
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
      {/* Every FAILURE on this screen already routes to `error` above, which
          is visible -- `notice` here is only ever a confirmation ("Saved.",
          a rename, a copy). Still worth a visible line, not just an
          aria-live announcement a sighted worker would never see, for the
          same reason TranslationWorkspace's Notice exists. */}
      <Notice message={notice} tone="info" />

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
          subtitle="Rename one everywhere, fix a single turn below, or add one diarization missed"
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
            {/* NOT YET ON ANY ROW -- see pendingSpeakers' own comment. Shown
                distinctly from a real chip (no turn count, because there
                is none yet) so it reads as "ready to use", not as a
                speaker already found in the audio. */}
            {pendingSpeakers
              .filter((label) => !speakers.some((s) => s.label === label))
              .map((label) => (
                <div key={label}
                     className="flex items-center gap-1.5 rounded-lg border border-dashed
                                border-accent/50 bg-canvas px-2 py-1">
                  <span className="text-xs font-medium text-ink">{label}</span>
                  <span className="text-2xs text-ink-mute">not used yet</span>
                </div>
              ))}
            {speakers.length === 0 && pendingSpeakers.length === 0 && (
              <p className="text-xs text-ink-mute">No speaker labels on this transcript.</p>
            )}
          </div>
          {/* THE GAP THIS CLOSES: a diarization pass that merged two real
              speakers into one label had no way to introduce the second
              one at all -- every dropdown only ever offered labels already
              on some row. See pendingSpeakers' own comment for the real
              incident this is built from. */}
          {!readOnly && (
            <div className="mt-3 flex items-center gap-2 border-t border-edge pt-3">
              <select
                aria-label="Gender for the new speaker"
                value={addingGender}
                onChange={(e) => setAddingGender(e.target.value)}
                className="rounded border border-edge bg-canvas px-1.5 py-1 text-2xs text-ink"
              >
                <option value="Male">Male</option>
                <option value="Female">Female</option>
                <option value="Unknown">Unknown</option>
              </select>
              <Button size="sm" variant="secondary" onClick={addSpeaker}>
                + Add {addingGender}_{nextSpeakerNumber(addingGender)}
              </Button>
              <span className="text-2xs text-ink-mute">
                For a speaker the machine missed entirely — pick it from any
                segment's dropdown below once added.
              </span>
            </div>
          )}
        </Card>
      )}

      {/* REQUIRED, NOT OPTIONAL -- see run_quality_gate's
          'self-check-not-completed' finding. This sits AFTER every editing
          tool above (speakers, and the per-line correction below) and
          BEFORE Preview/Submit -- an additional checkpoint, not a
          replacement for any of them. The tasker still owns verifying the
          words against the audio through the normal editing; the worker's
          own assistant is asked for what it can actually help with --
          domain vocabulary and terminology a hand-maintained rule was
          never written for. Hidden on a translation task and on delivered
          work, same as the guide button below. */}
      {labelsSpeakers && !readOnly && (
        <Card
          title="Accuracy check"
          subtitle={
            checkStatus?.accuracy_check_by
              ? `Completed by ${checkStatus.accuracy_check_by}`
              : eagleMode
                ? 'Recommended before you paste your final draft'
                : 'Required before this task can be submitted'
          }
        >
          <ol className="space-y-3 text-xs text-ink-dim">
            <li className="flex gap-2">
              <Step n={1} />
              <div className="flex-1">
                <p>
                  Copy the prompt and the transcript together, and paste
                  them into your own assistant.
                </p>
                <Button size="sm" variant="primary" className="mt-2"
                        loading={copyingCheck} onClick={copyAccuracyCheck}>
                  {checkCopied ? 'Copied' : 'Copy accuracy check'}
                </Button>
              </div>
            </li>
            <li className="flex gap-2">
              <Step n={2} />
              <p className="flex-1">
                Check anything it flags against the audio and fix it through
                the normal editing below — it has not heard this recording,
                so treat what it says as leads, not answers.
              </p>
            </li>
            <li className="flex gap-2">
              <Step n={3} />
              <p className="flex-1">
                Paste what it found into the box below and save, as a record
                that the check was run.
              </p>
            </li>
          </ol>

          <div className="mt-4">
            <label htmlFor="accuracy-check-notes"
                   className="text-2xs font-medium text-ink-mute">
              What your assistant found
            </label>
            <textarea
              id="accuracy-check-notes"
              value={checkNotesInput}
              onChange={(e) => setCheckNotesInput(e.target.value)}
              rows={5}
              placeholder="Paste your assistant's reply here…"
              className="mt-1 w-full rounded-lg border border-edge bg-canvas px-3 py-2
                         font-mono text-xs text-ink placeholder:text-ink-mute"
            />
            <div className="mt-2 flex items-center gap-3">
              <Button size="sm" variant="primary" onClick={saveAccuracyCheckNotes}
                      loading={savingCheckNotes} disabled={!checkNotesInput.trim()}>
                Save accuracy check
              </Button>
              <span className="text-2xs text-ink-mute">
                {checkStatus?.accuracy_check_by
                  ? `Already saved by ${checkStatus.accuracy_check_by} — `
                    + 'pasting again replaces it'
                  : 'Not completed yet'}
              </span>
            </div>
            {/* Same box, same paste, no second place to put the same text --
                the checklist is just what its own contents parse into. */}
            <AccuracyChecklist
              findings={enrichedFindings}
              hasText={Boolean(checkNotesInput.trim())}
              onFind={findFinding}
              onApply={applyFinding}
              applying={applyingPhrase}
            />
          </div>
        </Card>
      )}

      <Card
        title="Transcript"
        subtitle={`${rows.length} turns · click any line to hear it`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {!search.open && (
              <Button size="sm" variant="ghost" onClick={search.openSearch}
                      title="Search this transcript">
                ⌕ Search
              </Button>
            )}
            {labelsSpeakers && GuideModal && (
              <Button size="sm" variant="ghost" onClick={() => setShowGuide(true)}>
                Full guide
              </Button>
            )}
            <Button size="sm" onClick={() => setShowPreview(true)}>
              {/* Delivered work can still be READ -- a coach asks what was sent
                  often enough -- but it is never offered a submit path. */}
              {eagleMode ? 'Get final draft'
                : isDelivered(task) ? 'View what was submitted' : 'Preview submission'}
            </Button>
          </div>
        }
        padded={false}
      >
        {/* BOUNDED AND SELF-SCROLLING, matching TranslationWorkspace's own
            panel -- see the following-the-audio note above. Without this
            the highlight still moved with the playhead but the page around
            it did not, so a reader had to go hunting for wherever a long
            transcript's highlight had wandered off to. */}
        <div ref={listBox} onScroll={onListScroll}
             className="max-h-[60vh] overflow-y-auto overscroll-contain">
        <SegmentSearchBar
          search={search}
          placeholder="Search the transcript…"
          onJump={(row) => {
            playerRef.current?.seekTo(row.start_time);
            searchRowRefs.current[row.id]?.scrollIntoView?.({ block: 'center' });
          }}
          onTogglePlay={() => playerRef.current?.togglePlay()}
        />
        <ol className="divide-y divide-edge/60">
          {rows.map((row) => {
            const active = playhead >= row.start_time && playhead < row.end_time;
            // SEARCHING DIMS EVERYTHING ELSE. With a query typed, the
            // segments that did not match recede instead of disappearing
            // outright -- position in the transcript still matters (a
            // worker reads a fix in context), but the eye should land on
            // the handful that matched, not scan all 400 to find them.
            const searching = search.query.trim() !== '';
            const isMatch = matchingIds.has(row.id);
            const isCurrent = search.current?.row.id === row.id;
            return (
              <li
                key={row.id}
                ref={(el) => {
                  if (active) activeRow.current = el;
                  searchRowRefs.current[row.id] = el;
                }}
                // THE WHOLE ROW SEEKS, same as TranslationWorkspace's rows --
                // clicking a segment to just listen used to be impossible
                // without hitting the small timestamp link exactly; now the
                // default action on this screen matches the other one.
                onClick={() => playerRef.current?.seekTo(row.start_time)}
                className={`cursor-pointer px-4 py-3 transition-colors ${
                  isCurrent ? 'bg-accent-soft ring-1 ring-inset ring-accent'
                    : active ? 'bg-accent-soft' : 'hover:bg-overlay/60'
                } ${searching && !isMatch ? 'opacity-40' : ''}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  {labelsSpeakers && (
                    <select
                      aria-label={`Speaker for the turn at ${timestamp(row.start_time)}`}
                      value={row.speaker_label || ''}
                      disabled={readOnly || saving === row.id}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => patchSegment(row, { speaker_label: e.target.value })}
                      className="rounded border border-edge bg-canvas px-1.5 py-0.5 text-2xs text-ink"
                    >
                      {row.speaker_label && !speakerOptions.some((s) => s.label === row.speaker_label) && (
                        <option value={row.speaker_label}>{displayName(row.speaker_label)}</option>
                      )}
                      {speakerOptions.map((s) => (
                        <option key={s.label} value={s.label}>{s.display || s.label}</option>
                      ))}
                    </select>
                  )}

                  <span className="rounded font-mono text-2xs tabular-nums text-accent">
                    {timestamp(row.start_time)} – {timestamp(row.end_time)}
                  </span>

                  {/* EXPLICIT AND ALWAYS VISIBLE, not triggered by clicking
                      the words themselves -- see TranslationWorkspace's own
                      Edit button for the same reasoning, though this one is
                      styled as the ordinary action it is here: fixing a
                      mishearing is this screen's main job, not a rare,
                      consequential exception the way editing the English
                      source is on the translation side. */}
                  {!readOnly && editing !== row.id && (
                    <Button
                      size="sm" variant="secondary"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditing(row.id);
                        setDraft(row.text || '');
                        setSplitting(false);
                        // A STALE POSITION FROM A DIFFERENT ROW IS WORSE
                        // THAN NO POSITION: the backend refuses an out-of-
                        // range split_at, but a coincidentally in-range one
                        // left over from a longer row would split THIS
                        // text at a meaningless point instead of visibly
                        // doing nothing. Cleared so "Split here" starts
                        // disabled until this row's own text is clicked.
                        draftCursorRef.current = null;
                      }}
                    >
                      Edit
                    </Button>
                  )}
                </div>

                {editing === row.id ? (
                  <div className="mt-2 space-y-2" onClick={(e) => e.stopPropagation()}>
                    <textarea
                      autoFocus
                      rows={3}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onSelect={(e) => {
                        draftCursorRef.current = { start: e.target.selectionStart, end: e.target.selectionEnd };
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') { setEditing(null); setSplitting(false); }
                        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !splitting) {
                          patchSegment(row, { text: draft });
                        }
                      }}
                      className={`${inputClass} resize-y font-normal`}
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        value=""
                        onChange={(e) => { if (e.target.value) handleInsertAnnotation(e.target.value); e.target.value = ''; }}
                        title="Insert an annotation at the cursor"
                        className="shrink-0 rounded border border-edge bg-canvas px-1.5 py-0.5 text-2xs text-ink-mute"
                      >
                        <option value="">+ Annotate</option>
                        {ANNOTATION_TAGS.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
                        <option value="Custom tag...">Custom tag...</option>
                      </select>
                      {/* ONE SEGMENT, TWO SPEAKERS -- the OTHER shape
                          diarization gets wrong. Not the same problem the
                          annotate dropdown solves (marking something IN a
                          turn), and not solvable by inserting a bracket tag
                          either -- Eagle recognises a fixed, closed set of
                          those ([inaudible], [crosstalk]...), and even a
                          recognised one would not actually split this into
                          two properly attributed, separately timed lines.
                          This does that for real. */}
                      {!splitting && (
                        <Button size="sm" variant="ghost"
                                onClick={() => { setSplitSpeaker(''); setSplitting(true); }}>
                          Split turn…
                        </Button>
                      )}
                    </div>
                    {splitting ? (
                      <div className="flex flex-wrap items-center gap-2 rounded-lg border
                                       border-edge bg-raised px-3 py-2">
                        <span className="text-2xs text-ink-dim">
                          Click in the text above where the second speaker
                          starts, then choose who they are:
                        </span>
                        <select
                          aria-label="Second speaker"
                          value={splitSpeaker}
                          onChange={(e) => setSplitSpeaker(e.target.value)}
                          className="rounded border border-edge bg-canvas px-1.5 py-0.5 text-2xs text-ink"
                        >
                          <option value="">Choose speaker…</option>
                          {speakerOptions
                            .filter((s) => s.label !== row.speaker_label)
                            .map((s) => (
                              <option key={s.label} value={s.label}>{s.display || s.label}</option>
                            ))}
                        </select>
                        <Button
                          size="sm" variant="primary" loading={saving === row.id}
                          disabled={!splitSpeaker || !draftCursorRef.current?.start}
                          onClick={() => splitSegment(row, draftCursorRef.current.start, splitSpeaker)}
                        >
                          Split here
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setSplitting(false)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
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
                    )}
                  </div>
                ) : (
                  <p className="mt-1 text-sm leading-relaxed text-ink">
                    {/* No click-to-edit here any more -- the Edit button
                        above is the one, explicit way in. This text is part
                        of the row now, so clicking it seeks like the rest
                        of the row instead of surprising a reader who just
                        wanted to listen with an open textarea. */}
                    {/* Follow the audio sentence by sentence while it plays.
                        Only the segment being spoken is broken up -- doing it
                        to all 400 would cost a lot of DOM for nothing. */}
                    {/* SEARCH HIGHLIGHTING TAKES OVER WHILE A QUERY IS
                        TYPED, in place of the active-sentence split above --
                        the two would otherwise fight over the same text, and
                        a worker jumping between matches is not necessarily
                        mid-playback on any of them. */}
                    {searching
                      ? splitOnMatch(row.text, search.query).map((part, i) => (
                          <span
                            key={i}
                            className={part.hit
                              ? 'rounded bg-status-review/30 box-decoration-clone px-0.5'
                              : undefined}
                          >
                            {part.text}
                          </span>
                        ))
                      : active
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
        </div>
      </Card>

      {showPreview && eagleMode && (
        <FinalDraftDialog taskId={task.id} onClose={() => setShowPreview(false)} />
      )}
      {showPreview && !eagleMode && SubmitDialog && (
        <SubmitDialog
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

      {showGuide && GuideModal && <GuideModal onClose={() => setShowGuide(false)} />}
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
