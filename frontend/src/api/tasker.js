import { api } from './client.js';

// The Eagle tasker's calls. Backend: workforce/eagle_tasker_views.py.
//
// EVERYTHING IS ADDRESSED BY TASK, NEVER BY JOB. The tasker edits their own
// copy, which the server finds from the task and the signed-in account. The
// job id the review screens pass to these functions is ignored on purpose:
// it would point at the SHARED transcript.

const base = (taskId) => `/workforce/tasker/tasks/${taskId}`;

export const tasker = {
  /** Find a task by its exact Eagle ID, or by the whole Eagle link. */
  lookup: (reference) => api.post('/workforce/tasker/lookup/', { reference }),

  /** The task summary again -- for a reload, and to poll readiness. */
  get: (taskId) => api.get(`${base(taskId)}/`),
};

/**
 * The same shape as workerApi.WORKER_API, pointed at one tasker copy.
 * Built per task because every call needs the task id, including the ones
 * the screens make with a job id.
 */
export function taskerWorkspaceApi(taskId) {
  const at = (suffix) => `${base(taskId)}/${suffix}`;
  return {
    mode: 'desk',
    transcript: {
      get: () => api.get(at('transcript/')),
      setSpeakerAliases: (_job, aliases) => api.post(at('speaker-aliases/'), { aliases }),
    },
    segments: {
      update: (_job, segmentId, changes) => api.patch(at(`segments/${segmentId}/`), changes),
      revert: (_job, segmentId) => api.post(at(`segments/${segmentId}/revert/`)),
      split: (_job, segmentId, splitAt, secondSpeaker) => api.post(
        at(`segments/${segmentId}/split/`),
        { split_at: splitAt, second_speaker: secondSpeaker },
      ),
    },
    submission: {
      // The final draft, in the same response shape as the worker preview.
      preview: () => api.get(at('final-draft/')),
      // Never reached -- the tasker screens offer Copy instead of Submit --
      // but refused loudly rather than silently if anything ever calls it.
      submit: () => Promise.reject(new Error('Copy the final draft and paste it where you submit.')),
      checkPrompt: () => api.get(at('check/')),
      saveAccuracyCheck: (_task, notes) => api.post(at('check/'), { notes }),
    },
    translation: {
      get: () => api.get(at('translation/')),
      save: (_task, text) => api.post(at('translation/'), { text }),
    },
    aiRevisions: {
      // One line of the tasker's own English. Answers already finished, in
      // the shape of a completed revision, so the screen never polls.
      request: (_job, options) => api.post(at('retranslate/'), {
        index: options.segment_start_index,
      }),
      get: () => Promise.reject(new Error('Nothing to poll.')),
    },
    taskMedia: {
      url: () => `/api/v1${at('media/')}`,
    },
    tasks: {
      events: () => api.get(at('applied-findings/')),
      applyFinding: (_task, phrase, suggestion) =>
        api.post(at('applied-findings/'), { phrase, suggestion }),
    },
  };
}
