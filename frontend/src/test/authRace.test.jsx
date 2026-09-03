import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../auth/AuthContext.jsx';
import LoginPage from '../auth/LoginPage.jsx';
import RequireAuth from '../auth/RequireAuth.jsx';
import { auth as authApi } from '../api/workforce.js';
import { api, ApiError, getToken, setToken, setUnauthorizedHandler } from '../api/client.js';

/**
 * Regression tests for two defects a real browser found and the rest of the
 * suite could not.
 *
 * Both come from the same root cause: a 401 was treated as "your session
 * ended" no matter which request produced it. On the authentication
 * endpoints a 401 is the ANSWER -- "those credentials are wrong", or "you
 * are not signed in yet" -- and handling it as an expiry produced both a
 * misleading message and a race that logged people out of a session they
 * had just successfully created.
 */

function renderApp(initialPath = '/login') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<RequireAuth><div>Worker home</div></RequireAuth>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('a 401 from the auth endpoints is an answer, not an expiry', () => {
  it("shows the server's reason for a rejected sign-in", async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));
    vi.spyOn(authApi, 'login').mockRejectedValue(
      new ApiError('Invalid credentials.', { status: 401 })
    );

    renderApp();

    await userEvent.type(await screen.findByLabelText('Username'), 'ana');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    const alert = await screen.findByRole('alert');
    // Telling someone trying to START a session that theirs has ENDED is
    // advice they cannot act on.
    expect(alert).toHaveTextContent('Invalid credentials.');
    expect(alert.textContent).not.toMatch(/session has ended/i);
  });

  it('does not fire the global session-ended handler for a failed sign-in', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 401, ok: false,
      headers: { get: () => 'application/json' },
      json: async () => ({ error: 'Invalid credentials.' }),
    }));

    const err = await authApi.login('ana', 'wrong').catch((e) => e);

    expect(err.message).toBe('Invalid credentials.');
    expect(onUnauthorized).not.toHaveBeenCalled();
    setUnauthorizedHandler(null);
  });

  it('still ends the session on a 401 from any other request', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken('tok-1');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 401, ok: false,
      headers: { get: () => 'application/json' },
      json: async () => ({ detail: 'expired' }),
    }));

    await expect(api.get('/workforce/tasks/')).rejects.toThrow(/session has ended/i);

    // The opt-out is per-call and must not have leaked into normal requests.
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
    setUnauthorizedHandler(null);
  });

  it('a slow bootstrap 401 does not cancel a sign-in that already succeeded', async () => {
    // The real sequence: AuthProvider asks "who am I" on mount, the person
    // signs in before that answer arrives, and the stale 401 then lands.
    // Playwright hit this every run; a human on a slow connection hits it too.
    let rejectMe;
    vi.spyOn(authApi, 'me').mockReturnValue(
      new Promise((_, reject) => { rejectMe = reject; })
    );
    vi.spyOn(authApi, 'login').mockResolvedValue({
      token: 'fresh-token',
      user: { username: 'ana', is_admin: false },
      profile: { worker_code: 'W-01' },
    });

    renderApp();

    await userEvent.type(await screen.findByLabelText('Username'), 'ana');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse-battery');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(getToken()).toBe('fresh-token'));

    // NOW the bootstrap answer arrives, late and stale.
    rejectMe(new ApiError('no session', { status: 401 }));
    await new Promise((r) => setTimeout(r, 50));

    // The session that sign-in established must survive it.
    expect(getToken()).toBe('fresh-token');
    expect(await screen.findByText('Worker home')).toBeInTheDocument();
  });
});
