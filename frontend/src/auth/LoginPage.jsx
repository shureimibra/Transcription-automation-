import React, { useRef, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext.jsx';
import { Button, Field, LiveRegion, FOCUS } from '../components/ui/index.jsx';
import { BrandLockup, BrandMark } from '../components/Brand.jsx';

/**
 * The sign-in screen.
 *
 * WHAT IT DELIBERATELY DOES NOT SAY. The backend already refuses to
 * distinguish a wrong password from an unknown username, and this screen
 * keeps that promise: it shows the server's message verbatim rather than
 * elaborating. With six accounts, "no such user" would be a free list of
 * who works here.
 *
 * The one case that IS specific is a suspended account (403), because that
 * is something the person can act on -- they need to contact an
 * administrator, not keep retrying their password.
 *
 * WHY THERE IS NO "CREATE AN ACCOUNT" AND NO "FORGOT PASSWORD". Neither
 * flow exists: accounts are made by an administrator and passwords are reset
 * by one. A link to a door that does not open is worse than no link -- it
 * sends someone away from the one thing that would actually help them,
 * which is asking the person who can do it.
 */
export default function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const location = useLocation();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const usernameRef = useRef(null);

  if (isAuthenticated) {
    const to = location.state?.from?.pathname || '/';
    return <Navigate to={to} replace />;
  }

  const validate = () => {
    const errors = {};
    if (!username.trim()) errors.username = 'Enter your username.';
    if (!password) errors.password = 'Enter your password.';
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    if (!validate()) return;

    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      // The server's own client-safe message. Never a status code, never a
      // stack trace, never a hint about which half was wrong.
      setError(err.message || 'Sign-in failed. Please try again.');
      setPassword('');
      usernameRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  };

  // A softer field than the one used inside the app: this screen has two
  // inputs and nothing else, so they can afford the room.
  const field =
    'w-full rounded-xl2 border border-transparent bg-overlay px-4 py-3 text-sm text-ink ' +
    'placeholder:text-ink-mute transition-colors hover:bg-edge/60 ' +
    'focus:border-accent focus:bg-surface disabled:opacity-50 ' + FOCUS;

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-hero-brand px-4 py-10">
      {/* Two soft light sources, drawn rather than imported. The reference
          screen we were shown puts a flat two-colour gradient behind the
          card; a pair of blurred discs gives the same warmth with somewhere
          for the eye to rest, and costs no network request. */}
      <span aria-hidden="true" className="pointer-events-none absolute -left-40 -top-40 h-[32rem] w-[32rem] rounded-full bg-white/10 blur-3xl" />
      <span aria-hidden="true" className="pointer-events-none absolute -bottom-48 -right-32 h-[36rem] w-[36rem] rounded-full bg-black/20 blur-3xl" />

      <div className="relative w-full max-w-4xl overflow-hidden rounded-[1.75rem] bg-surface shadow-lift">
        <div className="grid md:grid-cols-2">
          {/* The illustration half. Hidden on a phone rather than shrunk:
              on a 5-inch screen it would push the password field below the
              fold, and this screen has exactly one job. */}
          <div className="relative hidden flex-col justify-between bg-raised p-10 md:flex">
            <BrandLockup size="lg" />

            <Illustration />

            <p className="text-2xs leading-relaxed text-ink-mute">
              Claim a task, work it against the audio, and submit — the
              platform clock starts the moment you claim, so the transcript
              is prepared before you arrive.
            </p>
          </div>

          {/* The form half. */}
          <div className="flex flex-col justify-center p-8 sm:p-10">
            <div className="mb-8 md:hidden">
              <BrandMark className="h-10 w-10" letter="text-xl" />
            </div>

            <h1 className="text-2xl font-semibold tracking-tight text-ink">Member login</h1>
            <p className="mt-1.5 text-xs text-ink-mute">
              Sign in to view and process your assigned tasks.
            </p>

            <form onSubmit={handleSubmit} noValidate className="mt-7 space-y-4">
              {error && (
                <div
                  role="alert"
                  className="rounded-xl2 border border-status-rejected/30 bg-status-rejected/10 px-4 py-3"
                >
                  <p className="text-xs text-ink">{error}</p>
                </div>
              )}

              <Field label="Username" htmlFor="username" error={fieldErrors.username}>
                <input
                  id="username"
                  ref={usernameRef}
                  type="text"
                  autoComplete="username"
                  autoFocus
                  placeholder="Your username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={submitting}
                  aria-invalid={Boolean(fieldErrors.username)}
                  aria-describedby={fieldErrors.username ? 'username-error' : undefined}
                  className={field}
                />
              </Field>

              <Field label="Password" htmlFor="password" error={fieldErrors.password}>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="Your password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                    aria-invalid={Boolean(fieldErrors.password)}
                    aria-describedby={fieldErrors.password ? 'password-error' : undefined}
                    className={`${field} pr-16`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    disabled={submitting}
                    // aria-pressed rather than a label swap alone, so the
                    // control announces its state rather than only its next
                    // action.
                    aria-pressed={showPassword}
                    className={`absolute inset-y-0 right-0 rounded-r-xl2 px-4 text-2xs font-semibold
                      text-ink-mute hover:text-ink ${FOCUS}`}
                  >
                    {showPassword ? 'Hide' : 'Show'}
                  </button>
                </div>
              </Field>

              <Button
                type="submit"
                variant="primary"
                size="lg"
                loading={submitting}
                className="w-full rounded-xl2 py-3"
              >
                {submitting ? 'Signing in…' : 'Sign in'}
              </Button>

              <LiveRegion message={submitting ? 'Signing in' : error ? `Sign-in failed. ${error}` : ''} />
            </form>

            <p className="mt-8 border-t border-edge pt-5 text-2xs text-ink-mute">
              Accounts and password resets are handled by your administrator.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}

/**
 * The illustration.
 *
 * DRAWN, NOT SOURCED. It inherits the theme's tokens, so it is legible in
 * both themes without a second asset, and there is nothing to fail to load
 * on the mobile hotspot our taskers actually work on.
 *
 * WHAT IT SHOWS is the work: a waveform under a cursor, with the transcript
 * lines already filled in beneath it. The reference screen we were shown
 * draws a generic person-in-a-laptop; a picture of the actual job is worth
 * more than a stock avatar on the screen someone sees before every shift.
 */
function Illustration() {
  const bars = [10, 22, 34, 18, 40, 28, 46, 30, 20, 38, 26, 14, 32, 24, 42, 16, 28, 20];
  return (
    <div className="relative my-10 flex items-center justify-center" aria-hidden="true">
      <span className="absolute h-52 w-52 rounded-full bg-accent-soft" />

      <svg viewBox="0 0 260 180" className="relative w-full max-w-[16rem]" fill="none">
        {/* The screen. */}
        <rect x="30" y="18" width="200" height="120" rx="10"
              className="fill-brand" />
        <rect x="40" y="28" width="180" height="100" rx="6"
              className="fill-surface" />

        {/* The waveform, mid-play. */}
        <g className="fill-accent">
          {bars.map((h, i) => (
            <rect
              key={i}
              x={50 + i * 9}
              y={72 - h / 2}
              width="4"
              height={h}
              rx="2"
              opacity={i < 8 ? 1 : 0.32}
            />
          ))}
        </g>
        {/* The playhead. */}
        <line x1="122" y1="40" x2="122" y2="104" strokeWidth="2" strokeLinecap="round"
              className="stroke-money" />

        {/* Transcript lines already written underneath. */}
        <g className="fill-edge">
          <rect x="50" y="112" width="86" height="5" rx="2.5" />
          <rect x="142" y="112" width="48" height="5" rx="2.5" />
          <rect x="50" y="122" width="62" height="5" rx="2.5" />
        </g>

        {/* The stand. */}
        <path d="M96 138h68l10 12H86l10-12Z" className="fill-brand" opacity="0.85" />
        <rect x="74" y="150" width="112" height="5" rx="2.5" className="fill-edge" />

        {/* Floating marks, echoing the reference without copying it. */}
        <circle cx="22" cy="46" r="6" strokeWidth="3" className="stroke-accent" />
        <path d="m236 62 10 6-10 6z" className="fill-money" />
        <rect x="212" y="20" width="9" height="9" rx="2" strokeWidth="2.5" className="stroke-edging" />
        <path d="m18 128 7 12H11z" strokeWidth="2.5" strokeLinejoin="round" className="stroke-money" />
        <circle cx="240" cy="128" r="4" className="fill-accent" />
      </svg>
    </div>
  );
}
