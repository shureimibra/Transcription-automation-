import { createContext, useContext } from 'react';

// WHERE THE REVIEW SCREENS READ AND WRITE.
//
// ReviewWorkspace and TranslationWorkspace are used by two kinds of account.
// Your own workers edit the SHARED draft and submit it to Eagle. Outside
// Eagle taskers edit a PRIVATE copy and copy the result out themselves (see
// api/tasker.js and backend eagle_tasker.py). The screens are the same; only
// the endpoints behind them differ, so they ask this context instead of
// importing the API modules directly.
//
// THIS FILE IMPORTS NO API MODULE ON PURPOSE. The tasker app is a separate
// bundle (tasker/DeskApp.jsx) and must not carry the worker endpoints. The
// worker set lives in workerApi.js, which registers itself as the fallback
// when the main app (or the test setup) imports it -- so every worker screen,
// and every test that renders a workspace without a provider, behaves
// exactly as before.

const WorkspaceApiContext = createContext(null);

let fallback = null;

export function registerDefaultWorkspaceApi(api) {
  fallback = api;
}

export const WorkspaceApiProvider = WorkspaceApiContext.Provider;

export function useWorkspaceApi() {
  const api = useContext(WorkspaceApiContext) || fallback;
  if (!api) throw new Error('No workspace API: wrap the screen in a WorkspaceApiProvider.');
  return api;
}
