import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext.jsx';
import { Spinner } from '../components/ui/index.jsx';

/**
 * Route guard.
 *
 * THIS IS CONVENIENCE, NOT SECURITY. Every endpoint behind these routes is
 * independently authenticated and authorised by the backend; the guard exists
 * so people land somewhere sensible, not to keep anyone out. Removing it would
 * expose no data.
 *
 * IT GATES ON A CAPABILITY, NOT ON A ROLE NAME. `capability="administration"`
 * rather than `role === 'admin'`, so a role added later that should reach the
 * admin surface reaches it without editing this file. The capability list is
 * the server's answer, fetched on every load.
 *
 * The `checking` state matters: without it an authenticated user with a valid
 * token is bounced to /login for one frame while the bootstrap call is in
 * flight, losing wherever they were heading.
 */
export default function RequireAuth({ children, capability = null }) {
  const { isAuthenticated, isChecking, can, homePath } = useAuth();
  const location = useLocation();

  if (isChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-ink-mute">
        <Spinner className="h-5 w-5" />
        <span className="sr-only">Checking your session</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    // `from` so login can return them to where they were going.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (capability && !can(capability)) {
    // Send them to THEIR home rather than to a fixed one. A tool user
    // bounced from a workforce route must not be bounced to the worker
    // dashboard, which is another route they cannot use.
    return <Navigate to={homePath} replace />;
  }

  return children;
}
