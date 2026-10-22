import React from 'react';
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, CAPABILITY, useAuth } from './auth/AuthContext.jsx';
import RequireAuth from './auth/RequireAuth.jsx';
import LoginPage from './auth/LoginPage.jsx';
import AppShell from './components/AppShell.jsx';
import WorkerDashboard from './worker/WorkerDashboard.jsx';
import TaskQueue from './worker/TaskQueue.jsx';
import TaskWorkspace from './worker/TaskWorkspace.jsx';
import AdminOverview from './admin/AdminOverview.jsx';
import AdminTasks from './admin/AdminTasks.jsx';
import AdminWorkers from './admin/AdminWorkers.jsx';
import AdminAccounts from './admin/AdminAccounts.jsx';
import Dashboard from './views/Dashboard.jsx';
import HardRedirect from './components/HardRedirect.jsx';
import { TASKER_BASE } from './tasker/brand.js';
// The worker endpoints for the shared review screens (see workspaceApi.js).
import './worker/workerApi.js';

/**
 * Routing for the whole application.
 *
 * THREE EXPERIENCES, ONE ENGINE. A tasker gets the workforce surface, a tool
 * user gets the studio alone, an administrator gets both plus the admin
 * surface. None of them is a different application: the transcription studio
 * below is the same component in every case, mounted once.
 *
 * GATES ARE CAPABILITIES, NOT ROLE NAMES. `capability={CAPABILITY.WORKFORCE}`
 * rather than a role comparison, so a role added on the server reaches the
 * right routes without a frontend change. The server enforces the same rules
 * independently; these gates only decide where someone lands.
 *
 * /studio KEEPS THE OLD BEHAVIOUR. Before Phase 3 the entire app was
 * `<Dashboard />`. That surface still exists at its own route and is the
 * home page for a tool user.
 */
export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          {/* Workforce surface -- taskers and administrators. */}
          <Route
            path="/"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <AppShell><WorkerDashboard /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/tasks"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <AppShell><TaskQueue key="all" /></AppShell>
              </RequireAuth>
            }
          />
          {/* ONE PAGE PER KIND. Router 6 has no regex in path params, so
              these are spelled out -- and they must sit ABOVE `/tasks/:taskId`,
              which would otherwise match "transcription" as a task id.

              EACH CARRIES ITS OWN KEY. All three routes render the same
              component in the same position, so without one React keeps a
              single instance alive across the move and the page number,
              search text and status filter follow the reader from one queue
              to the next. Supply is lopsided enough between them -- seventy
              translations against a handful of transcriptions -- that page 3
              of one is off the end of the other. */}
          <Route
            path="/tasks/transcription"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <AppShell><TaskQueue key="transcription" kind="transcription" /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/tasks/translation"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <AppShell><TaskQueue key="translation" kind="translation" /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/tasks/:taskId"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <AppShell><TaskWorkspace /></AppShell>
              </RequireAuth>
            }
          />

          {/* The standalone studio -- everyone who can sign in has it. */}
          <Route
            path="/studio"
            element={
              <RequireAuth capability={CAPABILITY.STUDIO}>
                <AppShell><Dashboard embedded /></AppShell>
              </RequireAuth>
            }
          />

          {/* Administrative surface. */}
          <Route
            path="/admin"
            element={
              <RequireAuth capability={CAPABILITY.ADMINISTRATION}>
                <AppShell><AdminOverview /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/tasks"
            element={
              <RequireAuth capability={CAPABILITY.ADMINISTRATION}>
                <AppShell><AdminTasks /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/workers"
            element={
              <RequireAuth capability={CAPABILITY.ADMINISTRATION}>
                <AppShell><AdminWorkers /></AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/accounts"
            element={
              <RequireAuth capability={CAPABILITY.ADMINISTRATION}>
                <AppShell><AdminAccounts /></AppShell>
              </RequireAuth>
            }
          />

          {/* Outside Eagle taskers are a separate bundle (tasker/DeskApp.jsx).
              Reaching their address from inside this app -- e.g. a tasker
              signing in on the workers' page -- is a full page load. */}
          <Route path={`${TASKER_BASE}/*`} element={<HardRedirect />} />

          <Route path="*" element={<NotFound />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

/**
 * Unknown route.
 *
 * Sends people to THEIR home rather than a fixed one — a tool user offered
 * "go to my work" would land on a route they cannot open and bounce again.
 */
function NotFound() {
  const { isAuthenticated, isChecking, homePath } = useAuth();

  if (isChecking) return null;
  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas px-4 text-center">
      <h1 className="text-lg font-semibold text-ink">That page does not exist</h1>
      <p className="max-w-sm text-xs text-ink-mute">
        The link may be out of date, or the task may have been removed.
      </p>
      <Link
        to={homePath}
        className="rounded-xl2 bg-brand px-4 py-2 text-xs font-medium text-white hover:bg-brand-strong"
      >
        Take me back
      </Link>
    </main>
  );
}
