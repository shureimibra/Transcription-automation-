import React, { useCallback, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { auth as authApi } from '../api/auth.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { Button, Field, inputClass } from '../components/ui/index.jsx';
import AuthLayout, { OrDivider } from './AuthLayout.jsx';
import GoogleButton from './GoogleButton.jsx';
import { taskerPath } from './brand.js';

/** What a password needs. The server checks the same rules (tasker_auth.py). */
const PASSWORD_RULES = [
  { label: '8 or more characters', test: (p) => p.length >= 8 },
  { label: 'An uppercase letter', test: (p) => /[A-Z]/.test(p) },
  { label: 'A lowercase letter', test: (p) => /[a-z]/.test(p) },
  { label: 'A number', test: (p) => /[0-9]/.test(p) },
  { label: 'A symbol ( _ ! @ # - … )', test: (p) => /[^A-Za-z0-9]/.test(p) },
];

/**
 * Create an account. The server decides everything that matters -- the
 * account type, whether the username is acceptable, whether sign-up is open
 * at all (backend tasker_auth.SignupView); this page only collects the
 * fields and shows the server's answer beside the field it is about.
 */
export default function TaskerSignup() {
  const { adopt, isAuthenticated, homePath } = useAuth();
  const loadOptions = useCallback(() => authApi.options(), []);
  const { data: options } = useAsync(loadOptions, []);
  const [form, setForm] = useState({ username: '', email: '', password: '', confirm: '', website: '' });
  const [errors, setErrors] = useState({});
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (isAuthenticated) return <Navigate to={homePath || taskerPath.search} replace />;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    const local = {};
    if (!form.username.trim()) local.username = 'Choose a username.';
    if (!form.email.trim()) local.email = 'Enter your email.';
    if (PASSWORD_RULES.some((rule) => !rule.test(form.password))) {
      local.password = 'The password needs everything in the list below.';
    }
    if (form.password !== form.confirm) local.confirm = 'The passwords do not match.';
    setErrors(local);
    setError('');
    if (Object.keys(local).length) return;

    setSubmitting(true);
    try {
      adopt(await authApi.signup({
        username: form.username.trim(), email: form.email.trim(),
        password: form.password, website: form.website,
      }));
    } catch (err) {
      setErrors(err.payload?.fields || {});
      setError(err.message || 'Sign-up failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (options?.signup_open === false) {
    return (
      <AuthLayout>
        <h2 className="text-xl font-semibold text-ink">Sign-up is closed</h2>
        <p className="mt-2 text-sm text-ink-dim">New accounts are paused for now. Please check back soon.</p>
        <Link to={taskerPath.login} className="mt-4 inline-block text-xs font-medium text-accent hover:underline">
          ← Sign in
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form onSubmit={submit} noValidate className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-ink">Create your account</h2>
          <p className="mt-1 text-xs text-ink-mute">
            Already have one?{' '}
            <Link to={taskerPath.login} className="font-medium text-accent hover:underline">Sign in</Link>
          </p>
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
        <Field label="Username" htmlFor="s-username" error={errors.username}
               hint="Letters, numbers, dots, dashes or underscores.">
          <input id="s-username" autoFocus autoComplete="username" maxLength={30}
                 value={form.username} onChange={set('username')} className={inputClass} />
        </Field>
        <Field label="Email" htmlFor="s-email" error={errors.email}>
          <input id="s-email" type="email" autoComplete="email" maxLength={254}
                 value={form.email} onChange={set('email')} className={inputClass} />
        </Field>
        <Field label="Password" htmlFor="s-password" error={errors.password}>
          <input id="s-password" type="password" autoComplete="new-password" maxLength={128}
                 aria-describedby="s-password-rules"
                 value={form.password} onChange={set('password')} className={inputClass} />
        </Field>
        {/* Ticks as they type, so nobody learns the rules from an error. */}
        <ul id="s-password-rules" className="grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
          {PASSWORD_RULES.map((rule) => {
            const ok = rule.test(form.password);
            return (
              <li key={rule.label} className={`flex items-center gap-1.5 ${ok ? 'text-money-ink' : 'text-ink-mute'}`}>
                <span aria-hidden="true">{ok ? '✓' : '○'}</span>
                {rule.label}
              </li>
            );
          })}
        </ul>
        <Field label="Confirm password" htmlFor="s-confirm" error={errors.confirm}>
          <input id="s-confirm" type="password" autoComplete="new-password" maxLength={128}
                 value={form.confirm} onChange={set('confirm')} className={inputClass} />
        </Field>
        {/* Honeypot: hidden from people, filled in by bots. */}
        <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
          <label htmlFor="s-website">Website</label>
          <input id="s-website" tabIndex={-1} autoComplete="off"
                 value={form.website} onChange={set('website')} />
        </div>
        <Button type="submit" variant="primary" size="lg" loading={submitting} className="w-full">
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
