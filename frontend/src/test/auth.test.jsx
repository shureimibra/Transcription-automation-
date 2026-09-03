import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../auth/AuthContext.jsx';
import LoginPage from '../auth/LoginPage.jsx';
import RequireAuth from '../auth/RequireAuth.jsx';
import { auth as authApi } from '../api/workforce.js';
import { ApiError, getToken, setToken } from '../api/client.js';

// What these tests protect: the login screen must never leak more than the
// server said, and the guard must not bounce an authenticated user to /login
// while their session is still being checked.

function renderApp(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={<RequireAuth><div>Worker home</div></RequireAuth>}
          />
          <Route
            path="/admin"
            element={<RequireAuth adminOnly><div>Admin only</div></RequireAuth>}
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('authentication', () => {
  it('sends an unauthenticated visitor to the login screen', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));

    renderApp('/');

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('does not bounce to login while the session is still being checked', async () => {
    // A `me` call that never settles: the guard must show its checking state
    // rather than deciding the user is anonymous.
    vi.spyOn(authApi, 'me').mockImplementation(() => new Promise(() => {}));

    renderApp('/');

    expect(screen.getByText('Checking your session')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument();
  });

  it('lets an authenticated worker through', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      username: 'ana', is_admin: false, profile: { worker_code: 'W-01', display_name: 'Ana' },
    });

    renderApp('/');

    expect(await screen.findByText('Worker home')).toBeInTheDocument();
  });

  it('keeps a worker out of admin routes', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      username: 'ana', is_admin: false, profile: { worker_code: 'W-01' },
    });

    renderApp('/admin');

    await waitFor(() => expect(screen.queryByText('Admin only')).not.toBeInTheDocument());
  });

  it('shows the server message verbatim on a failed sign-in', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));
    vi.spyOn(authApi, 'login').mockRejectedValue(
      new ApiError('Invalid credentials.', { status: 401 })
    );

    renderApp('/login');

    await userEvent.type(await screen.findByLabelText('Username'), 'ana');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Invalid credentials.');
    // Nothing about which half was wrong, and no status code on screen.
    expect(alert.textContent).not.toMatch(/username|password|401/i);
  });

  it('validates before it calls the server', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));
    const login = vi.spyOn(authApi, 'login');

    renderApp('/login');

    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }));

    expect(login).not.toHaveBeenCalled();
    expect(screen.getByText('Enter your username.')).toBeInTheDocument();
    expect(screen.getByText('Enter your password.')).toBeInTheDocument();
  });

  it('toggles password visibility without losing what was typed', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));

    renderApp('/login');

    const password = await screen.findByLabelText('Password');
    await userEvent.type(password, 'secret-phrase');
    expect(password).toHaveAttribute('type', 'password');

    await userEvent.click(screen.getByRole('button', { name: 'Show' }));
    expect(password).toHaveAttribute('type', 'text');
    expect(password).toHaveValue('secret-phrase');
  });

  it('stores the token on a successful sign-in', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));
    vi.spyOn(authApi, 'login').mockResolvedValue({
      token: 'abc123',
      user: { username: 'ana', is_admin: false },
      profile: { worker_code: 'W-01' },
    });

    renderApp('/login');

    await userEvent.type(await screen.findByLabelText('Username'), 'ana');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse-battery');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(getToken()).toBe('abc123'));
  });

  it('clears the stored token when any request comes back 401', async () => {
    setToken('stale-token');
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('expired', { status: 401 }));

    renderApp('/');

    // The provider's own bootstrap failure is enough to end the session.
    await waitFor(() => expect(getToken()).toBeNull());
  });
});
