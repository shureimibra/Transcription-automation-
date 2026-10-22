import React from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, CAPABILITY } from '../auth/AuthContext.jsx';
import RequireAuth from '../auth/RequireAuth.jsx';
import HardRedirect from '../components/HardRedirect.jsx';
import TaskerShell from './TaskerShell.jsx';
import TaskerLogin from './TaskerLogin.jsx';
import TaskerSignup from './TaskerSignup.jsx';
import TaskerSearch from './TaskerSearch.jsx';
import TaskerTask from './TaskerTask.jsx';
import { taskerPath, TASKER_BASE } from './brand.js';

/**
 * The whole application an Eagle tasker's browser ever loads.
 *
 * A SEPARATE BUNDLE, NOT A SET OF ROUTES IN App.jsx. main.jsx loads this
 * file, and only this file, for any address under TASKER_BASE. Nothing here
 * imports the worker, admin or studio screens, or the worker API module, so
 * none of their code -- or the endpoints they call -- is in what a tasker
 * downloads. The server enforces the same boundary independently
 * (backend apps/workforce/perimeter.py); this keeps the code itself out of
 * reach too.
 *
 * Anything outside TASKER_BASE is a full page load into the main app, so an
 * administrator who opens these pages can still go back.
 */
/**
 * An address this app does not know.
 *
 * Under TASKER_BASE it goes to the tasker home. It must NOT reload the page:
 * main.jsx would load this same app for that address, hit this same route,
 * and reload again -- an endless loop that shows as a dark, empty screen.
 * Only an address outside TASKER_BASE is a real page load into the main app.
 */
function Fallback() {
  const { pathname } = useLocation();
  if (pathname === TASKER_BASE || pathname.startsWith(`${TASKER_BASE}/`)) {
    return <Navigate to={TASKER_BASE} replace />;
  }
  return <HardRedirect />;
}

export default function DeskApp() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path={taskerPath.login} element={<TaskerLogin />} />
          <Route path={taskerPath.signup} element={<TaskerSignup />} />
          <Route
            path={TASKER_BASE}
            element={
              <RequireAuth capability={CAPABILITY.DESK} loginPath={taskerPath.login}>
                <TaskerShell><TaskerSearch /></TaskerShell>
              </RequireAuth>
            }
          />
          <Route
            path={`${TASKER_BASE}/task/:taskId`}
            element={
              <RequireAuth capability={CAPABILITY.DESK} loginPath={taskerPath.login}>
                <TaskerShell><TaskerTask /></TaskerShell>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Fallback />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
