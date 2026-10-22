import React, { useState, useEffect, useRef } from 'react';
import SearchableLanguageSelect from '../components/SearchableLanguageSelect';
import AIAssistPanel from '../components/AIAssistPanel';
import DiffReview from '../components/DiffReview';
import { ANNOTATION_TAGS, insertTagAtPosition } from '../lib/annotations';
import { api, ApiError, getToken, csrfToken } from '../api/client';

// Session recovery (persist across refresh/navigation/sleep-wake): the
// backend (TranscriptionJob + its segments/translations) is always the
// real source of truth. This localStorage entry is deliberately minimal --
// just enough to know WHICH job/series to re-fetch on the next page load,
// never a cache of the transcript itself. One slot for "the current
// session" (single job or series, mutually exclusive), matching this
// dashboard's single-active-job UI -- starting a new job overwrites it,
// exactly as starting a new job already replaces what's on screen.
const SESSION_STORAGE_KEY = 'transcription_session_v1';

function saveSession(session) {
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
  } catch (err) {
    console.warn('Could not persist session to localStorage (private browsing / storage full?)', err);
  }
}

function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    console.warn('Could not read persisted session from localStorage', err);
    return null;
  }
}

function clearSession() {
  try {
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (err) {
    // Nothing meaningful to do if localStorage itself is unavailable.
  }
}

const ACTIVE_JOB_STATUSES = ['queued', 'downloading', 'diarizing', 'transcribing'];

// Display labels for AcademicFormat.subject (matches the backend's
// AcademicFormat.SUBJECT_CHOICES exactly -- see core/services/academic).
const SUBJECT_LABELS = {
  GENERAL: 'General',
  MATHEMATICS: 'Mathematics',
  PHYSICS: 'Physics',
  CHEMISTRY: 'Chemistry',
  STEM_MIXED: 'STEM Mixed',
};

// Client-side allowlist mirroring what the backend pipeline is actually
// exercised against (ffmpeg itself handles far more than this, but this is
// the explicitly-verified supported set) -- advisory, not a security
// boundary: the backend accepts whatever's uploaded and lets ffmpeg fail
// gracefully on anything it can't decode (see extract_from_file's
// error_detail). This just avoids a pointless round-trip for an obviously
// wrong file, and matches the picker's `accept` attribute.
const SUPPORTED_UPLOAD_EXTENSIONS = ['.mp3', '.wav', '.m4a', '.mp4', '.mkv', '.mov', '.webm', '.flac'];

const formatFileSize = (bytes) => {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
};

// Mirrors core.services.transcript_formatting.group_segments_into_paragraphs
// on the backend (same thresholds, same signals) -- a lightweight,
// deterministic, O(n) presentation-layer pass over already-streamed
// segments, never a re-fetch or a different data shape. Used only to decide
// which segments in the flat, already-rendered list should show a
// speaker/timestamp header vs. flow as a continuation of the previous
// paragraph; it does not merge segments' underlying text/editability --
// each segment keeps its own id, textarea, and annotation dropdown exactly
// as before (see the transcript render block).
const PARAGRAPH_GAP_SECONDS = 2.0;
const PARAGRAPH_SOFT_MAX_CHARS = 500;
const PARAGRAPH_HARD_MAX_CHARS = 1000;
const SENTENCE_END_SUFFIXES = ['.', '!', '?', '…', '...', '."', '!"', '?"', ".'", "!'", "?'", '.”', '!”', '?”', '.’', '!’', '?’'];
const MID_SENTENCE_PUNCTUATION = [',', ';', ':', '-', '–', '—'];

const CONTINUATION_WORDS = new Set([
  'that', 'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how',
  'and', 'or', 'but', 'nor', 'for', 'yet', 'so',
  'because', 'since', 'as', 'although', 'though', 'even', 'if', 'unless',
  'while', 'whereas', 'whether',
  'of', 'in', 'to', 'for', 'with', 'on', 'at', 'from', 'by', 'about',
  'into', 'through', 'after', 'over', 'between', 'out', 'against',
  'during', 'without', 'before', 'under', 'around', 'among',
  'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'have', 'has', 'had', 'do', 'does', 'did',
  'can', 'could', 'should', 'would', 'may', 'might', 'must',
]);

const endsSentence = (text) => {
  const t = (text || '').trimEnd();
  return SENTENCE_END_SUFFIXES.some((suf) => t.endsWith(suf));
};

const isMidClause = (text) => {
  const t = (text || '').trimEnd();
  if (MID_SENTENCE_PUNCTUATION.some((p) => t.endsWith(p))) return true;
  return !endsSentence(t);
};

// items: flat array of {isHeader?, speaker, startTime, endTime, text,
// diarizationEnabled, ...rest}. Returns an array of paragraph groups:
// header items pass through unchanged as their own single-item group
// ({isHeader, label}); everything else is grouped into
// {speaker, start, text (merged), items (the original constituent items,
// in order, each keeping its own id/text/etc for editable rendering)}.
// speaker is '' whenever diarization was off for that stretch -- never a
// fabricated label. Read-only rendering can just use `text`; editable
// rendering renders each of `items` individually (its own textarea/
// annotate control) under one shared header, so per-segment edit
// granularity is completely unaffected by this grouping.
const groupIntoParagraphs = (items) => {
  const paragraphs = [];
  let current = null;

  const flush = () => { if (current) paragraphs.push(current); };

  items.forEach((item) => {
    if (item.isHeader) {
      flush();
      current = null;
      paragraphs.push(item);
      return;
    }
    const diarizationEnabled = Boolean(item.diarizationEnabled);
    const speaker = diarizationEnabled ? (item.speaker || '') : '';
    const text = (item.text || '').trim();
    const startTime = Number(item.startTime) || 0;
    const endTime = Number(item.endTime) || startTime;
    if (!text) return;

    let startsNew = false;
    if (!current) {
      startsNew = true;
    } else if (diarizationEnabled && speaker !== current.speaker) {
      startsNew = true;
    } else if ((startTime - current.endTime) >= PARAGRAPH_GAP_SECONDS) {
      startsNew = true;
    } else if (current.text.length >= PARAGRAPH_HARD_MAX_CHARS) {
      startsNew = true;
    } else if (current.text.length >= PARAGRAPH_SOFT_MAX_CHARS && endsSentence(current.text)) {
      startsNew = true;
    }

    if (startsNew) {
      flush();
      current = { speaker, start: item.start, endTime, text, items: [item] };
    } else {
      current.text = `${current.text} ${text}`;
      current.endTime = endTime;
      current.items.push(item);
    }
  });

  flush();
  return paragraphs;
};

const TRANSITION_STARTERS = [
  'all right', 'first', 'second', 'third', 'now', 'so', 'however',
  'well', 'then', 'therefore', 'in conclusion', 'finally', 'moreover',
  'furthermore', 'on the other hand', 'what about', 'let\'s turn',
  'in addition', 'for example', 'as a result'
];

const normalizeGenderSpeaker = (raw, gender = null) => {
  if (!raw) return gender === 'female' ? 'Female_1' : 'Male_1';
  if (raw.startsWith('Male_') || raw.startsWith('Female_')) return raw;
  if (gender === 'female' || gender === 'Female') {
    const match = raw.match(/\d+/);
    return `Female_${match ? match[0] : '1'}`;
  }
  const match = raw.match(/^Speaker\s*(\d+)$/i);
  if (match) {
    return `Male_${match[1]}`;
  }
  return raw;
};

const groupIntoEagleParagraphs = (items) => {
  const paragraphs = [];
  let current = null;

  const flush = () => { if (current) paragraphs.push(current); };

  (items || []).forEach((item) => {
    if (item.isHeader) return;
    const text = (item.text || '').trim();
    if (!text) return;
    const rawSpeaker = item.speaker || item.speaker_label || item.speaker_gender || 'Male_1';
    const speaker = normalizeGenderSpeaker(rawSpeaker, item.speaker_gender || item.gender);
    const startTime = Number(item.startTime !== undefined ? item.startTime : item.start_time) || 0;
    const endTime = Number(item.endTime !== undefined ? item.endTime : item.end_time) || startTime;

    let startsNew = false;
    if (!current) {
      startsNew = true;
    } else if (speaker !== current.speaker) {
      startsNew = true;
    } else {
      const prevText = current.text.trim();
      const gap = startTime - current.endTime;
      const endsPunct = endsSentence(prevText);
      const midClause = isMidClause(prevText);

      const words = text.split(/\s+/);
      const firstWord = words.length > 0 ? words[0].toLowerCase().replace(/^[^a-z0-9]+/i, '') : '';
      const isContinuationWord = CONTINUATION_WORDS.has(firstWord);
      const startsLower = text.length > 0 && text[0] === text[0].toLowerCase() && text[0] !== text[0].toUpperCase();

      // 1. Hard safety cap (oversized block prevention)
      if (prevText.length >= PARAGRAPH_HARD_MAX_CHARS) {
        startsNew = true;
      // 2. Significant audio pause/silence (natural topic or turn pause)
      } else if (gap >= 2.0) {
        startsNew = true;
      // 3. Incomplete grammatical clause or continuation word (never break mid-sentence)
      } else if (midClause || startsLower || isContinuationWord) {
        startsNew = false;
      // 4. Short interjections & brief rapid sentences (e.g. "Right.", "Mike.", "Yes.", "Okay."):
      //    If previous text is short (< 280 chars total) and within same speaker turn without a long pause:
      //    Keep merged into the speaker's surrounding sentence block!
      } else if (prevText.length < 280 && gap < 1.8) {
        startsNew = false;
      // 5. Preceding sentence is grammatically complete and has reached natural paragraph size
      } else if (endsPunct) {
        startsNew = true;
      // 6. Fallback for unpunctuated long discourse with noticeable pause
      } else if (prevText.length >= PARAGRAPH_SOFT_MAX_CHARS && gap >= 1.2) {
        startsNew = true;
      }
    }

    if (startsNew) {
      flush();
      current = { speaker, startTime, endTime, text, items: [item] };
    } else {
      current.text = `${current.text} ${text}`;
      current.endTime = Math.max(current.endTime, endTime);
      current.items.push(item);
    }
  });

  flush();
  return paragraphs;
};

/**
 * `embedded` is the ONLY thing Phase 3 changed about this component.
 *
 * When it is false -- the default, and what every existing caller gets --
 * this renders exactly as it always has: its own full-page background, its
 * own centred title, its own max-width column. Nothing about the
 * transcription, WebSocket, AI-assist or export behaviour is conditional on
 * it.
 *
 * When true, the studio is being mounted inside the task workspace, which
 * already supplies the page chrome and the task's identity. Only the
 * outer wrapper and the title block change; the "Start New Transcription"
 * control is kept, because clearing the screen is still useful there.
 */
export default function Dashboard({ embedded = false }) {
  const [sourceUrl, setSourceUrl] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState(null);
  // 0-100 while an upload's request body is actively being sent; null the
  // rest of the time (including during server-side processing, which has
  // its own status messages already) -- only meaningful for the file-upload
  // path, since a URL job's request body is tiny and finishes instantly.
  const [uploadProgress, setUploadProgress] = useState(null);
  const fileInputRef = useRef(null);

  // Optional per-job auth for login-walled source URLs (see
  // core.services.auth_helper on the backend). siteAuthCookiesFileContent
  // is the uploaded cookies.txt's text content, read client-side so it can
  // travel alongside the other JSON fields instead of requiring multipart.
  const [siteAuthText, setSiteAuthText] = useState('');
  const [siteAuthCookiesFileName, setSiteAuthCookiesFileName] = useState('');
  const [siteAuthCookiesFileContent, setSiteAuthCookiesFileContent] = useState('');

  // Opt-in only: walking a numbered-sequence query parameter (e.g.
  // ?seq_num=0) can have real side effects on the source site (confirmed:
  // some sites advance server-side queue state per fetch), so this is
  // never tried automatically -- see sequential_param_strategy.py.
  const [trySequential, setTrySequential] = useState(false);
  const [enableDiarization, setEnableDiarization] = useState(false);
  // '' means automatic (the pre-existing, only behaviour before this
  // control existed) -- never sent to the API at all in that case, so an
  // untouched job is byte-for-byte what it always was. A string, not a
  // number, because that's what a <select> naturally gives back; parsed
  // to Number only right before it goes on the wire.
  const [expectedSpeakers, setExpectedSpeakers] = useState('');
  const [enableWordSync, setEnableWordSync] = useState(true);
  const [jobStatus, setJobStatus] = useState('idle'); // idle, processing, completed, failed, cancelled
  // Mirrors jobStatus for code that reads it from inside a WebSocket event
  // handler (see connectWebSocket's onclose below): a socket opened without
  // an intervening await (e.g. during session restore, right after
  // setJobStatus('processing') with no await between them) would otherwise
  // close over whatever jobStatus was at the START of that render, not the
  // value just set -- a ref sidesteps that staleness entirely since
  // .current is always the latest value, independent of closure timing.
  const jobStatusRef = useRef(jobStatus);
  useEffect(() => { jobStatusRef.current = jobStatus; }, [jobStatus]);
  // Set true the instant Cancel is clicked so the button disables itself
  // immediately, before the backend has actually stopped the pipeline --
  // reset whenever a new job starts.
  const [cancelRequested, setCancelRequested] = useState(false);
  const [segments, setSegments] = useState([]);
  const [statusMessage, setStatusMessage] = useState('Ready');
  // {[rawSpeakerLabel]: displayName} for the single-job flow's current job
  // -- e.g. {"Speaker 0": "Interviewer"}. Applied at display time only
  // (see displayedSegments below); the underlying segment rows always keep
  // their raw label, exactly mirroring how the backend applies aliases for
  // exports (see export_formats.apply_speaker_aliases) so preview and
  // exported files are always in sync.
  const [speakerAliases, setSpeakerAliases] = useState({});
  // Whether the CURRENT single job actually had diarization enabled --
  // distinct from the `enableDiarization` checkbox above, which only
  // reflects the form for a job not yet submitted. Drives both whether any
  // speaker label is shown at all and whether a speaker change counts as a
  // paragraph boundary (see groupIntoParagraphs) -- set from the
  // loaded job's own enable_diarization, never inferred from whether a
  // segment's speaker happens to be non-empty (old jobs may still carry
  // stale "Speaker 0"/"Speaker 1" text from before the backend fix; this
  // flag is what correctly keeps that hidden too).
  const [currentJobDiarizationEnabled, setCurrentJobDiarizationEnabled] = useState(false);
  // null when closed; otherwise {title, paragraphs, downloadUrl} for the
  // DOCX export preview modal -- see handleExport/buildDocxPreviewParagraphs.
  const [docxPreview, setDocxPreview] = useState(null);
  // null when closed; otherwise metadata, items, and preview state for Eagle Final Submission Format
  const [eaglePreview, setEaglePreview] = useState(null);
  const [eagleCopied, setEagleCopied] = useState(false);

  const [languages, setLanguages] = useState([]);
  const [selectedLanguage, setSelectedLanguage] = useState(''); // '' = Auto Detect
  const [detectedLanguage, setDetectedLanguage] = useState(null);

  const [seriesInfo, setSeriesInfo] = useState(null); // discovery result awaiting a user choice
  const [seriesJobs, setSeriesJobs] = useState(null); // [{id, series_index, series_title, status}] once a series is running
  const [seriesId, setSeriesId] = useState(null);
  const [seriesActiveLanguage, setSeriesActiveLanguage] = useState({}); // {[jobId]: 'original' | langCode} -- which variant Export acts on, per item
  const seriesPollRef = useRef(null);

  const [currentJobId, setCurrentJobId] = useState(null);
  const [translationLanguages, setTranslationLanguages] = useState([]);
  const [translateTarget, setTranslateTarget] = useState('');
  const [translations, setTranslations] = useState([]); // [{id, language, status, segments}], one per cached translation
  // null until Academic Format has been triggered at least once for the
  // current job; then {id, subject, subject_confidence, status, segments,
  // error_message} -- one per job (no language-like dimension), mirroring
  // `translations` but singular. See handleAcademicFormat/
  // core.services.academic on the backend.
  const [academicFormat, setAcademicFormat] = useState(null);
  // AI Assist. `aiRevisions` are summaries (no segments -- see
  // AIRevisionSummarySerializer), which is all the version switcher needs;
  // `aiRevision` is the one currently loaded in full for the diff review.
  // activeView carries 'ai:<revisionId>' for a revision, so one piece of
  // state still answers "which version am I looking at" for every variant.
  const [aiCapabilities, setAiCapabilities] = useState(null);
  const [aiRevisions, setAiRevisions] = useState([]);
  const [aiRevision, setAiRevision] = useState(null);
  const [showAIPanel, setShowAIPanel] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState('');
  const [documentMetadata, setDocumentMetadata] = useState({});
  const [activeView, setActiveView] = useState('original'); // 'original' | 'academic' | 'ai:<revisionId>' | a translated language code
  const translationPollRef = useRef(null);
  const academicFormatPollRef = useRef(null);
  const aiRevisionPollRef = useRef(null);

  const [projectId, setProjectId] = useState(null);
  const pollIntervalRef = useRef(null);
  const wsRef = useRef(null);

  // Use Vite proxy during development so we avoid CORS and host-specific issues
  const API_BASE = '/api/v1';
  const WS_BASE = '/ws/transcription';

  // Forgets everything AI-Assist about the job that's leaving the screen.
  // Called from each of the reset paths that already clear
  // translations/academicFormat -- a stale revision left behind would show
  // another job's diff, and a live poll would keep hitting a revision nobody
  // is looking at any more. Purely frontend: an in-flight revision keeps
  // running server-side, exactly like a transcription does.
  const resetAIAssistState = () => {
    if (aiRevisionPollRef.current) clearInterval(aiRevisionPollRef.current);
    setAiRevisions([]);
    setAiRevision(null);
    setShowAIPanel(false);
    setAiBusy(false);
    setAiError('');
    setDocumentMetadata({});
  };

  // 1. Initial Setup: Fetch or Create default Project
  //
  // Retries with a short backoff instead of giving up on the first failed
  // fetch -- during local development the backend's autoreloader restarts
  // the whole process on every file save, and a page that happens to mount
  // during one of those windows would otherwise be stuck with projectId
  // permanently null (never retried), silently failing every later "Run
  // Transcription" click even once the backend is back up.
  const INIT_PROJECT_RETRY_DELAYS_MS = [500, 1000, 2000, 4000];

  useEffect(() => {
    let cancelled = false;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    async function initProject(attempt = 0) {
      try {
        const projects = await api.get('/projects/');
        if (cancelled) return;
        if (projects.length > 0) {
          setProjectId(projects[0].id);
        } else {
          // Create default project
          const newProj = await api.post('/projects/', { name: 'Default Project', description: 'Default workspace' });
          if (!cancelled) setProjectId(newProj.id);
        }
        return;
      } catch (err) {
        if (cancelled) return;
        if (attempt < INIT_PROJECT_RETRY_DELAYS_MS.length) {
          const delay = INIT_PROJECT_RETRY_DELAYS_MS[attempt];
          console.warn(`Backend API not reachable yet (attempt ${attempt + 1}), retrying in ${delay}ms...`, err);
          await sleep(delay);
          if (!cancelled) return initProject(attempt + 1);
          return;
        }
        console.error('Backend API server still not reachable after retries. Please verify the Django backend is running and refresh.', err);
      }
    }
    initProject();

    async function loadLanguages() {
      try {
        setLanguages(await api.get('/languages/'));
      } catch (err) {
        console.error('Failed to load supported languages.', err);
      }
    }
    loadLanguages();

    async function loadTranslationLanguages() {
      try {
        setTranslationLanguages(await api.get('/translation-languages/'));
      } catch (err) {
        console.error('Failed to load translation languages.', err);
      }
    }
    loadTranslationLanguages();

    // What AI Assist is permitted to do, as reported by the server. The
    // frontend never decides this for itself -- if this fetch fails the
    // panel simply shows AI as unavailable, which is the safe direction, and
    // every dispatch is re-checked server-side regardless.
    async function loadAiCapabilities() {
      try {
        setAiCapabilities(await api.get('/ai-capabilities/'));
      } catch (err) {
        console.error('Failed to load AI capabilities.', err);
      }
    }
    loadAiCapabilities();

    return () => {
      cancelled = true;
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      if (seriesPollRef.current) clearInterval(seriesPollRef.current);
      if (translationPollRef.current) clearInterval(translationPollRef.current);
      if (academicFormatPollRef.current) clearInterval(academicFormatPollRef.current);
      if (aiRevisionPollRef.current) clearInterval(aiRevisionPollRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // 2. Session recovery: on mount, restore whichever job/series was last
  // persisted (see saveSession calls in runTranscription/
  // runSeriesTranscription) -- the backend is the source of truth, this
  // only rebuilds the UI from what it reports. Runs independently of
  // initProject (restoring doesn't need projectId, only creating a NEW job
  // does), so a slow/retrying project init never delays getting the
  // in-progress transcript back on screen.
  useEffect(() => {
    const session = loadSession();
    if (!session) return;

    if (session.type === 'single' && session.jobId) {
      restoreSingleJob(session);
    } else if (session.type === 'series' && session.seriesId && Array.isArray(session.jobIds) && session.jobIds.length > 0) {
      restoreSeries(session);
    } else {
      clearSession();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const restoreSingleJob = async (session) => {
    try {
      const job = await api.get(`/transcriptions/${session.jobId}/`);

      setCurrentJobId(job.id);
      setSourceUrl(session.sourceUrl || '');
      if (session.language) setSelectedLanguage(session.language);
      setSegments((job.segments || []).map(s => ({
        id: s.id, speaker: s.speaker_label, start: formatTime(s.start_time), text: s.text,
        startTime: s.start_time, endTime: s.end_time,
      })));
      setSpeakerAliases(job.speaker_aliases || {});
      setCurrentJobDiarizationEnabled(Boolean(job.enable_diarization));
      setTranslations(job.translations || []);
      setAcademicFormat(job.academic_format || null);
      // Summaries only -- the job payload's ai_revisions carry no segments, so
      // the tabs can be rebuilt on recovery without loading every diff. The
      // segments arrive when a tab is actually selected.
      setAiRevisions(job.ai_revisions || []);
      setDocumentMetadata(job.document_metadata || {});
      if (job.detected_language) {
        setDetectedLanguage({ code: job.detected_language, confidence: job.language_confidence });
      }

      if (ACTIVE_JOB_STATUSES.includes(job.status)) {
        setJobStatus('processing');
        setStatusMessage('Reconnected to in-progress transcription...');
        connectWebSocket(job.id); // falls back to HTTP polling on its own if the socket can't connect
      } else if (job.status === 'completed') {
        setJobStatus('completed');
        setStatusMessage('Transcription successfully completed.');
      } else if (job.status === 'failed') {
        setJobStatus('failed');
        setStatusMessage(`Failed: ${job.error_message || 'Unknown error'}`);
      } else if (job.status === 'cancelled') {
        setJobStatus('cancelled');
        setStatusMessage('Transcription cancelled.');
      }
    } catch (err) {
      if (err instanceof ApiError && err.status !== 0) {
        // Job no longer exists server-side (deleted, or a stale/foreign id) --
        // drop the dead pointer rather than retrying it forever.
        clearSession();
        return;
      }
      console.warn('Failed to restore the previous session (job).', err);
    }
  };

  const restoreSeries = async (session) => {
    try {
      const results = await Promise.all(
        session.jobIds.map(id => api.get(`/transcriptions/${id}/`).catch(() => null))
      );
      const jobs = results.filter(Boolean);
      if (jobs.length === 0) {
        clearSession();
        return;
      }

      setSourceUrl(session.sourceUrl || '');
      if (session.language) setSelectedLanguage(session.language);
      setSeriesId(session.seriesId);
      setSeriesJobs(jobs);
      setSegments(buildAggregatedSeriesSegments(jobs));
      // If some (but not all) items were deleted before the page reloaded,
      // keep the persisted list in sync with what's actually still there.
      if (jobs.length !== session.jobIds.length) {
        saveSession({ ...session, jobIds: jobs.map(j => j.id) });
      }

      const stillActive = jobs.some(j => ACTIVE_JOB_STATUSES.includes(j.status));
      if (stillActive) {
        setJobStatus('processing');
        setStatusMessage('Reconnected to in-progress series transcription...');
        startSeriesPolling(jobs.map(j => j.id));
      } else {
        setJobStatus('completed');
        setStatusMessage('Series transcription restored.');
      }
    } catch (err) {
      console.warn('Failed to restore the previous session (series).', err);
    }
  };

  // Laptop sleep/wake, backgrounded tabs, and temporary network drops don't
  // reliably fire a WebSocket close event right away -- the socket can sit
  // in a dead state for a while (or the OS suspends JS execution entirely
  // during sleep) before onclose/onerror ever runs. Re-check on the signals
  // that reliably fire the moment the page is usable again: tab regains
  // focus/visibility, or the network reports it's back online. Cheap no-op
  // when there's nothing active.
  useEffect(() => {
    const reconcileAfterWake = () => {
      if (document.visibilityState !== 'visible') return;
      if (jobStatus !== 'processing') return;
      if (currentJobId) {
        reconcileActiveJob();
      } else if (seriesId && seriesJobs && seriesJobs.length > 0) {
        startSeriesPolling(seriesJobs.map(j => j.id));
      }
    };
    document.addEventListener('visibilitychange', reconcileAfterWake);
    window.addEventListener('online', reconcileAfterWake);
    return () => {
      document.removeEventListener('visibilitychange', reconcileAfterWake);
      window.removeEventListener('online', reconcileAfterWake);
    };
  }, [jobStatus, currentJobId, seriesId, seriesJobs]);

  // Pulls the latest state over plain HTTP regardless of WebSocket status --
  // guarantees segments produced while the tab was asleep/backgrounded are
  // never missed -- then makes sure a live connection exists for whatever's
  // still in progress (reconnects the socket only if it isn't already
  // open/connecting, so this never tears down a perfectly healthy one).
  const reconcileActiveJob = async () => {
    if (!currentJobId) return;
    try {
      const job = await api.get(`/transcriptions/${currentJobId}/`);
      setSegments((job.segments || []).map(s => ({
        id: s.id, speaker: s.speaker_label, start: formatTime(s.start_time), text: s.text,
        startTime: s.start_time, endTime: s.end_time,
      })));
      setSpeakerAliases(job.speaker_aliases || {});
      setCurrentJobDiarizationEnabled(Boolean(job.enable_diarization));
      if (job.detected_language) {
        setDetectedLanguage({ code: job.detected_language, confidence: job.language_confidence });
      }
      if (job.status === 'completed') {
        setJobStatus('completed');
        setStatusMessage('Transcription successfully completed.');
        return;
      } else if (job.status === 'failed') {
        setJobStatus('failed');
        setStatusMessage(`Failed: ${job.error_message || 'Unknown error'}`);
        return;
      } else if (job.status === 'cancelled') {
        setJobStatus('cancelled');
        setStatusMessage('Transcription cancelled.');
        return;
      }
    } catch (err) {
      console.warn('Reconcile-after-wake fetch failed; will retry on the next wake signal.', err);
      return;
    }
    const wsOpen = wsRef.current && (
      wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING
    );
    if (!wsOpen) {
      connectWebSocket(currentJobId);
    }
  };

  // Shared by drag-drop and the click-to-browse picker so both paths get
  // identical validation -- rejects an obviously-wrong file client-side
  // (extension not in the supported set, or a 0-byte file) rather than
  // letting it reach the backend and fail minutes later during ffmpeg
  // extraction. Also disallowed while a job is already being submitted/
  // processed, so a stray drop or file-picker selection during that window
  // can't silently swap out the file an in-flight upload is using.
  const validateAndSetFile = (candidate) => {
    if (!candidate) return;
    if (jobStatus === 'processing') {
      alert('A transcription is already running. Cancel it or wait for it to finish before starting another.');
      return;
    }
    const name = candidate.name || '';
    const dotIndex = name.lastIndexOf('.');
    const ext = dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : '';
    if (!SUPPORTED_UPLOAD_EXTENSIONS.includes(ext)) {
      alert(
        `Unsupported file type "${ext || 'unknown'}".\n\nSupported formats: ${SUPPORTED_UPLOAD_EXTENSIONS.join(', ')}`
      );
      return;
    }
    if (candidate.size === 0) {
      alert('That file appears to be empty (0 bytes). Please choose a different file.');
      return;
    }
    setFile(candidate);
    // A freshly picked/dropped file replaces any pasted URL rather than
    // silently coexisting with it -- handleRunClick would otherwise use
    // the file and never explain why the URL was ignored.
    setSourceUrl('');
    setStatusMessage(`Loaded local file: ${candidate.name} (${formatFileSize(candidate.size)})`);
  };

  // Drag and drop event handlers
  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  // Click-to-browse: the dropzone itself is clickable (see the onClick on
  // its <section> below) and forwards to this hidden native file input,
  // since a styled drop-zone can't be a real file picker on its own.
  const handleFileInputChange = (e) => {
    const selected = e.target.files && e.target.files[0];
    if (selected) validateAndSetFile(selected);
    // Reset so choosing the exact same file again still fires onChange
    // (browsers don't emit a change event if the value doesn't change).
    e.target.value = '';
  };

  const handleDropzoneClick = () => {
    if (jobStatus === 'processing') return;
    fileInputRef.current?.click();
  };

  // Reads an uploaded cookies.txt client-side so its content can travel
  // alongside the other request fields as a plain string, rather than
  // requiring the whole job-creation request to become multipart just for
  // this one optional field.
  const handleAuthCookiesFile = (e) => {
    const uploaded = e.target.files && e.target.files[0];
    if (!uploaded) return;
    setSiteAuthCookiesFileName(uploaded.name);
    const reader = new FileReader();
    reader.onload = () => setSiteAuthCookiesFileContent(reader.result || '');
    reader.readAsText(uploaded);
  };

  // Included in every discovery/job-creation request; parsed server-side
  // (core.services.auth_helper) into whatever HTTP headers the site needs.
  // Both empty is the common case and simply means "no auth", unchanged
  // from before this feature existed.
  const siteAuthFields = () => ({
    site_auth_text: siteAuthText || undefined,
    site_auth_cookies_file: siteAuthCookiesFileContent || undefined,
  });

  // Entry point for the "Run Transcription" button. For URL submissions,
  // checks whether the page is part of a detectable series/playlist first
  // and, if so, asks the user how much to transcribe before doing anything.
  // File uploads and non-series URLs proceed exactly as before.
  const handleRunClick = async () => {
    if (!file && !sourceUrl) {
      alert("Please upload an audio/video file or paste a public media URL.");
      return;
    }
    if (file) {
      runTranscription();
      return;
    }

    setStatusMessage('Checking for related pages (playlist/series)...');
    // Discovery runs in a background thread on the server (see
    // DiscoverSeriesView) and is polled here rather than awaited on one long
    // request, so a slow site never ties up a server worker. The UX is
    // unchanged either way: same status message, same graceful fallback to
    // the normal single-page flow on any error or timeout.
    try {
      const { task_id } = await api.post('/discover-series/', {
        source_url: sourceUrl, try_sequential: trySequential, ...siteAuthFields(),
      });
      const result = await pollDiscoveryTask(task_id);
      if (result && result.is_series && result.items && result.items.length > 1) {
        setSeriesInfo(result);
        setStatusMessage('This page appears to be part of a series.');
        return;
      }
    } catch (err) {
      console.warn('Series discovery skipped (slow or unavailable), proceeding with a single page.', err);
    }
    runTranscription();
  };

  // Polls a DiscoverSeriesView task until it completes or fails. Bounded
  // well above the slowest known discovery strategy (yt-dlp's own playlist
  // check allows up to 90s server-side) so genuine playlist detection on
  // platforms like YouTube gets a real chance to finish now that it no
  // longer blocks anything else while it runs.
  const DISCOVERY_POLL_INTERVAL_MS = 1500;
  const DISCOVERY_POLL_TIMEOUT_MS = 100000;

  const pollDiscoveryTask = (taskId) => {
    return new Promise((resolve, reject) => {
      const startedAt = Date.now();
      const interval = setInterval(async () => {
        if (Date.now() - startedAt > DISCOVERY_POLL_TIMEOUT_MS) {
          clearInterval(interval);
          reject(new Error('Discovery timed out'));
          return;
        }
        try {
          const data = await api.get(`/discover-series/${taskId}/`);
          if (data.status === 'completed') {
            clearInterval(interval);
            resolve(data.result);
          } else if (data.status === 'failed') {
            clearInterval(interval);
            reject(new Error(data.error || 'Discovery failed'));
          }
          // else still pending -- keep polling
        } catch (err) {
          clearInterval(interval);
          reject(err);
        }
      }, DISCOVERY_POLL_INTERVAL_MS);
    });
  };

  const RELATED_PAGES_COUNT = 5;

  const chooseCurrentPageOnly = () => {
    setSeriesInfo(null);
    runTranscription();
  };

  const chooseRelatedPages = () => {
    if (!seriesInfo) return;
    const start = seriesInfo.current_index ?? 0;
    const subset = seriesInfo.items.slice(start, start + RELATED_PAGES_COUNT);
    setSeriesInfo(null);
    runSeriesTranscription(subset);
  };

  const chooseEntireSeries = () => {
    if (!seriesInfo) return;
    const items = seriesInfo.items;
    setSeriesInfo(null);
    runSeriesTranscription(items);
  };

  // Tears down every background connection from whatever was previously
  // running (either mode) -- a WebSocket, either polling interval, or a
  // translation poll. Called before starting anything new so a leftover
  // interval from a PREVIOUS run (e.g. a series poll still ticking every
  // 2s) can never keep overwriting freshly-started state with stale data,
  // which is what made an old series/job appear to linger "underneath" a
  // newly started one.
  const stopAllActiveConnections = () => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    if (seriesPollRef.current) clearInterval(seriesPollRef.current);
    if (translationPollRef.current) clearInterval(translationPollRef.current);
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  };

  // Creates one job per item (ordered, sequential, resumable server-side)
  // and polls the whole group's status.
  const runSeriesTranscription = async (items) => {
    stopAllActiveConnections();
    setSegments([]);
    setSeriesJobs(null);
    setSeriesId(null);
    setSeriesActiveLanguage({});
    // Clear the single-job mode's state too -- starting a series shouldn't
    // leave a previous single job's translations/export controls dangling
    // around alongside the new series' own.
    setCurrentJobId(null);
    setTranslations([]);
    setAcademicFormat(null);
    resetAIAssistState();
    setActiveView('original');
    setJobStatus('processing');
    setCancelRequested(false);
    setStatusMessage(`Submitting series of ${items.length} item(s)...`);
    setDetectedLanguage(null);

    if (!projectId) {
      setJobStatus('failed');
      setStatusMessage('Backend project initialization failed. Please verify the Django API is reachable.');
      return;
    }

    try {
      const data = await api.post('/transcriptions/create_series/', {
        project: projectId, items, language: selectedLanguage, ...siteAuthFields(),
      });
      setSeriesJobs(data.jobs.map(j => ({ ...j })));
      setSeriesId(data.series_id);
      setStatusMessage(`Series submitted: ${data.jobs.length} item(s) queued, processing in order...`);
      saveSession({
        type: 'series', seriesId: data.series_id, jobIds: data.jobs.map(j => j.id),
        sourceUrl: sourceUrl || '', language: selectedLanguage, savedAt: Date.now(),
      });
      startSeriesPolling(data.jobs.map(j => j.id));
    } catch (err) {
      console.error(err);
      setJobStatus('failed');
      setStatusMessage('Backend error creating series. Please check the server logs for details.');
    }
  };

  // Shared by live polling and post-delete refresh, so both build the
  // preview the exact same way -- see startSeriesPolling's original
  // comment for why this aggregation exists at all.
  const buildAggregatedSeriesSegments = (jobsArray) => jobsArray
    .slice()
    .sort((a, b) => (a.series_index ?? 0) - (b.series_index ?? 0))
    .flatMap((job) => {
      // Each series item carries its own independent speaker_aliases (see
      // Part 4's "no merging" principle) -- applied here at display time,
      // same substitution the backend applies for exports, so a renamed
      // speaker shows correctly in the aggregated preview too.
      const jobAliases = job.speaker_aliases || {};
      const segs = (job.segments || []).map(s => ({
        id: s.id, jobId: job.id,
        speaker: jobAliases[s.speaker_label] || s.speaker_label,
        start: formatTime(s.start_time), text: s.text,
        startTime: s.start_time, endTime: s.end_time,
        diarizationEnabled: Boolean(job.enable_diarization),
      }));
      if (segs.length === 0) return [];
      const header = { isHeader: true, label: job.series_title || `Item ${(job.series_index ?? 0) + 1}` };
      return [header, ...segs];
    });

  const startSeriesPolling = (jobIds) => {
    if (seriesPollRef.current) clearInterval(seriesPollRef.current);
    seriesPollRef.current = setInterval(async () => {
      try {
        const results = await Promise.all(
          jobIds.map(id => api.get(`/transcriptions/${id}/`).catch(() => null))
        );
        const updated = results.filter(Boolean);
        setSeriesJobs(updated);

        // Each job's response already includes its own segments (same
        // TranscriptionJobSerializer used everywhere) -- the status
        // checklist above only showed completion state, never the actual
        // transcribed text. Aggregate every item's segments, in series
        // order, into the same {speaker, start, text} shape the live
        // preview already renders for a single job, with a header between
        // items so it's clear which clip each line came from. Shows text
        // for any item that has produced segments so far, not just fully
        // completed ones, so the preview grows live as each item finishes
        // -- same as the single-job WebSocket flow.
        setSegments(buildAggregatedSeriesSegments(updated));

        const allDone = updated.length > 0 && updated.every(
          j => j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled'
        );
        if (allDone) {
          setJobStatus('completed');
          setStatusMessage('Series transcription finished.');
          clearInterval(seriesPollRef.current);
        }
      } catch (err) {
        console.error('Series polling error:', err);
      }
    }, 2000);
  };

  // Trigger real backend transcription or fall back to mock
  // fetch() has no reliable, widely-supported way to report upload
  // progress for a request body -- XMLHttpRequest does (xhr.upload.onprogress),
  // so the file-upload path specifically uses it instead of fetch. URL-based
  // jobs keep using plain fetch below (unchanged): their request body is a
  // few bytes of JSON, finishing instantly, so there's nothing meaningful
  // to show progress for.
  const uploadJobWithProgress = (formData) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}/transcriptions/`);
      // uploadJobWithProgress uses XMLHttpRequest instead of api.post (see
      // the comment above) specifically for upload-progress events, which
      // fetch cannot report -- so it has to attach auth itself rather than
      // going through the shared client. Same headers api/client.js's
      // request() would send for an unsafe method: the bearer token, plus
      // the CSRF header for the session-cookie fallback path.
      const token = getToken();
      if (token) xhr.setRequestHeader('Authorization', `Token ${token}`);
      const csrf = csrfToken();
      if (csrf) xhr.setRequestHeader('X-CSRFToken', csrf);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          setUploadProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        setUploadProgress(null);
        let parsed = null;
        try { parsed = JSON.parse(xhr.responseText); } catch (err) { /* non-JSON error body -- handled by the ok check below */ }
        resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, json: parsed, text: xhr.responseText });
      };
      xhr.onerror = () => {
        setUploadProgress(null);
        reject(new Error('Network error during upload -- the connection may have been interrupted.'));
      };
      xhr.onabort = () => {
        setUploadProgress(null);
        reject(new Error('Upload cancelled.'));
      };
      xhr.send(formData);
    });
  };

  const runTranscription = async () => {
    stopAllActiveConnections();
    setSegments([]);
    setJobStatus('processing');
    setCancelRequested(false);
    setStatusMessage('Submitting transcription job...');
    setDetectedLanguage(null);
    setCurrentJobId(null);
    setTranslations([]);
    setAcademicFormat(null);
    resetAIAssistState();
    setActiveView('original');
    setSpeakerAliases({});
    setCurrentJobDiarizationEnabled(enableDiarization);
    // Clear the series mode's state too -- starting a single job shouldn't
    // leave a previous series' progress panel rendered underneath it.
    setSeriesJobs(null);
    setSeriesId(null);
    setSeriesActiveLanguage({});

    if (!projectId) {
      setJobStatus('failed');
      setStatusMessage('Backend project initialization failed. Please verify the Django API is reachable.');
      return;
    }

    try {
      let job;

      if (file) {
        const body = new FormData();
        body.append('project', projectId);
        body.append('input_source', 'upload');
        body.append('file', file);
        body.append('source_url', '');
        body.append('language', selectedLanguage);
        body.append('enable_diarization', enableDiarization);
        if (enableDiarization && expectedSpeakers) {
          body.append('expected_speakers', expectedSpeakers);
        }

        setStatusMessage(`Uploading ${file.name}...`);
        setUploadProgress(0);
        const result = await uploadJobWithProgress(body);
        if (!result.ok) {
          console.error('Backend create job error:', result.status, result.text);
          throw new Error('Failed to create job on backend.');
        }
        job = result.json;
      } else {
        job = await api.post('/transcriptions/', {
          project: projectId,
          input_source: 'url',
          source_url: sourceUrl || '',
          file_path: '',
          language: selectedLanguage,
          enable_diarization: enableDiarization,
          expected_speakers: (enableDiarization && expectedSpeakers) ? Number(expectedSpeakers) : null,
          ...siteAuthFields(),
        });
      }

      setCurrentJobId(job.id);
      setStatusMessage('Job created. Initializing connections...');
      saveSession({
        type: 'single', jobId: job.id, sourceUrl: sourceUrl || '', language: selectedLanguage,
        savedAt: Date.now(),
      });

      // Connect WS or fallback to Polling
      connectWebSocket(job.id);

    } catch (err) {
      console.error(err);
      setUploadProgress(null);
      setJobStatus('failed');
      setStatusMessage(file ? `Upload failed: ${err.message || 'please check the server logs for details.'}` : 'Backend error. Please check the server logs for details.');
    }
  };

  // Connect Django Channels WebSocket
  const connectWebSocket = (jobId) => {
    try {
      const wsUrl = `${WS_BASE}/${jobId}/`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('Connected to transcription WebSocket');
        setStatusMessage('WebSocket connected. Processing...');
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'status_update') {
          setStatusMessage(msg.message);
          if (msg.status === 'completed') {
            setJobStatus('completed');
            // Fetch final canonical segments with latest speaker labels
            api.get(`/transcriptions/${jobId}/segments/`)
              .then(data => {
                if (Array.isArray(data) && data.length > 0) {
                  setSegments(data.map(s => ({
                    id: s.id,
                    speaker: s.speaker_label,
                    start: formatTime(s.start_time),
                    text: s.text,
                    startTime: s.start_time,
                    endTime: s.end_time,
                  })));
                }
              })
              .catch(err => console.warn('Failed to fetch final segments:', err));
            ws.close();
          } else if (msg.status === 'failed') {
            setJobStatus('failed');
            ws.close();
          } else if (msg.status === 'cancelled') {
            setJobStatus('cancelled');
            ws.close();
          }
        } else if (msg.type === 'segments_updated') {
          if (Array.isArray(msg.segments)) {
            setSegments(msg.segments.map(s => ({
              id: s.id,
              speaker: s.speaker_label,
              start: formatTime(s.start_time),
              text: s.text,
              startTime: s.start_time,
              endTime: s.end_time,
            })));
          }
        } else if (msg.type === 'segment_added') {
          const seg = {
            id: msg.segment.id,
            speaker: msg.segment.speaker_label,
            start: formatTime(msg.segment.start_time),
            text: msg.segment.text,
            startTime: msg.segment.start_time,
            endTime: msg.segment.end_time,
          };
          setSegments(prev => [...prev, seg]);
          if (enableWordSync) {
            console.log(`[WORD SYNC EVENT] Typing: ${seg.speaker}: ${seg.text}`);
          }
        } else if (msg.type === 'language_detected') {
          setDetectedLanguage({ code: msg.detected_language, confidence: msg.language_confidence });
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket error, falling back to HTTP polling...', err);
        startPolling(jobId);
      };

      ws.onclose = (event) => {
        if (event.code !== 1000 && jobStatusRef.current === 'processing') {
          console.warn('WebSocket closed abnormally, falling back to HTTP polling...');
          startPolling(jobId);
        }
      };

    } catch (e) {
      console.warn('Failed to start WebSocket, falling back to HTTP polling...', e);
      startPolling(jobId);
    }
  };

  // HTTP Polling Fallback
  const startPolling = (jobId) => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    
    pollIntervalRef.current = setInterval(async () => {
      try {
        // Fetch Job Details
        const job = await api.get(`/transcriptions/${jobId}/`);

        if (job.status === 'downloading') {
          setStatusMessage('Downloading media and extracting audio (Polling)...');
        } else if (job.status === 'diarizing') {
          setStatusMessage('Identifying speakers (this can take a few minutes for longer audio) (Polling)...');
        } else if (job.status === 'transcribing') {
          setStatusMessage('Transcribing speech to text (Polling)...');
        }

        if (job.detected_language) {
          setDetectedLanguage({ code: job.detected_language, confidence: job.language_confidence });
        }
        setSpeakerAliases(job.speaker_aliases || {});
        setCurrentJobDiarizationEnabled(Boolean(job.enable_diarization));

        // Fetch segments
        const segs = await api.get(`/transcriptions/${jobId}/segments/`);
        const formatted = segs.map(s => ({
          id: s.id,
          speaker: s.speaker_label,
          start: formatTime(s.start_time),
          text: s.text,
          startTime: s.start_time,
          endTime: s.end_time,
        }));
        setSegments(formatted);

        if (job.status === 'completed') {
          setJobStatus('completed');
          setStatusMessage('Transcription successfully completed.');
          clearInterval(pollIntervalRef.current);
        } else if (job.status === 'failed') {
          setJobStatus('failed');
          setStatusMessage(`Failed: ${job.error_message || 'Unknown error'}`);
          clearInterval(pollIntervalRef.current);
        } else if (job.status === 'cancelled') {
          setJobStatus('cancelled');
          setStatusMessage('Transcription cancelled.');
          clearInterval(pollIntervalRef.current);
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 1500);
  };

  // Helper: format float seconds to MM:SS
  const formatTime = (secs) => {
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };



  // Builds the same paragraph structure the backend's to_docx() renders
  // (see export_formats.py) entirely from data already in memory -- no
  // extra network round-trip needed, since every case (single job,
  // series item, translation) already has its segments loaded for the
  // live preview panel. Mirrors displayedSegments' own enrichment
  // (aliases applied, diarizationEnabled attached) so what's previewed
  // is guaranteed to match what the real export actually produces.
  const getEnrichedExportItems = (jobId, language) => {
    let items;
    if (language === 'academic') {
      items = (academicFormat?.segments || []).map(s => ({
        id: s.id,
        speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        text: s.text,
        start: formatTime(s.start_time),
        startTime: s.start_time,
        endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }));
    } else if (typeof language === 'string' && language.startsWith('ai:')) {
      const revisionId = language.slice(3);
      const revision = aiRevision?.id === revisionId ? aiRevision : null;
      items = (revision?.segments || []).map(s => ({
        id: s.id,
        speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        text: s.effective_text !== undefined
          ? s.effective_text
          : (s.changed && s.accepted ? s.processed_text : s.original_text),
        start: formatTime(s.start_time),
        startTime: s.start_time,
        endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }));
    } else if (language) {
      const translation = translations.find(t => t.language === language);
      items = (translation?.segments || []).map(s => ({
        id: s.id,
        speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        text: s.text,
        start: formatTime(s.start_time),
        startTime: s.start_time,
        endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }));
    } else if (seriesJobs) {
      items = segments.filter(s => !s.isHeader && s.jobId === jobId).map(s => ({
        ...s,
        speaker: (speakerAliases && (speakerAliases[s.speaker] || speakerAliases[s.speaker_label])) || s.speaker || s.speaker_label || 'Speaker 1',
        startTime: s.startTime !== undefined ? s.startTime : s.start_time,
        endTime: s.endTime !== undefined ? s.endTime : s.end_time,
      }));
    } else {
      items = segments.map(s => ({
        ...s,
        speaker: (speakerAliases && (speakerAliases[s.speaker] || speakerAliases[s.speaker_label])) || s.speaker || s.speaker_label || 'Speaker 1',
        startTime: s.startTime !== undefined ? s.startTime : s.start_time,
        endTime: s.endTime !== undefined ? s.endTime : s.end_time,
        diarizationEnabled: s.diarizationEnabled !== undefined ? s.diarizationEnabled : currentJobDiarizationEnabled,
      }));
    }
    return items;
  };

  const buildDocxPreviewParagraphs = (jobId, language) => {
    const items = getEnrichedExportItems(jobId, language);
    return groupIntoParagraphs(items);
  };

  const formatEagleTimestamp = (sec) => {
    const msTotal = Math.max(0, Math.round((sec || 0) * 1000));
    const hours = Math.floor(msTotal / 3600000);
    const rem1 = msTotal % 3600000;
    const mins = Math.floor(rem1 / 60000);
    const rem2 = rem1 % 60000;
    const secs = Math.floor(rem2 / 1000);
    const ms = rem2 % 1000;
    return `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
  };

  const generateEagleTranscriptionText = (preview) => {
    const { title, client, transcriber, date, items } = preview;
    const lastEnd = items.length > 0 ? Math.max(...items.map(s => s.endTime !== undefined ? s.endTime : (s.end_time || 0))) : 0;
    const mins = Math.floor(lastEnd / 60);
    const secs = Math.floor(lastEnd % 60);
    const audioLen = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')} Minutes`;

    let out = `Transcription Job: ${title || 'Transcription Job'}\n` +
              `Client: ${client || 'Eagle Web'}\n` +
              `Transcribed by: ${transcriber || 'Transcriber'}\n` +
              `Date: ${date || ''}\n` +
              `Audio Length: ${audioLen}\n\n`;

    const paragraphs = groupIntoEagleParagraphs(items);
    for (const para of paragraphs) {
      const rawSpk = normalizeGenderSpeaker(para.speaker || para.speaker_label, para.speaker_gender);
      const spk = (speakerAliases && (speakerAliases[rawSpk] || speakerAliases[para.speaker_label])) || rawSpk;
      const startMs = formatEagleTimestamp(para.startTime !== undefined ? para.startTime : (para.start_time || 0));
      const endMs = formatEagleTimestamp(para.endTime !== undefined ? para.endTime : (para.end_time || 0));
      out += `${spk}:\n[${startMs} --> ${endMs}]\n${(para.text || '').trim()}\n\n`;
    }
    out += `[END OF RECORDING --> ${formatEagleTimestamp(lastEnd)}]\n`;
    return out;
  };

  const generateEagleTranslationText = (preview) => {
    const { title, client, translator, date, sourceLang, targetLang, items } = preview;
    const paragraphs = groupIntoParagraphs(items);
    const combinedText = paragraphs.map(p => p.text.trim()).filter(Boolean).join('\n\n');
    const words = (combinedText.match(/\S+/g) || []).length;

    return `Translation Job: ${title || 'Translation Job'}\n` +
           `Client: ${client || 'Eagle Web'}\n` +
           `Translated By: ${translator || 'Translator'}\n` +
           `Date: ${date || ''}\n\n` +
           `Source Language: ${sourceLang || 'English'}\n` +
           `Target Language: ${targetLang || 'French'}\n\n` +
           `Translation :\n\n` +
           `${combinedText}\n\n` +
           `(Number Of Words: ${words.toLocaleString()})\n`;
  };

  // Export Trigger from API. Exports the version currently being viewed --
  // the original transcript, or a specific cached translation -- using the
  // exact same timestamps/speakers either way.
  const handleExport = (format, jobId = currentJobId, language = undefined) => {
    if (!jobId) {
      alert("No transcribed data exists to export.");
      return;
    }
    const effectiveLanguage = language !== undefined
      ? language
      : (activeView !== 'original' ? activeView : null);
    const queryParam = effectiveLanguage === 'academic'
      ? '?variant=academic'
      : (typeof effectiveLanguage === 'string' && effectiveLanguage.startsWith('ai:')
        ? `?variant=ai&revision=${effectiveLanguage.slice(3)}`
        : (effectiveLanguage ? `?language=${effectiveLanguage}` : ''));
    const exportUrl = `${API_BASE}/transcriptions/${jobId}/export/${format}/${queryParam}`;

    if (format === 'docx') {
      const title = documentMetadata?.title
        || (seriesJobs
          ? (seriesJobs.find(j => j.id === jobId)?.series_title || `Transcript Report - Job ${jobId}`)
          : `Transcript Report - Job ${jobId}`);
      setDocxPreview({ title, paragraphs: buildDocxPreviewParagraphs(jobId, effectiveLanguage), downloadUrl: exportUrl });
      return;
    }

    if (format === 'eagle') {
      // Mirror of the server's submission gate (ExportTranscriptView). The
      // server is the authority and refuses this with a 409 regardless of
      // what happens here; this exists so the user finds out BEFORE building
      // a preview and clicking download, rather than after.
      //
      // Only an actual refusal blocks. 'not_run' is the common case — the
      // gate is opt-in — and must behave exactly as it always has.
      if (typeof effectiveLanguage === 'string' && effectiveLanguage.startsWith('ai:')) {
        const revisionId = effectiveLanguage.slice(3);
        const revision = aiRevision?.id === revisionId
          ? aiRevision
          : (aiRevisions || []).find(r => r.id === revisionId);
        const refused = ['quality_review_required', 'technical_failure'];
        if (revision && refused.includes(revision.quality_status)) {
          const detail = revision.quality_status === 'technical_failure'
            ? 'The quality pipeline could not check this version.'
            : (revision.quality_reason || 'Unresolved quality issues remain.');
          alert(
            'This version has not passed quality review, so it cannot be ' +
            'exported as an Eagle Web submission.\n\n' + detail + '\n\n' +
            'Review the findings above, or export it as DOCX or TXT to work on it.'
          );
          return;
        }
      }

      const isTranslation = Boolean(
        (effectiveLanguage && effectiveLanguage !== 'original' && effectiveLanguage !== 'academic') ||
        (typeof effectiveLanguage === 'string' && effectiveLanguage.startsWith('ai:') && aiRevision?.operation === 'translate')
      );
      const srcName = (detectedLanguage && (languages.find(l => l.code === detectedLanguage.code)?.name || detectedLanguage.code)) || 'English';
      let tgtName = 'French';
      if (effectiveLanguage && typeof effectiveLanguage === 'string') {
        if (effectiveLanguage.startsWith('ai:')) {
          tgtName = aiRevision?.target_language || 'French';
        } else {
          tgtName = (translationLanguages.find(l => l.code === effectiveLanguage)?.name) || effectiveLanguage;
        }
      }

      const rawItems = getEnrichedExportItems(jobId, effectiveLanguage);
      setEaglePreview({
        jobId,
        isTranslation,
        title: documentMetadata?.title || (isTranslation ? 'Translation Job' : 'Transcription Job'),
        client: documentMetadata?.client || 'Eagle Web',
        transcriber: documentMetadata?.transcriber || documentMetadata?.presenter || documentMetadata?.author || '',
        translator: documentMetadata?.translator || documentMetadata?.author || '',
        date: documentMetadata?.date || new Date().toLocaleDateString('en-GB'),
        sourceLang: srcName,
        targetLang: tgtName,
        items: rawItems,
        effectiveLanguage,
      });
      return;
    }

    window.open(exportUrl, '_blank');
  };

  const handleExportSeries = (format, seriesId) => {
    if (!seriesId) return;
    window.open(`${API_BASE}/series/${seriesId}/export/${format}/`, '_blank');
  };

  // "Start New Transcription" -- a pure frontend/session reset, not a
  // backend action. If the previous job/series is still running, it keeps
  // running on the backend untouched; this only stops THIS tab from
  // displaying/tracking it and forgets it from the persisted session, so a
  // fresh Run doesn't have a leftover series panel (or a stale WebSocket/
  // polling interval fighting over the same screen with the new one --
  // that mismatch was the actual bug: an old interval kept ticking and
  // overwriting new state with stale data). Cancel the old job first via
  // its own Cancel button if it should actually stop, not this.
  const handleStartNewTranscription = () => {
    stopAllActiveConnections();
    clearSession();

    setSourceUrl('');
    setFile(null);
    setUploadProgress(null);
    setSeriesInfo(null);
    setSeriesJobs(null);
    setSeriesId(null);
    setSeriesActiveLanguage({});
    setCurrentJobId(null);
    setSegments([]);
    setTranslations([]);
    setAcademicFormat(null);
    resetAIAssistState();
    setActiveView('original');
    setSpeakerAliases({});
    setDetectedLanguage(null);
    setJobStatus('idle');
    setCancelRequested(false);
    setStatusMessage('Ready');
  };

  // Requests cancellation of an in-progress job. Sets cancelRequested
  // immediately (disabling the button before the backend even responds) --
  // the actual stop happens on the backend's polling loop, reported back
  // via the same WebSocket/polling status updates as completed/failed.
  // For the single-job flow this is currentJobId; series items pass their
  // own id (isSeriesItem=true) so only that one item stops, matching the
  // same per-item failure isolation as Export/Translate/Delete.
  const handleCancelJob = async (jobId, isSeriesItem = false) => {
    if (!isSeriesItem) setCancelRequested(true);
    try {
      await api.post(`/transcriptions/${jobId}/cancel/`);
    } catch (err) {
      console.error(err);
      if (!isSeriesItem) setCancelRequested(false);
      alert('Failed to cancel this job. Please check the server logs for details.');
    }
  };

  // Permanent: removes the job's DB records (segments/translations cascade
  // server-side), leftover temp audio, and any now-orphaned discovery
  // record -- see JobCleanupService. isSeriesItem controls whether this
  // resets the single-job view or just removes one row from the series list.
  const handleDeleteJob = async (jobId, isSeriesItem = false) => {
    if (!window.confirm(
      'Delete this transcription?\n\nThis permanently removes the transcript, translations, ' +
      'downloaded media, and job history for this item. This cannot be undone.'
    )) {
      return;
    }
    try {
      await api.del(`/transcriptions/${jobId}/`);

      if (isSeriesItem) {
        const remaining = (seriesJobs || []).filter((j) => j.id !== jobId);
        setSeriesJobs(remaining);
        setSegments(buildAggregatedSeriesSegments(remaining));
        // Keep the persisted session's job list in sync so a later restore
        // doesn't try to re-fetch an id that no longer exists; clear the
        // whole session once every item has been deleted.
        const persisted = loadSession();
        if (persisted && persisted.type === 'series' && persisted.seriesId === seriesId) {
          if (remaining.length === 0) {
            clearSession();
          } else {
            saveSession({ ...persisted, jobIds: remaining.map((j) => j.id) });
          }
        }
        setSeriesActiveLanguage((prev) => {
          const next = { ...prev };
          delete next[jobId];
          return next;
        });
      } else {
        // Single-job view: nothing left to show, reset to the idle state.
        setSegments([]);
        setTranslations([]);
        setAcademicFormat(null);
        resetAIAssistState();
        setActiveView('original');
        setSpeakerAliases({});
        setCurrentJobId(null);
        setJobStatus('idle');
        setStatusMessage('Ready');
        clearSession();
      }
    } catch (err) {
      console.error(err);
      alert('Failed to delete this job. Please check the server logs for details.');
    }
  };

  // Speaker renaming: aliases live server-side on the job (see
  // TranscriptionJobViewSet.speaker_aliases), applied at display time only
  // -- the underlying segment rows always keep their raw "Speaker N"
  // label, which is what makes this instant and trivially reversible.
  // editingSpeaker holds the RAW label currently being renamed (or null);
  // the input's own value is tracked separately so typing doesn't fire a
  // request on every keystroke.
  const [editingSpeaker, setEditingSpeaker] = useState(null);
  const [editingSpeakerValue, setEditingSpeakerValue] = useState('');

  const startEditingSpeaker = (rawLabel) => {
    setEditingSpeaker(rawLabel);
    setEditingSpeakerValue(speakerAliases[rawLabel] || '');
  };

  const saveSpeakerAlias = async (rawLabel, newAlias) => {
    if (!currentJobId) return;
    const trimmed = newAlias.trim();
    setSpeakerAliases(prev => {
      const next = { ...prev };
      if (trimmed) next[rawLabel] = trimmed; else delete next[rawLabel];
      return next;
    });
    setEditingSpeaker(null);
    try {
      await api.post(`/transcriptions/${currentJobId}/speaker_aliases/`, { aliases: { [rawLabel]: trimmed } });
    } catch (err) {
      console.error(err);
      alert('Failed to save the speaker name. Please check the server logs for details.');
    }
  };

  const revertAllSpeakerAliases = async () => {
    if (!currentJobId) return;
    setSpeakerAliases({});
    try {
      await api.post(`/transcriptions/${currentJobId}/speaker_aliases/`, { reset: true });
    } catch (err) {
      console.error(err);
      alert('Failed to revert speaker names. Please check the server logs for details.');
    }
  };

  // Manual transcript editing (typo fixes, and the annotation dropdown
  // below). Only enabled once a job has left 'processing' -- while
  // segments are still streaming in live via WebSocket/polling, an
  // in-progress edit would otherwise race with incoming updates.
  const segmentTextareaRefs = useRef({}); // { [segmentId]: HTMLTextAreaElement }

  // LAST KNOWN CURSOR POSITION, PER SEGMENT -- captured continuously while
  // the textarea has focus, not read at insert-time.
  //
  // The annotate dropdown is a native <select>; the instant it's clicked,
  // focus moves OFF the textarea and onto the select, so by the time its
  // onChange fires, `document.activeElement` is the select, never the
  // textarea. Checking live focus at insert-time (the previous approach)
  // was therefore true almost never, and the tag silently fell through to
  // the "append at the end" fallback on essentially every real click --
  // which is exactly the bug reported: tags always landing at the end
  // regardless of where the cursor actually was.
  const segmentCursorRef = useRef({}); // { [segmentId]: {start, end} }
  const rememberCursor = (segmentId, el) => {
    segmentCursorRef.current[segmentId] = { start: el.selectionStart, end: el.selectionEnd };
  };

  const handleSegmentTextEdit = (segmentId, newText) => {
    setSegments(prev => prev.map(s => (s.id === segmentId ? { ...s, text: newText } : s)));
  };

  const saveSegmentText = async (segmentId, text) => {
    if (!currentJobId || !segmentId) return;
    try {
      await api.patch(`/transcriptions/${currentJobId}/segments/${segmentId}/`, { text });
    } catch (err) {
      console.error(err);
      alert('Failed to save your edit. Please check the server logs for details.');
    }
  };

  // Inserts at the LAST REMEMBERED cursor position for this segment (see
  // segmentCursorRef above -- live focus is gone by the time this runs,
  // since the dropdown click already moved it). insertTagAtPosition
  // (lib/annotations.js) falls back to appending at the end only when no
  // position was ever recorded at all, e.g. a tag picked before the
  // textarea was ever clicked into this session.
  const handleInsertAnnotation = (segmentId, tag) => {
    const resolvedTag = tag === 'Custom tag...' ? window.prompt('Enter a custom tag (e.g. [whispering]):') : tag;
    if (!resolvedTag) return;
    const currentSeg = segments.find(s => s.id === segmentId);
    if (!currentSeg) return;
    const { text: newText, cursor } = insertTagAtPosition(
      currentSeg.text, segmentCursorRef.current[segmentId], resolvedTag,
    );
    segmentCursorRef.current[segmentId] = cursor;
    handleSegmentTextEdit(segmentId, newText);
    saveSegmentText(segmentId, newText);
  };

  // Translation is a separate, on-demand stage: it never runs automatically
  // and never touches the original transcript. Caches per (job, language) --
  // requesting an already-translated language returns instantly.
  const handleTranslate = async (jobId = currentJobId, language = translateTarget) => {
    if (!jobId || !language) return;
    try {
      const translation = await api.post(`/transcriptions/${jobId}/translate/`, { language });
      if (jobId === currentJobId) {
        // Single-job flow: exact existing behavior (tabs + live polling).
        upsertTranslation(translation);
        if (translation.status === 'completed') {
          setActiveView(translation.language);
        } else {
          startTranslationPolling(currentJobId, translation.language);
        }
      }
      // Series items: the next startSeriesPolling tick (every 2s) already
      // re-fetches every job, which includes its own nested translations --
      // no separate polling needed here.
    } catch (err) {
      console.error(err);
      alert('Failed to start translation. Please check the server logs for details.');
    }
  };

  const upsertTranslation = (translation) => {
    setTranslations(prev => {
      const exists = prev.some(t => t.language === translation.language);
      return exists
        ? prev.map(t => (t.language === translation.language ? translation : t))
        : [...prev, translation];
    });
  };

  const startTranslationPolling = (jobId, language) => {
    if (translationPollRef.current) clearInterval(translationPollRef.current);
    translationPollRef.current = setInterval(async () => {
      try {
        const data = await api.get(`/transcriptions/${jobId}/translations/`);
        setTranslations(data);
        const target = data.find(t => t.language === language);
        if (target && (target.status === 'completed' || target.status === 'failed')) {
          clearInterval(translationPollRef.current);
          if (target.status === 'completed') setActiveView(target.language);
        }
      } catch (err) {
        console.error('Translation polling error:', err);
      }
    }, 2000);
  };

  // Academic Format: converts spoken technical phrases into notation
  // ("x squared" -> "x²") -- optional, on-demand, never touches the
  // original transcript. Mirrors handleTranslate's shape, minus the
  // language parameter (subject is detected, not chosen -- see
  // core.services.academic on the backend).
  //
  // Unlike translation (which only ever polls, even though the backend
  // broadcasts translation_status_update/translated_batch_added over the
  // same WebSocket -- see the audit note on connectWebSocket below), this
  // opens its OWN dedicated WebSocket connection for real progress events,
  // because the ticket specifically asked for live Detecting/Formatting/
  // Validating feedback -- and the transcription job's own WebSocket is
  // essentially always already closed by the time this runs (Academic
  // Format is only ever clicked after transcription has completed, which
  // is exactly when connectWebSocket's own onmessage handler closes it).
  // Polling stays as the fallback, same reconnect-after-sleep reasoning
  // used everywhere else in this file.
  const handleAcademicFormat = async (jobId = currentJobId) => {
    if (!jobId) return;
    try {
      const result = await api.post(`/transcriptions/${jobId}/academic_format/`);
      if (jobId === currentJobId) {
        setAcademicFormat(result);
        if (result.status === 'completed') {
          setActiveView('academic');
        } else {
          connectAcademicFormatWebSocket(currentJobId);
          startAcademicFormatPolling(currentJobId);
        }
      }
    } catch (err) {
      console.error(err);
      alert('Failed to start academic formatting. Please check the server logs for details.');
    }
  };

  const connectAcademicFormatWebSocket = (jobId) => {
    try {
      const ws = new WebSocket(`${WS_BASE}/${jobId}/`);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'academic_format_status_update') {
          setAcademicFormat(prev => ({
            ...(prev || { segments: [] }),
            status: msg.status,
            subject: msg.subject !== undefined ? msg.subject : prev?.subject,
            subject_confidence: msg.subject_confidence !== undefined ? msg.subject_confidence : prev?.subject_confidence,
            error_message: msg.status === 'failed' ? msg.message : (prev?.error_message || ''),
          }));
          if (['completed', 'failed', 'cancelled'].includes(msg.status)) {
            if (academicFormatPollRef.current) clearInterval(academicFormatPollRef.current);
            ws.close(1000);
          }
        } else if (msg.type === 'academic_segment_added') {
          setAcademicFormat(prev => (prev ? { ...prev, segments: [...(prev.segments || []), msg.segment] } : prev));
        }
      };
      ws.onerror = (err) => {
        // startAcademicFormatPolling (already started alongside this) is
        // the fallback -- nothing extra to do here.
        console.warn('Academic format WebSocket error, relying on polling fallback.', err);
      };
    } catch (e) {
      console.warn('Failed to open academic format WebSocket, relying on polling fallback.', e);
    }
  };

  const startAcademicFormatPolling = (jobId) => {
    if (academicFormatPollRef.current) clearInterval(academicFormatPollRef.current);
    academicFormatPollRef.current = setInterval(async () => {
      try {
        const job = await api.get(`/transcriptions/${jobId}/`);
        if (job.academic_format) {
          setAcademicFormat(job.academic_format);
          if (['completed', 'failed', 'cancelled'].includes(job.academic_format.status)) {
            clearInterval(academicFormatPollRef.current);
          }
        }
      } catch (err) {
        console.error('Academic format polling error:', err);
      }
    }, 2000);
  };

  // ------------------------------------------------------------------ //
  // AI Assist
  //
  // Same on-demand, never-touches-the-original shape as Academic Format
  // above, with the differences a paid external API forces:
  //
  //   * Submitting is explicit and pre-flighted. The server refuses an
  //     over-budget or rate-limited request with a 429 before spending
  //     anything, and that message is shown as-is -- it's the only place the
  //     user finds out a limit was hit.
  //   * A revision is reviewed before it counts. Segments arrive with
  //     accepted=true, but rejecting one flips back to the original text
  //     without deleting the suggestion.
  //   * Dedicated WebSocket plus polling fallback, for exactly the reason
  //     connectAcademicFormatWebSocket documents: the transcription job's own
  //     socket is already closed by the time anyone clicks this.
  // ------------------------------------------------------------------ //

  const handleSaveDocumentMetadata = async (metadata, jobId = currentJobId) => {
    if (!jobId) return;
    try {
      const result = await api.post(`/transcriptions/${jobId}/metadata/`, { metadata });
      setDocumentMetadata(result.document_metadata || {});
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to save document details.');
    }
  };

  const handleAIAssist = async (options, jobId = currentJobId) => {
    if (!jobId) return;
    setAiError('');
    setAiBusy(true);
    try {
      // The server's message is already client-safe (see the
      // .safe_message/.detail split in core.services.ai_assist.errors --
      // internal limits and provider bodies never reach here), and
      // api.post's ApiError already carries it verbatim as err.message on
      // failure, same as this used to extract from result.error by hand.
      const result = await api.post(`/transcriptions/${jobId}/ai_revisions/`, options);

      setAiRevision(result);
      setAiRevisions(prev => {
        const others = prev.filter(r => r.id !== result.id);
        return [result, ...others];
      });
      setActiveView(`ai:${result.id}`);
      setShowAIPanel(false);

      if (['queued', 'processing'].includes(result.status)) {
        connectAIRevisionWebSocket(jobId, result.id);
        startAIRevisionPolling(result.id);
      } else {
        setAiBusy(false);
      }
    } catch (err) {
      console.error(err);
      setAiError(err.message || 'Failed to start AI processing.');
      setAiBusy(false);
    }
  };

  const connectAIRevisionWebSocket = (jobId, revisionId) => {
    try {
      const ws = new WebSocket(`${WS_BASE}/${jobId}/`);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        // Broadcasts go to the job's group, so a message can belong to a
        // different revision than the one on screen (e.g. an older socket
        // that hasn't closed yet). Filtered by id rather than assumed.
        if (msg.revision_id && msg.revision_id !== revisionId) return;

        if (msg.type === 'ai_revision_status_update') {
          setAiRevision(prev => (prev && prev.id !== revisionId ? prev : {
            ...(prev || { id: revisionId, segments: [] }),
            status: msg.status,
            degraded: msg.degraded !== undefined ? msg.degraded : prev?.degraded,
            total_tokens: msg.total_tokens !== undefined ? msg.total_tokens : prev?.total_tokens,
            cache_hits: msg.cache_hits !== undefined ? msg.cache_hits : prev?.cache_hits,
            cache_misses: msg.cache_misses !== undefined ? msg.cache_misses : prev?.cache_misses,
            estimated_tokens_saved: msg.estimated_tokens_saved !== undefined ? msg.estimated_tokens_saved : prev?.estimated_tokens_saved,
            error_message: msg.status === 'failed' ? msg.message : (prev?.error_message || ''),
            // The quality axis travels on the completion event too, so the
            // verdict is on screen the moment the run ends rather than only
            // after the authoritative refetch below lands.
            quality_status: msg.quality_status ?? prev?.quality_status,
            quality_profile: msg.quality_profile ?? prev?.quality_profile,
            quality_reason: msg.quality_reason ?? prev?.quality_reason,
            qa_iterations: msg.qa_iterations ?? prev?.qa_iterations,
            quality_confidence: msg.quality_confidence ?? prev?.quality_confidence,
            detected_domain: msg.detected_domain ?? prev?.detected_domain,
            detected_register: msg.detected_register ?? prev?.detected_register,
            submission_ready: msg.submission_ready ?? prev?.submission_ready,
            critical_issue_count: msg.critical_issue_count ?? prev?.critical_issue_count,
            high_issue_count: msg.high_issue_count ?? prev?.high_issue_count,
            medium_issue_count: msg.medium_issue_count ?? prev?.medium_issue_count,
            low_issue_count: msg.low_issue_count ?? prev?.low_issue_count,
          }));
          setAiRevisions(prev => prev.map(r => (
            r.id === revisionId ? {
              ...r,
              status: msg.status,
              degraded: msg.degraded ?? r.degraded,
              total_tokens: msg.total_tokens ?? r.total_tokens,
              cache_hits: msg.cache_hits ?? r.cache_hits,
              cache_misses: msg.cache_misses ?? r.cache_misses,
              estimated_tokens_saved: msg.estimated_tokens_saved ?? r.estimated_tokens_saved,
            } : r
          )));
          if (['completed', 'failed', 'cancelled'].includes(msg.status)) {
            if (aiRevisionPollRef.current) clearInterval(aiRevisionPollRef.current);
            setAiBusy(false);
            // One authoritative reload: the incremental events built the list
            // optimistically, and a refetch guarantees what's on screen is
            // what the server actually stored.
            refreshAIRevision(revisionId);
            ws.close(1000);
          }
        } else if (msg.type === 'ai_revision_segment_added') {
          setAiRevision(prev => {
            if (!prev || prev.id !== revisionId) return prev;
            const existing = prev.segments || [];
            if (existing.some(s => s.id === msg.segment.id)) return prev;
            return { ...prev, segments: [...existing, msg.segment] };
          });
        } else if (msg.type === 'ai_revision_segment_updated') {
          // A repair pass rewrote a segment that was already on screen.
          // Merged rather than appended: the row exists, only its text and
          // its changed flag moved.
          setAiRevision(prev => {
            if (!prev || prev.id !== revisionId) return prev;
            return {
              ...prev,
              segments: (prev.segments || []).map(s => (
                s.id === msg.segment.id ? { ...s, ...msg.segment } : s
              )),
            };
          });
        } else if (msg.type === 'ai_revision_quality_update') {
          // Progress through the quality stages. The terminal event carries
          // quality_status; the intermediate ones carry only a stage and a
          // message, so each field is merged only when actually present --
          // otherwise a mid-run progress tick would blank the verdict.
          setAiRevision(prev => {
            if (!prev || prev.id !== revisionId) return prev;
            const next = { ...prev, quality_stage: msg.stage, quality_message: msg.message };
            if (msg.quality_status !== undefined) next.quality_status = msg.quality_status;
            if (msg.quality_reason !== undefined) next.quality_reason = msg.quality_reason;
            if (msg.confidence !== undefined) next.quality_confidence = msg.confidence;
            if (msg.iterations !== undefined) next.qa_iterations = msg.iterations;
            if (msg.domain !== undefined) next.detected_domain = msg.domain;
            if (msg.register !== undefined) next.detected_register = msg.register;
            if (msg.counts) {
              next.critical_issue_count = msg.counts.critical ?? next.critical_issue_count;
              next.high_issue_count = msg.counts.high ?? next.high_issue_count;
              next.medium_issue_count = msg.counts.medium ?? next.medium_issue_count;
              next.low_issue_count = msg.counts.low ?? next.low_issue_count;
            }
            return next;
          });
        }
      };
      ws.onerror = (err) => {
        console.warn('AI revision WebSocket error, relying on polling fallback.', err);
      };
    } catch (e) {
      console.warn('Failed to open AI revision WebSocket, relying on polling fallback.', e);
    }
  };

  const refreshAIRevision = async (revisionId) => {
    try {
      const revision = await api.get(`/ai-revisions/${revisionId}/`);
      setAiRevision(prev => (prev && prev.id !== revisionId ? prev : revision));
      setAiRevisions(prev => prev.map(r => (r.id === revisionId ? { ...r, ...revision, segments: undefined } : r)));
      return revision;
    } catch (err) {
      console.error('Failed to refresh AI revision.', err);
      return null;
    }
  };

  // Read-only fetch of one version's segments for the pre-flight panel.
  // Deliberately not refreshAIRevision(): that one writes `aiRevision`, which
  // is the version currently open in the diff review -- picking a source to
  // read FROM must not switch what the user is looking at.
  const loadAIRevisionSegments = async (revisionId) => {
    try {
      const revision = await api.get(`/ai-revisions/${revisionId}/`);
      return revision.segments || [];
    } catch (err) {
      console.error('Failed to load the selected source version.', err);
      return [];
    }
  };

  const startAIRevisionPolling = (revisionId) => {
    if (aiRevisionPollRef.current) clearInterval(aiRevisionPollRef.current);
    aiRevisionPollRef.current = setInterval(async () => {
      const revision = await refreshAIRevision(revisionId);
      if (revision && ['completed', 'failed', 'cancelled'].includes(revision.status)) {
        clearInterval(aiRevisionPollRef.current);
        setAiBusy(false);
      }
    }, 2500);
  };

  const handleCancelAIRevision = async (revisionId, jobId = currentJobId) => {
    if (!jobId || !revisionId) return;
    try {
      await api.post(`/transcriptions/${jobId}/cancel_ai_revision/`, { revision: revisionId });
      // The flag is set; the background thread stops before its next provider
      // call and broadcasts 'cancelled'. Nothing to update here.
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to cancel AI processing.');
    }
  };

  // Accept/reject a single segment. Applied optimistically so the click feels
  // instant, then reconciled with what the server returns -- the server's
  // copy wins, so a failed request visibly reverts rather than leaving the UI
  // claiming a decision that was never stored.
  const handleAISegmentDecision = async (revisionId, segmentId, accepted) => {
    setAiRevision(prev => {
      if (!prev || prev.id !== revisionId) return prev;
      return {
        ...prev,
        segments: (prev.segments || []).map(s => (s.id === segmentId ? { ...s, accepted } : s)),
      };
    });
    try {
      const saved = await api.patch(`/ai-revisions/${revisionId}/segments/${segmentId}/`, { accepted });
      setAiRevision(prev => {
        if (!prev || prev.id !== revisionId) return prev;
        return {
          ...prev,
          segments: (prev.segments || []).map(s => (s.id === segmentId ? { ...s, ...saved } : s)),
        };
      });
    } catch (err) {
      console.error(err);
      refreshAIRevision(revisionId);
    }
  };

  const handleAIBulkDecision = async (revisionId, action) => {
    try {
      setAiRevision(await api.post(`/ai-revisions/${revisionId}/review/`, { action }));
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to apply that to every segment.');
    }
  };

  // Loading a revision from the version switcher: summaries carry no
  // segments, so opening one always fetches the full record.
  const handleSelectAIRevision = async (revisionId) => {
    setActiveView(`ai:${revisionId}`);
    if (aiRevision?.id === revisionId && aiRevision?.segments) return;
    await refreshAIRevision(revisionId);
  };

  // Segments currently on screen: the original transcript, a cached
  // translation's segments, the academic-format segments, or an AI revision's
  // effective text -- same shape either way for the preview panel. Only the
  // original is ever editable (see the annotation dropdown below) --
  // translations, academic formatting and AI revisions are all separate,
  // independently-regenerable artifacts (see Translation's, AcademicFormat's
  // and AIRevision's own docstrings), not something this manual editing aid
  // touches.
  //
  // For an AI revision the text shown is effective_text -- the accept/reject
  // result -- so this preview and an export of the same revision always agree
  // (ExportTranscriptView's variant=ai branch reads the same property).
  const activeAiRevisionId = activeView.startsWith('ai:') ? activeView.slice(3) : null;
  const displayedSegments = activeView === 'original'
    ? segments.map(s => ({
        ...s,
        speaker: speakerAliases[s.speaker] || s.speaker,
        diarizationEnabled: s.diarizationEnabled !== undefined ? s.diarizationEnabled : currentJobDiarizationEnabled,
      }))
    : activeView === 'academic'
    ? (academicFormat?.segments || []).map(s => ({
        id: s.id, speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        start: formatTime(s.start_time), text: s.text,
        startTime: s.start_time, endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }))
    : activeAiRevisionId
    ? ((aiRevision?.id === activeAiRevisionId ? aiRevision.segments : null) || []).map(s => ({
        id: s.id, speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        start: formatTime(s.start_time),
        text: s.effective_text !== undefined
          ? s.effective_text
          : (s.changed && s.accepted ? s.processed_text : s.original_text),
        startTime: s.start_time, endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }))
    : (translations.find(t => t.language === activeView)?.segments || []).map(s => ({
        id: s.id, speaker: speakerAliases[s.speaker_label] || s.speaker_label,
        start: formatTime(s.start_time), text: s.text,
        startTime: s.start_time, endTime: s.end_time,
        diarizationEnabled: currentJobDiarizationEnabled,
      }));

  // Grouped for presentation only -- see groupIntoParagraphs above. Never
  // changes what's in `segments`/`displayedSegments` itself, so editing,
  // WebSocket streaming, and exports are all unaffected by this.
  const displayedParagraphs = groupIntoParagraphs(displayedSegments);

  return (
    <div className={embedded
      ? 'text-[#e4e4e7] font-sans'
      : 'min-h-screen bg-[#121214] text-[#e4e4e7] p-3 sm:p-6 lg:p-8 font-sans'}>
      <div className={embedded
        ? 'space-y-4 sm:space-y-6'
        : 'max-w-4xl mx-auto space-y-4 sm:space-y-6'}>

        {/* Header Title -- the "Start New Transcription" button stays in
            normal flow (stacked below the subtitle) on narrow screens so it
            can never overlap the centered title; only from sm: up is there
            reliably enough horizontal room to float it into the top-right
            corner instead.

            Embedded, the title and subtitle are dropped: the workspace
            around it already names the task, and a second page title
            competing with the first is worse than none. The button keeps
            its place, in normal flow rather than absolutely positioned --
            with no centred title to avoid, it has nothing to overlap. */}
        <header className={embedded ? 'flex justify-end' : 'text-center space-y-2 sm:relative'}>
          {!embedded && (
            <>
              <h1 className="text-2xl sm:text-3xl lg:text-4xl font-extrabold tracking-tight bg-gradient-to-r from-blue-500 to-indigo-400 bg-clip-text text-transparent break-words">
                 Transcription Studio
              </h1>
              <p className="text-xs sm:text-sm text-zinc-400 px-2">
                 Transcribe Any file and url here
              </p>
            </>
          )}
          {(jobStatus !== 'idle' || currentJobId || seriesJobs || seriesInfo) && (
            <button
              onClick={handleStartNewTranscription}
              title="Clear the screen and start a fresh transcription. Any previous job/series keeps running on the backend untouched -- this only stops displaying it here."
              className={`px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-semibold rounded-lg transition-colors cursor-pointer${
                embedded ? '' : ' sm:absolute sm:top-0 sm:right-0'
              }`}
            >
              + Start New Transcription
            </button>
          )}
        </header>

        {/* Upload Container */}
        <section
          className={`border-2 border-dashed rounded-xl p-4 sm:p-6 md:p-8 text-center transition-all duration-300 ${
            dragActive ? 'border-blue-500 bg-blue-500/5' : 'border-zinc-800 bg-[#18181b]'
          }`}
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
        >
          <div className="space-y-4">
            {/* Click-to-browse lives on this inner wrapper specifically
                (not the whole <section>) so clicking the URL input or the
                auth-helper details below doesn't also pop the file picker
                via event bubbling. Drag-and-drop still works anywhere over
                the outer section. */}
            <div
              className={`flex flex-col items-center justify-center ${jobStatus === 'processing' ? '' : 'cursor-pointer'}`}
              onClick={handleDropzoneClick}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleDropzoneClick(); } }}
              aria-label="Browse for an audio or video file to upload"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept={SUPPORTED_UPLOAD_EXTENSIONS.join(',')}
                onChange={handleFileInputChange}
                disabled={jobStatus === 'processing'}
                className="hidden"
              />
              <svg className="w-12 h-12 text-zinc-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              {file ? (
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-blue-400">
                    Selected: {file.name} <span className="text-zinc-500 font-normal">({formatFileSize(file.size)})</span>
                  </p>
                  {jobStatus !== 'processing' && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setFile(null); setStatusMessage('Ready'); }}
                      title="Remove selected file"
                      className="text-zinc-500 hover:text-red-400 text-xs transition-colors"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-sm font-semibold">Drag & Drop media here, or click to browse files</p>
              )}
              <p className="text-xs text-zinc-500 mt-1">Supports MP3, WAV, M4A, MP4, MKV, MOV, WEBM, FLAC</p>
            </div>

            {uploadProgress !== null && (
              <div className="w-full max-w-md mx-auto space-y-1">
                <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-200"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <p className="text-[10px] text-zinc-500">Uploading... {uploadProgress}%</p>
              </div>
            )}

            <div className="flex items-center justify-center space-x-2">
              <span className="text-xs text-zinc-500">OR</span>
            </div>

            <input
              type="text"
              placeholder="Paste public media URL (YouTube, Vimeo, etc.)"
              value={sourceUrl}
              onChange={(e) => {
                setSourceUrl(e.target.value);
                if (e.target.value) setFile(null);
              }}
              disabled={jobStatus === 'processing'}
              className="w-full max-w-md px-4 py-2 bg-[#121214] border border-zinc-800 rounded-lg focus:outline-none focus:border-blue-500 text-sm transition-all disabled:opacity-50"
            />

            <details className="w-full max-w-md text-left mt-2">
              <summary className="text-xs text-zinc-400 cursor-pointer hover:text-zinc-300 select-none">
                Authentication helper (for login-walled pages)
              </summary>
              <div className="mt-2 space-y-2">
                <p className="text-xs text-zinc-500">
                  Paste headers JSON, header lines ("Key: Value"), a cookie string ("k=v"), or upload cookies.txt.
                </p>
                <textarea
                  value={siteAuthText}
                  onChange={(e) => setSiteAuthText(e.target.value)}
                  placeholder="Paste headers JSON, header lines, or cookie string here"
                  rows={3}
                  className="w-full px-3 py-2 bg-[#121214] border border-zinc-800 rounded-lg focus:outline-none focus:border-blue-500 text-xs font-mono transition-all resize-y"
                />
                <div className="flex items-center space-x-2">
                  <label className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold rounded-lg cursor-pointer transition-colors">
                    Choose File
                    <input type="file" accept=".txt" onChange={handleAuthCookiesFile} className="hidden" />
                  </label>
                  <span className="text-xs text-zinc-500">
                    {siteAuthCookiesFileName || 'No cookies.txt selected'}
                  </span>
                </div>
                <label className="flex items-start space-x-2 cursor-pointer pt-1">
                  <input
                    type="checkbox"
                    checked={trySequential}
                    onChange={(e) => setTrySequential(e.target.checked)}
                    className="accent-blue-500 mt-0.5"
                  />
                  <span className="text-xs text-zinc-500">
                    This page is part of a numbered sequence (e.g. a URL with{' '}
                    <code className="text-zinc-400">?seq_num=0</code>). Only enable this if you understand
                    that stepping through the sequence may have real effects on the source site
                    (e.g. advancing a queue) -- off by default for this reason.
                  </span>
                </label>
              </div>
            </details>
          </div>
        </section>

        {/* Options Dashboard */}
        <section className="bg-[#18181b] p-4 sm:p-6 rounded-xl border border-zinc-800 space-y-4">
          <h2 className="text-lg font-bold text-zinc-300">Job Configurations</h2>

          <div>
            <label className="block text-xs text-zinc-400 mb-1">Transcription Language</label>
            <SearchableLanguageSelect
              languages={languages}
              value={selectedLanguage}
              onChange={setSelectedLanguage}
              placeholder="Search or select a language..."
            />
            <p className="text-xs text-zinc-500 mt-1">
              Selecting a language skips auto-detection and improves accuracy. The platform automatically routes each language to whichever engine handles it best (Whisper by default; Simba/MMS for languages where they outperform it).
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <label className="flex items-center space-x-3 cursor-pointer text-sm">
              <input 
                type="checkbox" 
                checked={enableWordSync}
                onChange={(e) => setEnableWordSync(e.target.checked)}
                className="accent-blue-500" 
              />
              <span>Live Microsoft Word Sync</span>
            </label>

            <label
              className="flex items-center space-x-3 cursor-pointer text-sm"
              title="Real acoustic speaker diarization (pyannote.audio), not a guess -- adds one extra whole-file processing pass before transcription starts, so only enable it when you actually need multi-speaker labels."
            >
              <input
                type="checkbox"
                checked={enableDiarization}
                onChange={(e) => setEnableDiarization(e.target.checked)}
                className="accent-blue-500"
              />
              <span>Enable Speaker Diarization</span>
            </label>

            {enableDiarization && (
              <label
                className="flex items-center space-x-3 text-sm pl-7"
                title="Automatic detection can split one speaker into two or three on a tone shift alone. Naming the real count rules that out entirely for this job; it never affects any other job's automatic default."
              >
                <span className="text-zinc-400">Speakers</span>
                <select
                  value={expectedSpeakers}
                  onChange={(e) => setExpectedSpeakers(e.target.value)}
                  className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 text-sm"
                >
                  <option value="">Detect automatically</option>
                  {Array.from({ length: 9 }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>{n} speaker{n > 1 ? 's' : ''}</option>
                  ))}
                </select>
              </label>
            )}

            {jobStatus === 'processing' ? (
              <button
                onClick={() => currentJobId && handleCancelJob(currentJobId, false)}
                disabled={cancelRequested || !currentJobId}
                title={!currentJobId ? 'Series jobs are cancelled individually below' : undefined}
                className="w-full bg-red-700 hover:bg-red-600 disabled:bg-zinc-800 disabled:text-zinc-500 text-white font-bold py-2 rounded-lg text-sm transition-colors cursor-pointer"
              >
                {cancelRequested ? 'Cancelling...' : 'Cancel'}
              </button>
            ) : (
              <button
                onClick={handleRunClick}
                className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-800 text-white font-bold py-2 rounded-lg text-sm transition-colors cursor-pointer"
              >
                Run Transcription
              </button>
            )}
          </div>

          {seriesInfo && (
            <div className="bg-[#121214] border border-blue-900 rounded-lg p-4 space-y-3">
              <p className="text-sm text-zinc-300">
                This page looks like part of a series ({seriesInfo.items.length} related item(s) found). What would you like to transcribe?
              </p>
              <div className="flex flex-col sm:flex-row gap-2">
                <button onClick={chooseCurrentPageOnly} className="flex-1 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs">
                  Current Page Only
                </button>
                <button onClick={chooseRelatedPages} className="flex-1 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs">
                  + Related Pages (next {Math.min(RELATED_PAGES_COUNT, seriesInfo.items.length)})
                </button>
                <button onClick={chooseEntireSeries} className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs">
                  Entire Series ({seriesInfo.items.length})
                </button>
              </div>
            </div>
          )}

          {seriesJobs && (
            <div className="bg-[#121214] border border-zinc-800 rounded-lg p-3 sm:p-4 space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <p className="text-xs font-semibold text-zinc-400">Series Progress</p>
                {/* Convenience only: zips each item's already-independent
                    export together. Never merges transcripts or timestamps --
                    see ExportSeriesView. */}
                {seriesJobs.some(j => j.status === 'completed') && (
                  <div className="flex space-x-1">
                    {['txt', 'docx', 'srt', 'vtt', 'csv', 'eagle'].map((format) => (
                      <button
                        key={format}
                        onClick={() => handleExportSeries(format, seriesId)}
                        className={`px-2 py-1 text-[10px] rounded uppercase ${
                          format === 'eagle'
                            ? 'bg-emerald-800 hover:bg-emerald-700 text-white font-medium'
                            : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'
                        }`}
                        title={`Export Series (${format})`}
                      >
                        {format === 'eagle' ? 'Eagle' : format}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {seriesJobs.map((j) => {
                const jobTranslations = j.translations || [];
                const completedTranslations = jobTranslations.filter(t => t.status === 'completed');
                const activeLang = seriesActiveLanguage[j.id] || 'original';
                const isTerminal = j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled';
                return (
                  <div key={j.id} className="border-b border-zinc-800 last:border-0 pb-2 last:pb-0 space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-zinc-300 truncate max-w-[70%]">
                        {j.series_index + 1}. {j.series_title || j.source_url}
                      </span>
                      <span className="flex items-center space-x-2">
                        <span className={`font-semibold ${
                          j.status === 'completed' ? 'text-green-400'
                          : j.status === 'failed' ? 'text-red-400'
                          : j.status === 'cancelled' ? 'text-zinc-500'
                          : 'text-blue-400'
                        }`}>
                          {j.status}
                        </span>
                        {!isTerminal && (
                          <button
                            onClick={() => handleCancelJob(j.id, true)}
                            disabled={j.cancel_requested}
                            title="Cancel this item"
                            className="text-zinc-500 hover:text-red-400 transition-colors disabled:opacity-50 disabled:hover:text-zinc-500"
                          >
                            {j.cancel_requested ? 'Cancelling…' : '✕ Cancel'}
                          </button>
                        )}
                        {isTerminal && (
                          <button
                            onClick={() => handleDeleteJob(j.id, true)}
                            title="Delete this transcription"
                            className="text-zinc-500 hover:text-red-400 transition-colors"
                          >
                            🗑
                          </button>
                        )}
                      </span>
                    </div>

                    {j.status === 'completed' && (
                      <div className="flex items-center flex-wrap gap-1.5 pl-2">
                        <button
                          onClick={() => setSeriesActiveLanguage(prev => ({ ...prev, [j.id]: 'original' }))}
                          className={`px-2 py-1 text-[10px] rounded-full ${activeLang === 'original' ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400'}`}
                        >
                          Original
                        </button>
                        {completedTranslations.map((t) => (
                          <button
                            key={t.language}
                            onClick={() => setSeriesActiveLanguage(prev => ({ ...prev, [j.id]: t.language }))}
                            className={`px-2 py-1 text-[10px] rounded-full ${activeLang === t.language ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400'}`}
                          >
                            {(translationLanguages.find(l => l.code === t.language) || {}).name || t.language}
                          </button>
                        ))}
                        {translateTarget && !jobTranslations.some(t => t.language === translateTarget) && (
                          <button
                            onClick={() => handleTranslate(j.id, translateTarget)}
                            className="px-2 py-1 text-[10px] rounded-full bg-indigo-700 hover:bg-indigo-600 text-white"
                          >
                            Translate to {(translationLanguages.find(l => l.code === translateTarget) || {}).name || translateTarget}
                          </button>
                        )}
                        <span className="text-zinc-700">|</span>
                        {['txt', 'docx', 'srt', 'vtt', 'csv', 'eagle'].map((format) => (
                          <button
                            key={format}
                            onClick={() => handleExport(format, j.id, activeLang === 'original' ? null : activeLang)}
                            className={`px-2 py-1 text-[10px] rounded uppercase ${
                              format === 'eagle'
                                ? 'bg-emerald-800 hover:bg-emerald-700 text-white font-medium'
                                : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'
                            }`}
                          >
                            {format === 'eagle' ? 'Eagle' : format}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="pt-2">
            {/* min-w-0 on the text span is required, not decorative: a flex
                item's default min-width is its unwrapped content width, so
                without it a long status/error message (these can run to a
                full sentence) would refuse to wrap and force the whole row
                to overflow horizontally instead of shrinking to fit. */}
            <div className="flex items-center justify-between gap-2 text-xs text-zinc-400">
              <span className="min-w-0 break-words">Status: <span className="font-semibold text-blue-400">{statusMessage}</span></span>
              {jobStatus === 'processing' && (
                <div className="flex-shrink-0 animate-spin rounded-full h-4 w-4 border-2 border-t-blue-500 border-zinc-800" />
              )}
            </div>
            {detectedLanguage && (
              <div className="text-xs text-zinc-400 mt-1">
                Language in use: <span className="font-semibold text-blue-400">
                  {(languages.find(l => l.code === detectedLanguage.code) || {}).name || detectedLanguage.code}
                </span>
                {typeof detectedLanguage.confidence === 'number' && (
                  <span className="text-zinc-500"> ({Math.round(detectedLanguage.confidence * 100)}% confidence)</span>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Live Preview Display Panel */}
        <section className="bg-[#18181b] p-4 sm:p-6 rounded-xl border border-zinc-800 space-y-4">
          <div className="flex justify-between items-center flex-wrap gap-2">
            <h2 className="text-lg font-bold text-zinc-300">Live Transcript Preview</h2>
            <div className="flex items-center space-x-2 flex-wrap gap-y-1">
              {['txt', 'docx', 'srt', 'vtt', 'csv'].map((format) => (
                <button
                  key={format}
                  onClick={() => handleExport(format)}
                  className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-xs rounded transition-all uppercase"
                >
                  Export {format}
                </button>
              ))}
              <button
                onClick={() => handleExport('eagle')}
                className="px-3 py-1 bg-emerald-800 hover:bg-emerald-700 text-white text-xs font-semibold rounded transition-all flex items-center gap-1.5 shadow-sm"
                title="Eagle Web Final Submission Formatter & Exporter"
              >
                <span>🦅</span>
                <span>Eagle Submission</span>
              </button>
              {currentJobId && (jobStatus === 'completed' || jobStatus === 'failed' || jobStatus === 'cancelled') && (
                <button
                  onClick={() => handleDeleteJob(currentJobId, false)}
                  title="Delete this transcription"
                  className="px-2 py-1 text-zinc-500 hover:text-red-400 text-xs transition-colors"
                >
                  🗑 Delete
                </button>
              )}
            </div>
          </div>

          {/* Original / translated version tabs -- switching never re-translates,
              cached translations are shown instantly */}
          {(segments.length > 0 || translations.length > 0 || aiRevisions.length > 0) && (
            <div className="flex items-center space-x-2 flex-wrap gap-y-2">
              <button
                onClick={() => setActiveView('original')}
                className={`px-3 py-1 text-xs rounded-full transition-all ${activeView === 'original' ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'}`}
              >
                Original
              </button>
              {translations.map((t) => (
                <button
                  key={t.language}
                  onClick={() => t.status === 'completed' && setActiveView(t.language)}
                  disabled={t.status !== 'completed'}
                  className={`px-3 py-1 text-xs rounded-full transition-all ${
                    activeView === t.language ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                  } ${t.status !== 'completed' ? 'opacity-60 cursor-wait' : ''}`}
                >
                  {(translationLanguages.find(l => l.code === t.language) || {}).name || t.language}
                  {t.status !== 'completed' && t.status !== 'failed' ? '…' : ''}
                  {t.status === 'failed' ? ' (failed)' : ''}
                </button>
              ))}
              {academicFormat?.status === 'completed' && (
                <button
                  onClick={() => setActiveView('academic')}
                  className={`px-3 py-1 text-xs rounded-full transition-all ${
                    activeView === 'academic' ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                  }`}
                >
                  ✨ Academic
                </button>
              )}
              {/* One tab per AI revision. Failed and running ones stay
                  clickable on purpose -- selecting them is how the user sees
                  the progress or the error, which DiffReview renders. */}
              {aiRevisions.map((revision) => (
                <button
                  key={revision.id}
                  onClick={() => handleSelectAIRevision(revision.id)}
                  className={`px-3 py-1 text-xs rounded-full transition-all ${
                    activeView === `ai:${revision.id}` ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                  } ${['queued', 'processing'].includes(revision.status) ? 'cursor-wait' : ''}`}
                >
                  ✨ {revision.label || 'AI'}
                  {['queued', 'processing'].includes(revision.status) ? '…' : ''}
                  {revision.status === 'failed' ? ' (failed)' : ''}
                  {revision.status === 'cancelled' ? ' (cancelled)' : ''}
                </button>
              ))}
            </div>
          )}

          {/* Translate: on-demand only -- nothing here runs until the user clicks it.
              min-w-0 on the <select> matters here: a native <select> with no
              width bound sizes itself to its longest option text, and as a
              flex item it won't shrink below that by default -- on a narrow
              phone a long language name could otherwise push this row into
              horizontal overflow instead of the select just shrinking. */}
          {segments.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={translateTarget}
                onChange={(e) => setTranslateTarget(e.target.value)}
                className="min-w-0 flex-1 sm:flex-initial max-w-full px-3 py-1.5 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500"
              >
                <option value="">Translate To...</option>
                {translationLanguages.map((lang) => (
                  <option key={lang.code} value={lang.code}>{lang.name}</option>
                ))}
              </select>
              <button
                onClick={() => handleTranslate()}
                disabled={!translateTarget || !currentJobId}
                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer"
              >
                Translate
              </button>
            </div>
          )}

          {/* Academic Format: purely optional, on-demand post-processing of
              the already-completed transcript (see handleAcademicFormat) --
              converts spoken technical phrases into notation without ever
              touching the Original transcript. Re-clicking after it's
              already completed for the current segments is instant (backend
              content-hash cache), so this button stays enabled/re-clickable
              rather than hiding itself once done. */}
          {segments.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => handleAcademicFormat()}
                disabled={!currentJobId || (academicFormat && ['queued', 'detecting', 'formatting', 'validating'].includes(academicFormat.status))}
                className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer"
              >
                {academicFormat && ['queued', 'detecting', 'formatting', 'validating'].includes(academicFormat.status)
                  ? `✨ ${{
                      queued: 'Queued',
                      detecting: 'Detecting…',
                      formatting: 'Formatting…',
                      validating: 'Validating…',
                    }[academicFormat.status]}`
                  : '✨ Academic Format'}
              </button>
              {academicFormat?.status === 'completed' && (
                <span className="text-xs text-zinc-500">
                  {(SUBJECT_LABELS[academicFormat.subject] || academicFormat.subject)}
                  {typeof academicFormat.subject_confidence === 'number' ? ` · ${Math.round(academicFormat.subject_confidence * 100)}% confidence` : ''}
                </span>
              )}
              {academicFormat?.status === 'failed' && (
                <span className="text-xs text-red-400">
                  Academic formatting failed{academicFormat.error_message ? `: ${academicFormat.error_message}` : ''}
                </span>
              )}

              {/* AI Assist sits beside Academic Format rather than replacing
                  it: the deterministic formatter is the offline path and keeps
                  working with no key, no network and no token cost. This one
                  opens a workspace instead of dispatching immediately, because
                  everything worth adjusting -- metadata, range, operation,
                  prompt -- is decided before a single token is spent. */}
              <span className="text-zinc-700 hidden sm:inline">|</span>
              <button
                onClick={() => { setAiError(''); setShowAIPanel(v => !v); }}
                disabled={!currentJobId}
                className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors cursor-pointer disabled:bg-zinc-800 disabled:text-zinc-500 ${
                  showAIPanel ? 'bg-blue-700 hover:bg-blue-600 text-white' : 'bg-blue-600 hover:bg-blue-500 text-white'
                }`}
              >
                {showAIPanel ? '✨ AI Assist ▴' : '✨ AI Assist'}
              </button>
              {aiCapabilities && !aiCapabilities.ai_enabled && (
                <span className="text-xs text-zinc-500">Offline only — no AI key configured</span>
              )}
            </div>
          )}

          {/* The server's refusal messages are already client-safe, so they're
              shown as-is (see handleAIAssist). Kept outside the panel so a
              refusal stays visible after the panel closes. */}
          {aiError && (
            <div className="text-xs text-red-400 bg-red-950/30 border border-red-900/50 rounded-lg px-3 py-2">
              {aiError}
            </div>
          )}

          {showAIPanel && segments.length > 0 && (
            <AIAssistPanel
              segments={segments}
              metadata={documentMetadata}
              capabilities={aiCapabilities}
              translationLanguages={translationLanguages}
              revisions={aiRevisions}
              loadRevisionSegments={loadAIRevisionSegments}
              busy={aiBusy}
              onSaveMetadata={handleSaveDocumentMetadata}
              onSubmit={handleAIAssist}
              onClose={() => setShowAIPanel(false)}
            />
          )}

          {/* The diff review replaces nothing -- the Original tab is still one
              click away, and rejecting every segment leaves the exported text
              identical to the original. */}
          {activeAiRevisionId && aiRevision?.id === activeAiRevisionId && (
            <DiffReview
              revision={aiRevision}
              diarizationEnabled={currentJobDiarizationEnabled}
              speakerAliases={speakerAliases}
              onSegmentDecision={(segmentId, accepted) => handleAISegmentDecision(activeAiRevisionId, segmentId, accepted)}
              onBulkDecision={(action) => handleAIBulkDecision(activeAiRevisionId, action)}
              onCancel={() => handleCancelAIRevision(activeAiRevisionId)}
              onExport={() => handleExport('docx')}
              onClose={() => setActiveAiRevisionId(null)}
            />
          )}

          {/* Speaker renaming -- single-job flow only. Series items each
              carry their own independent aliases (applied automatically in
              buildAggregatedSeriesSegments) but don't get a rename UI here,
              since the aggregated preview mixes multiple jobs' speakers
              together and "which job's Speaker 0" would be ambiguous. */}
          {currentJobId && !seriesJobs && currentJobDiarizationEnabled && segments.some(s => s.speaker) && (
            <div className="flex items-center flex-wrap gap-2 text-xs">
              <span className="text-zinc-500">Speakers:</span>
              {[...new Set(segments.map(s => s.speaker).filter(Boolean))].map((rawLabel) => (
                editingSpeaker === rawLabel ? (
                  <input
                    key={rawLabel}
                    autoFocus
                    value={editingSpeakerValue}
                    onChange={(e) => setEditingSpeakerValue(e.target.value)}
                    onBlur={() => saveSpeakerAlias(rawLabel, editingSpeakerValue)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); saveSpeakerAlias(rawLabel, editingSpeakerValue); }
                      else if (e.key === 'Escape') { setEditingSpeaker(null); }
                    }}
                    placeholder={rawLabel}
                    className="px-2 py-1 w-32 bg-[#121214] border border-blue-500 rounded text-xs focus:outline-none"
                  />
                ) : (
                  <button
                    key={rawLabel}
                    onClick={() => startEditingSpeaker(rawLabel)}
                    title={`Rename ${rawLabel}`}
                    className="px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"
                  >
                    {speakerAliases[rawLabel] || rawLabel}
                    {speakerAliases[rawLabel] && <span className="text-zinc-500"> ({rawLabel})</span>}
                    {' '}✎
                  </button>
                )
              ))}
              {Object.keys(speakerAliases).length > 0 && (
                <button
                  onClick={revertAllSpeakerAliases}
                  title="Revert every speaker back to its default label"
                  className="text-zinc-500 hover:text-red-400"
                >
                  Reset names
                </button>
              )}
            </div>
          )}

          <div className="min-h-[200px] max-h-[350px] overflow-y-auto p-4 bg-[#121214] rounded-lg border border-zinc-900 text-sm space-y-3">
            {displayedSegments.length === 0 ? (
              <p className="text-zinc-500 italic text-center pt-16">Transcripts will load live here during processing...</p>
            ) : (
              (() => {
                // Editing (typo fixes + the annotation dropdown) is only
                // available for the single job's own ORIGINAL transcript,
                // and only once it's done processing -- while segments are
                // still streaming in live, an in-progress edit would
                // otherwise race with incoming WebSocket/poll updates. One
                // value for the whole job/view, not per-segment -- nothing
                // below depends on which specific segment is being looked at.
                const isEditableJob = Boolean(
                  currentJobId && !seriesJobs && activeView === 'original' &&
                  ['completed', 'failed', 'cancelled'].includes(jobStatus)
                );
                return displayedParagraphs.map((para, idx) => {
                  if (para.isHeader) {
                    return (
                      <div key={idx} className="pt-3 pb-1 text-xs font-bold text-zinc-400 uppercase tracking-wide border-b border-zinc-800 first:pt-0">
                        {para.label}
                      </div>
                    );
                  }
                  // para.speaker is already '' when diarization was off for
                  // this stretch (see groupIntoParagraphs) -- no fabricated
                  // speaker names, ever.
                  const key = para.items[0].id || idx;
                  if (!isEditableJob) {
                    // Read-only (still processing, a translation, or a
                    // series aggregate): one header per PARAGRAPH, flowing
                    // merged text -- this is what turns a choppy "Speaker 0
                    // (0:05) / Speaker 0 (0:12) / Speaker 0 (0:18)" listing
                    // into something that reads as one paragraph.
                    return (
                      <div key={key} className="flex flex-col space-y-1 transition-all duration-300 animate-fadeIn pt-2 first:pt-0">
                        <div className="flex items-center space-x-2 text-xs flex-wrap gap-y-1">
                          {para.speaker && <span className="font-semibold text-blue-400">{para.speaker}</span>}
                          <span className="text-zinc-500">({para.start})</span>
                        </div>
                        <p className="text-zinc-300">{para.text}</p>
                      </div>
                    );
                  }
                  // Editable: every ASR segment keeps its own timestamp,
                  // textarea, and annotate control exactly as before --
                  // grouping only collapses the REPEATED SPEAKER NAME
                  // (shown once, on the paragraph's first segment) since
                  // that repetition was the actual complaint (a single
                  // speaker flip-flopping "Speaker 0 / Speaker 1 / Speaker
                  // 0" every few seconds). The timestamp stays on every
                  // line -- it was never the problem, and it's needed for
                  // navigating/editing a specific segment.
                  return (
                    <div key={key} className="flex flex-col space-y-1 transition-all duration-300 animate-fadeIn pt-2 first:pt-0">
                      {para.items.map((seg, segIdx) => (
                        <div key={seg.id} className="flex flex-col space-y-1">
                          <div className="flex items-center space-x-2 text-xs flex-wrap gap-y-1">
                            {segIdx === 0 && para.speaker && (
                              <span className="font-semibold text-blue-400">{para.speaker}</span>
                            )}
                            <span className="text-zinc-500">({seg.start})</span>
                          </div>
                          <div className="flex items-start gap-2">
                            <textarea
                              ref={(el) => { if (el) segmentTextareaRefs.current[seg.id] = el; }}
                              value={seg.text}
                              onChange={(e) => handleSegmentTextEdit(seg.id, e.target.value)}
                              onSelect={(e) => rememberCursor(seg.id, e.target)}
                              onBlur={(e) => { rememberCursor(seg.id, e.target); saveSegmentText(seg.id, e.target.value); }}
                              rows={Math.max(1, Math.ceil(seg.text.length / 80))}
                              className="w-full px-2 py-1 bg-transparent border border-transparent hover:border-zinc-800 focus:border-blue-500 rounded text-zinc-300 focus:outline-none resize-y transition-colors"
                            />
                            <select
                              value=""
                              onChange={(e) => { if (e.target.value) handleInsertAnnotation(seg.id, e.target.value); e.target.value = ''; }}
                              title="Insert an annotation at the cursor"
                              className="shrink-0 px-1.5 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-[10px] text-zinc-400 focus:outline-none"
                            >
                              <option value="">+ Annotate</option>
                              {ANNOTATION_TAGS.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
                              <option value="Custom tag...">Custom tag...</option>
                            </select>
                          </div>
                        </div>
                      ))}
                    </div>
                  );
                });
              })()
            )}
          </div>
        </section>

      </div>

      {docxPreview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setDocxPreview(null)}
        >
          <div
            className="w-full max-w-2xl max-h-[85vh] flex flex-col bg-[#18181b] border border-zinc-800 rounded-xl shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
              <h3 className="text-sm font-semibold text-zinc-200">DOCX Preview</h3>
              <button
                onClick={() => setDocxPreview(null)}
                title="Close"
                className="text-zinc-500 hover:text-zinc-300 text-lg leading-none cursor-pointer"
              >
                &times;
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
              <h1 className="text-lg font-bold text-zinc-100">{docxPreview.title}</h1>
              {docxPreview.paragraphs.length === 0 ? (
                <p className="text-zinc-500 italic text-sm">No transcript content to preview yet.</p>
              ) : (
                docxPreview.paragraphs.map((para, idx) => (
                  para.isHeader ? (
                    <div key={idx} className="pt-2 text-xs font-bold text-zinc-400 uppercase tracking-wide border-b border-zinc-800">
                      {para.label}
                    </div>
                  ) : (
                    <p key={idx} className="text-sm text-zinc-300 leading-relaxed">
                      {para.speaker && <strong className="text-zinc-100">{para.speaker}: </strong>}
                      {para.text}
                    </p>
                  )
                ))
              )}
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-zinc-800">
              <button
                onClick={() => setDocxPreview(null)}
                className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-semibold rounded-lg transition-colors cursor-pointer"
              >
                Close
              </button>
              <button
                onClick={() => { window.open(docxPreview.downloadUrl, '_blank'); setDocxPreview(null); }}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer"
              >
                Download .docx
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Eagle Final Submission Format Preview Modal */}
      {eaglePreview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs"
          onClick={() => setEaglePreview(null)}
        >
          <div
            className="w-full max-w-3xl max-h-[90vh] flex flex-col bg-[#18181b] border border-zinc-700/80 rounded-2xl shadow-2xl overflow-hidden animate-fadeIn"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 bg-[#141416]">
              <div className="flex items-center space-x-2.5">
                <span className="text-xl">🦅</span>
                <div>
                  <h3 className="text-base font-bold text-zinc-100">
                    Eagle Final Submission Format
                  </h3>
                  <p className="text-xs text-zinc-400">
                    {eaglePreview.isTranslation ? 'Translation Job Submission' : 'Transcription Job Submission with Millisecond Timestamps'}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setEaglePreview(null)}
                className="text-zinc-400 hover:text-zinc-200 text-lg p-1 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
              >
                ✕
              </button>
            </div>

            {/* Modal Content */}
            <div className="flex-1 overflow-y-auto p-6 space-y-5 custom-scrollbar">
              {/* Metadata Editor Grid */}
              <div className="bg-[#121214] border border-zinc-800/80 rounded-xl p-4 space-y-3">
                <h4 className="text-xs font-bold text-zinc-400 uppercase tracking-wider">Submission Metadata</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                  <div>
                    <label className="block text-zinc-500 mb-1">
                      {eaglePreview.isTranslation ? 'Translation Job Name' : 'Transcription Job Name'}
                    </label>
                    <input
                      type="text"
                      value={eaglePreview.title}
                      onChange={(e) => setEaglePreview(p => ({ ...p, title: e.target.value }))}
                      placeholder={eaglePreview.isTranslation ? 'e.g. Translation Job Title' : 'e.g. Transcription Job Title'}
                      className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-zinc-500 mb-1">Client</label>
                    <input
                      type="text"
                      value={eaglePreview.client}
                      onChange={(e) => setEaglePreview(p => ({ ...p, client: e.target.value }))}
                      placeholder="e.g. Eagle Web"
                      className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-zinc-500 mb-1">
                      {eaglePreview.isTranslation ? 'Translated By' : 'Transcribed by'}
                    </label>
                    <input
                      type="text"
                      value={eaglePreview.isTranslation ? eaglePreview.translator : eaglePreview.transcriber}
                      onChange={(e) => setEaglePreview(p => ({
                        ...p,
                        [eaglePreview.isTranslation ? 'translator' : 'transcriber']: e.target.value
                      }))}
                      placeholder={eaglePreview.isTranslation ? 'e.g. Translator Name' : 'e.g. Transcriber Name'}
                      className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-zinc-500 mb-1">Date</label>
                    <input
                      type="text"
                      value={eaglePreview.date}
                      onChange={(e) => setEaglePreview(p => ({ ...p, date: e.target.value }))}
                      placeholder="DD/MM/YYYY"
                      className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                  {eaglePreview.isTranslation && (
                    <>
                      <div>
                        <label className="block text-zinc-500 mb-1">Source Language</label>
                        <input
                          type="text"
                          value={eaglePreview.sourceLang}
                          onChange={(e) => setEaglePreview(p => ({ ...p, sourceLang: e.target.value }))}
                          placeholder="e.g. English"
                          className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-zinc-500 mb-1">Target Language</label>
                        <input
                          type="text"
                          value={eaglePreview.targetLang}
                          onChange={(e) => setEaglePreview(p => ({ ...p, targetLang: e.target.value }))}
                          placeholder="e.g. French"
                          className="w-full px-3 py-1.5 bg-zinc-900 border border-zinc-700/70 rounded-lg text-zinc-200 focus:border-blue-500 focus:outline-none"
                        />
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Live Preview Box */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-zinc-400 uppercase tracking-wider">Formatted Output Preview</span>
                  <span className="text-zinc-500">Exact layout submitted to Eagle</span>
                </div>
                <pre className="p-4 bg-[#0d0d0f] border border-zinc-800 rounded-xl text-xs font-mono text-zinc-300 leading-relaxed overflow-x-auto max-h-[380px] select-all whitespace-pre-wrap">
                  {eaglePreview.isTranslation
                    ? generateEagleTranslationText(eaglePreview)
                    : generateEagleTranscriptionText(eaglePreview)}
                </pre>
              </div>
            </div>

            {/* Modal Actions */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-zinc-800 bg-[#141416] flex-wrap gap-3">
              <button
                onClick={() => {
                  const text = eaglePreview.isTranslation
                    ? generateEagleTranslationText(eaglePreview)
                    : generateEagleTranscriptionText(eaglePreview);
                  navigator.clipboard.writeText(text);
                  setEagleCopied(true);
                  setTimeout(() => setEagleCopied(false), 2000);
                }}
                className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium rounded-lg transition-colors flex items-center space-x-1.5 cursor-pointer"
              >
                <span>{eagleCopied ? '✓ Copied to Clipboard' : '📋 Copy to Clipboard'}</span>
              </button>
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => setEaglePreview(null)}
                  className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 text-xs rounded-lg transition-colors cursor-pointer"
                >
                  Close
                </button>
                <button
                  onClick={() => {
                    const text = eaglePreview.isTranslation
                      ? generateEagleTranslationText(eaglePreview)
                      : generateEagleTranscriptionText(eaglePreview);
                    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `transcript_${eaglePreview.jobId}_eagle.txt`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                    setEaglePreview(null);
                  }}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg transition-colors flex items-center space-x-1.5 cursor-pointer shadow-md"
                >
                  <span>📥 Download Eagle Format (.txt)</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
