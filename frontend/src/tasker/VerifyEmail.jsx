import React, { useState } from 'react';
import { auth as authApi } from '../api/auth.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { Button, inputClass } from '../components/ui/index.jsx';

/**
 * "Check your email": the 6-digit code that confirms a new account.
 *
 * Shown in place of every tasker page until the code is accepted. The
 * server enforces the same rule on every task endpoint, so skipping this
 * screen in the browser gets nothing.
 */
export default function VerifyEmail() {
  const { markEmailVerified } = useAuth();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState({ text: '', tone: 'info' });

  const submit = async (event) => {
    event.preventDefault();
    if (!/^\d{6}$/.test(code)) {
      setMessage({ text: 'Enter the 6-digit code from the email.', tone: 'error' });
      return;
    }
    setBusy(true);
    setMessage({ text: '', tone: 'info' });
    try {
      await authApi.verifyEmail(code);
      markEmailVerified();
    } catch (err) {
      setMessage({ text: err.message, tone: 'error' });
      setCode('');
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setMessage({ text: '', tone: 'info' });
    try {
      await authApi.resendCode();
      setMessage({ text: 'A new code is on its way. The old one no longer works.', tone: 'info' });
    } catch (err) {
      setMessage({ text: err.message, tone: 'error' });
    }
  };

  return (
    <div className="mx-auto max-w-sm pt-6 sm:pt-12">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-accent">One last step</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">Check your email</h1>
      <p className="mt-2 text-sm text-ink-dim">
        We sent a 6-digit code to the address you signed up with. It expires in 15 minutes.
      </p>
      <form onSubmit={submit} className="mt-6 space-y-3">
        <label htmlFor="otp" className="sr-only">Confirmation code</label>
        <input
          id="otp" autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6}
          value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
          placeholder="000000"
          className={`${inputClass} text-center font-mono text-2xl tracking-[0.5em]`}
        />
        <Button type="submit" variant="primary" size="lg" loading={busy} className="w-full">
          Confirm
        </Button>
      </form>
      {message.text && (
        <p role={message.tone === 'error' ? 'alert' : 'status'}
           className={`mt-3 rounded-lg border px-3 py-2 text-xs ${message.tone === 'error'
             ? 'border-status-rejected/30 bg-status-rejected/10 text-ink'
             : 'border-edge bg-surface text-ink-dim'}`}>
          {message.text}
        </p>
      )}
      <p className="mt-6 text-xs text-ink-mute">
        No email? Check spam, or{' '}
        <button type="button" onClick={resend} className="font-medium text-accent hover:underline">
          send a new code
        </button>.
      </p>
    </div>
  );
}
