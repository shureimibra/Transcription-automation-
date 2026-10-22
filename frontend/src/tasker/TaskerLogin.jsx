import React, { useCallback, useRef, useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { auth as authApi } from '../api/auth.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { Button, Field, inputClass } from '../components/ui/index.jsx';
import AuthLayout, { OrDivider } from './AuthLayout.jsx';
import GoogleButton from './GoogleButton.jsx';
import { taskerPath } from './brand.js';

/**
 * Sign in: Google, or username and password.
 *
 * Shows the server's message verbatim. The server never says which half of
 * a wrong username/password pair was wrong, and this page does not guess.
 */
export default function TaskerLogin() {
  const { adopt, isAuthenticated, homePath } = useAuth();
  const location = useLocation();
  const loadOptions = useCallback(() => authApi.options(), []);
  const { data: options } = useAsync(loadOptions, []);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const usernameRef = useRef(null);

  if (isAuthenticated) {
    return <Navigate to={location.state?.from?.pathname || homePath || taskerPath.search} replace />;
  }

  const submit = async (event) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter your username and password.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      // The tasker door: a worker's or admin's password is refused here.
      adopt(await authApi.deskLogin(username.trim(), password));
    } catch (err) {
      setError(err.message || 'Sign-in failed. Please try again.');
      setPassword('');
      usernameRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <form onSubmit={submit} noValidate className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-ink">Sign in</h2>
          {options?.signup_open !== false && (
            <p className="mt-1 text-xs text-ink-mute">
              New here?{' '}
              <Link to={taskerPath.signup} className="font-medium text-accent hover:underline">
                Create an account
              </Link>
            </p>
          )}
        </div>
        {error && (
          <p role="alert"
             className="rounded-lg border border-status-rejected/30 bg-status-rejected/10 px-3 py-2 text-xs text-ink">
            {error}
          </p>
        )}
        {options?.google_client_id && (
          <>
            <GoogleButton clientId={options.google_client_id} onError={setError} />
            <OrDivider />
          </>
        )}
        <Field label="Username" htmlFor="t-username">
          <input id="t-username" ref={usernameRef} autoFocus autoComplete="username"
                 value={username} onChange={(e) => setUsername(e.target.value)}
                 disabled={submitting} className={inputClass} />
        </Field>
        <Field label="Password" htmlFor="t-password">
          <input id="t-password" type="password" autoComplete="current-password"
                 value={password} onChange={(e) => setPassword(e.target.value)}
                 disabled={submitting} className={inputClass} />
        </Field>
        <Button type="submit" variant="primary" size="lg" loading={submitting} className="w-full">
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}
