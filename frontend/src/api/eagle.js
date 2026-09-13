import { api } from './client.js';

// The tasker's loop, as API calls.
//
// MEDIA IS A URL, NOT A FETCH. The <audio> element must issue its own ranged
// requests to seek without re-downloading, so this hands back a path and lets
// the browser drive it. It points at OUR backend: a worker never receives an
// Eagle URL and never receives a cookie.
//
// There is deliberately no client-side Eagle formatter. `preview()` asks the
// backend what it would send, so what a worker approves is byte-for-byte what
// the platform receives — two renderers that can disagree is a bug waiting
// for a deadline.

export const work = {
  /** Claim the next warm task. 409 `pool_empty` when nothing is ready. */
  next: (options = {}) => api.post('/workforce/work/next/', options),

  /** Is there work? Admins additionally get minutes and session health. */
  status: () => api.get('/workforce/work/status/'),
};

export const taskMedia = {
  /**
   * The audio endpoint for an <audio src>. Not fetched here on purpose —
   * range requests and buffering belong to the browser.
   */
  url: (taskId) => `/api/v1/workforce/tasks/${taskId}/media/`,
};

export const submission = {
  /** Exactly what Eagle will receive, plus the quality gate's findings. */
  preview: (taskId) => api.get(`/workforce/tasks/${taskId}/preview/`),

  /**
   * Send it. The backend renders, gates, calls Eagle, and only then records
   * the task as submitted.
   */
  submit: (taskId, body = {}) =>
    api.post(`/workforce/tasks/${taskId}/submit-external/`, body),

  /**
   * The accuracy-check prompt for a transcription task, already matched to
   * the project's own standard (edited vs. strict verbatim) -- see
   * TranscriptCheckPromptView. A worker pastes this ahead of the document
   * `preview` above already renders; there is no "rewrite" variant because
   * a worker's assistant never hears the audio (see
   * worker_prompts.build_transcript_check's own docstring). Also returns
   * whatever was already saved via `saveAccuracyCheck`, so the screen can
   * show the step as done across a reload.
   */
  checkPrompt: (taskId) =>
    api.get(`/workforce/tasks/${taskId}/transcript-check-prompt/`),

  /**
   * Record what the worker's own assistant found -- an audit trail, NOT a
   * document that overwrites the transcript (see TranscriptCheckPromptView
   * for why). This is what `preview`/`submit` now require to exist before
   * a transcription task can go out; the actual correction still happens
   * through the normal per-line editing on this screen.
   */
  saveAccuracyCheck: (taskId, notes) =>
    api.post(`/workforce/tasks/${taskId}/transcript-check-prompt/`, { notes }),
};

// Segment editing lives on the transcription API, addressed by job.
export const segments = {
  /**
   * Correct the words, the speaker, or both.
   *
   * Timestamps are refused by the server — they describe where in the audio
   * the segment actually is, and Eagle grades timing.
   */
  update: (jobId, segmentId, changes) =>
    api.patch(`/transcriptions/${jobId}/segments/${segmentId}/`, changes),

  /** Undo every edit made to one line's text, back to the ASR original. */
  revert: (jobId, segmentId) =>
    api.post(`/transcriptions/${jobId}/segments/${segmentId}/revert/`),
};

/**
 * A translation task's two documents.
 *
 * The English source and the French draft come from the same transcript, so
 * they arrive aligned by index -- the review screen pairs them without
 * guessing. `save` stores the worker's polished version and answers with the
 * gate's verdict, so a paste-back is checked the moment it lands rather than
 * at submission time.
 */
export const translation = {
  get: (taskId) => api.get(`/workforce/tasks/${taskId}/translation/`),
  save: (taskId, text) =>
    api.post(`/workforce/tasks/${taskId}/translation/`, { text }),
};

/**
 * Retranslating a single line, in place, after the English was corrected.
 *
 * NOT THE BULK PATH. `translation` above is the whole-document copy-paste
 * workflow through the worker's own assistant -- deliberately kept off our
 * own Gemini quota (see warmpool._warm_translation's own cost accounting).
 * This one is a real request, scoped to a few segments, and it must stay
 * that way: the caller is responsible for passing quality_profile: 'none'
 * so a one-line fix does not silently inherit the multi-request quality
 * gate meant for unsupervised warming.
 */
export const aiRevisions = {
  request: (jobId, options) =>
    api.post(`/transcriptions/${jobId}/ai_revisions/`, options),
  get: (revisionId) => api.get(`/ai-revisions/${revisionId}/`),
};

export const transcript = {
  /** The job with its segments — what the review screen renders. */
  get: (jobId) => api.get(`/transcriptions/${jobId}/`),

  /**
   * Rename a speaker everywhere at once. Distinct from a per-segment edit:
   * this is "Speaker 2 is actually Female_1 throughout", not "this one turn
   * was misattributed".
   */
  setSpeakerAliases: (jobId, aliases) =>
    api.post(`/transcriptions/${jobId}/speaker_aliases/`, { aliases }),
};
