import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider, CAPABILITY, useAuth } from '../auth/AuthContext.jsx';
import RequireAuth from '../auth/RequireAuth.jsx';
import AppShell from '../components/AppShell.jsx';
import { auth as authApi } from '../api/workforce.js';
import { ApiError } from '../api/client.js';

/**
 * The three experiences, from the frontend's side.
 *
 * WHAT THESE PROTECT. Route gating reads the server's CAPABILITY LIST, never
 * a role string this code interprets. That is what lets a role be added on
 * the server without editing the frontend — and it is exactly the kind of
 * indirection someone later "simplifies" into `role === 'tasker'`, which
 * silently breaks every role added after.
 *
 * None of this is security. The server refuses these endpoints
 * independently, and that is covered by the backend suite. These tests are
 * about people landing somewhere they can use.
 */

const ME = {
  tasker: {
    username: 'ana', role: 'tasker', is_admin: false,
    capabilities: ['measured', 'studio', 'workforce'],
    profile: { worker_code: 'W-01', display_name: 'Ana' },
  },
  tool: {
    username: 'toolonly', role: 'tool', is_admin: false,
    capabilities: ['studio'],
    profile: null,
  },
  admin: {
    username: 'boss', role: 'admin', is_admin: true,
    capabilities: ['administration', 'studio', 'workforce'],
    profile: null,
  },
};

function Harness({ initialPath }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>Login screen</div>} />
          <Route
            path="/"
            element={
              <RequireAuth capability={CAPABILITY.WORKFORCE}>
                <div>Worker dashboard</div>
              </RequireAuth>
            }
          />
          <Route
            path="/studio"
            element={
              <RequireAuth capability={CAPABILITY.STUDIO}>
                <div>Transcription studio</div>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth capability={CAPABILITY.ADMINISTRATION}>
                <div>Admin overview</div>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

const signedInAs = (who) => vi.spyOn(authApi, 'me').mockResolvedValue(ME[who]);

describe('capability-gated routing', () => {
  it('lets a tasker into the workforce surface', async () => {
    signedInAs('tasker');
    render(<Harness initialPath="/" />);
    expect(await screen.findByText('Worker dashboard')).toBeInTheDocument();
  });

  it('sends a tool user away from the workforce surface, to the studio', async () => {
    // Not to /login -- they are legitimately signed in -- and not to the
    // worker dashboard, which is another route they cannot use.
    signedInAs('tool');
    render(<Harness initialPath="/" />);
    expect(await screen.findByText('Transcription studio')).toBeInTheDocument();
    expect(screen.queryByText('Worker dashboard')).not.toBeInTheDocument();
  });

  it('lets a tool user into the studio directly', async () => {
    signedInAs('tool');
    render(<Harness initialPath="/studio" />);
    expect(await screen.findByText('Transcription studio')).toBeInTheDocument();
  });

  it('keeps a tool user out of the admin surface', async () => {
    signedInAs('tool');
    render(<Harness initialPath="/admin" />);
    await waitFor(() =>
      expect(screen.queryByText('Admin overview')).not.toBeInTheDocument()
    );
  });

  it('keeps a tasker out of the admin surface', async () => {
    signedInAs('tasker');
    render(<Harness initialPath="/admin" />);
    await waitFor(() =>
      expect(screen.queryByText('Admin overview')).not.toBeInTheDocument()
    );
  });

  it('lets an administrator into every surface', async () => {
    signedInAs('admin');
    render(<Harness initialPath="/admin" />);
    expect(await screen.findByText('Admin overview')).toBeInTheDocument();
  });

  it('still sends an anonymous visitor to login', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('no session', { status: 401 }));
    render(<Harness initialPath="/" />);
    expect(await screen.findByText('Login screen')).toBeInTheDocument();
  });

  it('gates on the capability list, not on the role name', async () => {
    // A role this frontend has never heard of, carrying the workforce
    // capability, must work. If someone rewrites the guard as
    // `role === 'tasker'`, this is the test that fails.
    vi.spyOn(authApi, 'me').mockResolvedValue({
      username: 'lead', role: 'team_lead', is_admin: false,
      capabilities: ['workforce', 'studio'], profile: null,
    });

    render(<Harness initialPath="/" />);

    expect(await screen.findByText('Worker dashboard')).toBeInTheDocument();
  });
});

describe('navigation reflects capabilities', () => {
  function Shell({ who }) {
    signedInAs(who);
    return (
      <MemoryRouter>
        <AuthProvider>
          <AppShell><div>content</div></AppShell>
        </AuthProvider>
      </MemoryRouter>
    );
  }

  it('offers a tasker their queue but no admin links', async () => {
    render(<Shell who="tasker" />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toContain('My work');
    // The two kinds are separate pages: different instructions, different
    // graded work, and supply so lopsided that a mixed list buries the
    // smaller kind entirely.
    expect(links).toContain('Transcription');
    expect(links).toContain('Translation');
    expect(links).not.toContain('Overview');
    expect(links).not.toContain('Accounts');
  });

  it('keeps the studio out of the navigation for a tasker', async () => {
    // The studio is for someone sourcing their own work -- a tool user, and
    // increasingly a paying one. A tasker's work arrives in the queue
    // already transcribed, and the studio's screens name the source platform
    // throughout, which the worker surface deliberately does not.
    render(<Shell who="tasker" />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).not.toContain('Studio');
  });

  it('keeps the studio for an administrator, who supports both populations', async () => {
    render(<Shell who="admin" />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toContain('Studio');
  });

  it('offers a tool user the studio and nothing else', async () => {
    render(<Shell who="tool" />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toEqual(['Studio']);
  });

  it('offers an administrator everything', async () => {
    render(<Shell who="admin" />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toContain('My work');
    expect(links).toContain('Overview');
    expect(links).toContain('Accounts');
  });

  it('tells a tool user what kind of account they have', async () => {
    // Someone who cannot see a queue should be able to tell that this is
    // why, rather than assuming the page failed to load.
    render(<Shell who="tool" />);
    expect(await screen.findByText('Studio access')).toBeInTheDocument();
  });
});

describe('useAuth', () => {
  function Probe() {
    const { can, role, homePath } = useAuth();
    return (
      <div>
        <span data-testid="role">{role || 'none'}</span>
        <span data-testid="home">{homePath}</span>
        <span data-testid="wf">{String(can(CAPABILITY.WORKFORCE))}</span>
      </div>
    );
  }

  const probe = (who) => {
    signedInAs(who);
    return render(
      <MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>
    );
  };

  it('reports a tasker home as the worker dashboard', async () => {
    probe('tasker');
    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('tasker'));
    expect(screen.getByTestId('home')).toHaveTextContent('/');
    expect(screen.getByTestId('wf')).toHaveTextContent('true');
  });

  it('reports a tool user home as the studio', async () => {
    probe('tool');
    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('tool'));
    expect(screen.getByTestId('home')).toHaveTextContent('/studio');
    expect(screen.getByTestId('wf')).toHaveTextContent('false');
  });
});
