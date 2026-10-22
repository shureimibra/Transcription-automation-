import {
  aiRevisions, segments, submission, taskMedia, transcript, translation,
} from '../api/eagle.js';
import { tasks } from '../api/workforce.js';
import { registerDefaultWorkspaceApi } from './workspaceApi.js';
import ReviewPreviewDialog from './ReviewPreviewDialog.jsx';
import TranslationPreviewDialog from './TranslationPreviewDialog.jsx';
import TranscriptionGuideModal from './TranscriptionGuideModal.jsx';
import TranslationGuideModal from './TranslationGuideModal.jsx';

/**
 * Your workers' endpoints for the review screens: the shared draft, and
 * Submit to Eagle. Importing this module makes it the default (see
 * workspaceApi.js). Only the main app imports it -- never the tasker app.
 */
export const WORKER_API = {
  mode: 'worker',
  aiRevisions, segments, submission, taskMedia, transcript, translation, tasks,
  // Preview + Submit to Eagle, and the guides that describe it. Workers only.
  dialogs: {
    review: ReviewPreviewDialog,
    translation: TranslationPreviewDialog,
    reviewGuide: TranscriptionGuideModal,
    translationGuide: TranslationGuideModal,
  },
};

registerDefaultWorkspaceApi(WORKER_API);
