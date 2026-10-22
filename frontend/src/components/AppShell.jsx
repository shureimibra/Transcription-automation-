import React, { useEffect, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { work } from '../api/eagle.js';
import { CAPABILITY, useAuth } from '../auth/AuthContext.jsx';
import { useTheme } from '../lib/theme.jsx';
import { Button, FOCUS } from './ui/index.jsx';
import {
  GaugeIcon, HomeIcon, KeyIcon, LanguagesIcon, ListIcon, LogOutIcon,
  MenuIcon, MicIcon, MoonIcon, StudioIcon, SunIcon, UsersIcon,
} from './icons.jsx';
import { BrandLockup, BrandMark } from './Brand.jsx';

// The frame every authenticated screen sits in.
//
// NAVIGATION IS BUILT FROM THE SERVER'S CAPABILITY LIST, not from a role
// string this file interprets. Someone is never shown a link to something
// the server will refuse, because an interface that offers impossible
// actions teaches people to distrust it. The server still refuses
// independently -- hiding a link is courtesy, not security.
//
// A tool user therefore sees no queue and no admin links at all: their
// capability list contains only `studio`.
//
// THE SIDEBAR LISTS WHAT WE HAVE. It is shaped like the platform our
// taskers already spend their day in, because moving between two tools with
// the same skeleton costs nothing to learn. It does NOT copy that
// platform's contents: there is no wallet here, no minutes to buy, no payout
// history, because none of those are ours to show. A nav entry that leads
// somewhere empty is worse than no nav entry.

function NavItem({ to, icon, children, end = false, collapsed = false }) {
  return (
    <NavLink
      to={to}
      end={end}
      title={collapsed ? children : undefined}
      className={({ isActive }) =>
        `group relative flex items-center gap-3 rounded-xl2 px-3 py-2 text-sm font-medium
         transition-colors ${FOCUS} ${
           isActive
             ? 'bg-accent-soft text-accent-ink'
             : 'text-ink-dim hover:bg-overlay hover:text-ink'
         } ${collapsed ? 'justify-center px-2' : ''}`
      }
    >
      {({ isActive }) => (
        <>
          {/* The active marker is a rail, not a colour change alone --
              readable without relying on hue. */}
          <span
            aria-hidden="true"
            className={`absolute -left-3 h-6 w-1 rounded-r-full transition-colors ${
              isActive ? 'bg-brand' : 'bg-transparent'
            }`}
          />
          <span className="shrink-0">{icon}</span>
          {/* The label stays in the document when the rail is collapsed,
              hidden only visually: it is the link's accessible name, and a
              screen reader must not lose it to a layout preference. */}
          <span className={collapsed ? 'sr-only' : 'truncate'}>{children}</span>
        </>
      )}
    </NavLink>
  );
}

function NavSection({ title, collapsed, children }) {
  return (
    <div className="space-y-1">
      {collapsed ? (
        <div className="mx-auto my-2 h-px w-6 bg-edge" aria-hidden="true" />
      ) : (
        <p className="px-3 pb-1 pt-2 text-2xs font-semibold uppercase tracking-[0.14em] text-ink-mute">
          {title}
        </p>
      )}
      {children}
    </div>
  );
}

export default function AppShell({ children }) {
  const { user, profile, can, role, logout } = useAuth();
  const inWorkforce = can(CAPABILITY.WORKFORCE);
  const isAdministrator = can(CAPABILITY.ADMINISTRATION);
  const navigate = useNavigate();

  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const name = profile?.display_name || user?.username || '';
  const roleLabel = isAdministrator
    ? 'Administrator'
    : profile?.worker_code || (role === 'tool' ? 'Studio access' : 'Worker');

  const railWidth = collapsed ? 'lg:w-[76px]' : 'lg:w-[248px]';
  const contentInset = collapsed ? 'lg:pl-[76px]' : 'lg:pl-[248px]';

  return (
    <div className="min-h-screen bg-canvas text-ink">
      {/* Skip link: the transcript workspace is a long document, and a
          keyboard user should not have to tab the whole nav to reach it. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50
          focus:rounded-lg focus:bg-brand focus:px-3 focus:py-2 focus:text-xs focus:text-white"
      >
        Skip to content
      </a>

      {/* The drawer scrim, on narrow screens only. */}
      {drawer && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setDrawer(false)}
          className="fixed inset-0 z-40 bg-ink/40 backdrop-blur-sm lg:hidden"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[248px] flex-col border-r border-edge
          bg-surface transition-transform duration-200 lg:translate-x-0 ${railWidth}
          ${drawer ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className={`flex items-center px-5 py-5 ${collapsed ? 'lg:justify-center lg:px-3' : ''}`}>
          {/* Collapsed, the wordmark would be clipped mid-letter, so the
              mark stands alone -- it is designed to. */}
          {collapsed
            ? <BrandMark className="h-9 w-9" />
            : <BrandLockup />}
        </div>

        <nav
          aria-label="Main"
          className={`flex-1 space-y-2 overflow-y-auto px-3 pb-4 ${collapsed ? 'lg:px-2' : ''}`}
        >
          {inWorkforce && (
            <NavSection title="Work" collapsed={collapsed}>
              <NavItem to="/" end icon={<HomeIcon />} collapsed={collapsed}>My work</NavItem>
              <NavItem to="/tasks/transcription" icon={<MicIcon />} collapsed={collapsed}>Transcription</NavItem>
              <NavItem to="/tasks/translation" icon={<LanguagesIcon />} collapsed={collapsed}>Translation</NavItem>
              <NavItem to="/tasks" end icon={<ListIcon />} collapsed={collapsed}>All tasks</NavItem>
            </NavSection>
          )}

          {/* WHO THE STUDIO IS FOR.
              It exists for someone sourcing their own work: paste a URL,
              transcribe, export. That is a tool user -- a population that
              includes people who pay for the tool and taskers from the
              source platform who are not ours.

              Our own taskers are not that. Their work arrives in the queue
              already transcribed, so the studio offers them nothing they
              need -- and its screens name the source platform throughout,
              which is exactly what the worker surface keeps out of view.

              Hence: everyone EXCEPT a pure tasker. An administrator keeps it
              because they support both populations. */}
          {(!inWorkforce || isAdministrator) && (
            <NavSection title="Tools" collapsed={collapsed}>
              <NavItem to="/studio" end={!inWorkforce} icon={<StudioIcon />} collapsed={collapsed}>Studio</NavItem>
            </NavSection>
          )}

          {isAdministrator && (
            <NavSection title="Administration" collapsed={collapsed}>
              <NavItem to="/admin" end icon={<GaugeIcon />} collapsed={collapsed}>Overview</NavItem>
              <NavItem to="/admin/tasks" icon={<ListIcon />} collapsed={collapsed}>Tasks</NavItem>
              <NavItem to="/admin/workers" icon={<UsersIcon />} collapsed={collapsed}>Workers</NavItem>
              <NavItem to="/admin/accounts" icon={<KeyIcon />} collapsed={collapsed}>Accounts</NavItem>
            </NavSection>
          )}
        </nav>

        <div className={`border-t border-edge p-3 ${collapsed ? 'lg:px-2' : ''}`}>
          <button
            type="button"
            onClick={handleLogout}
            className={`flex w-full items-center gap-3 rounded-xl2 px-3 py-2 text-sm font-medium
              text-ink-dim transition-colors hover:bg-overlay hover:text-ink ${FOCUS}
              ${collapsed ? 'lg:justify-center lg:px-2' : ''}`}
          >
            <LogOutIcon />
            <span className={collapsed ? 'sr-only' : ''}>Sign out</span>
          </button>
        </div>

        {/* The collapse handle, on the rail's edge. Desktop only: on a phone
            the rail is a drawer and collapsing it means nothing. */}
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-expanded={!collapsed}
          className={`absolute right-0 top-7 hidden h-6 w-6 translate-x-1/2 items-center justify-center
            rounded-full border border-edge bg-surface text-ink-mute shadow-panel
            transition-colors hover:text-ink lg:flex ${FOCUS}`}
        >
          <svg
            className={`h-3.5 w-3.5 transition-transform ${collapsed ? 'rotate-180' : ''}`}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
          >
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
      </aside>

      <div className={`transition-[padding] duration-200 ${contentInset}`}>
        <header className="sticky top-0 z-30 px-4 pt-4 sm:px-6">
          <div className="mx-auto flex max-w-[1500px] items-center gap-3 rounded-xl2 border border-edge
                          bg-surface/90 px-3 py-2.5 shadow-panel backdrop-blur">
            <button
              type="button"
              onClick={() => setDrawer(true)}
              aria-label="Open navigation"
              className={`rounded-lg p-2 text-ink-dim hover:bg-overlay hover:text-ink lg:hidden ${FOCUS}`}
            >
              <MenuIcon className="h-5 w-5" />
            </button>

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-ink">
                {name ? `Hi, ${name}` : 'Workbench'}
              </p>
              <p className="truncate text-2xs text-ink-mute">
                {/* Their own role, named. Someone who cannot see the queue
                    should be able to tell at a glance that this is why,
                    rather than assuming the page is broken. */}
                {roleLabel}
              </p>
            </div>

            <ThemeToggle />

            <span
              className="relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full
                         bg-accent-soft text-sm font-semibold text-accent-ink"
              aria-hidden="true"
            >
              {(name || '?').trim().charAt(0).toUpperCase()}
              <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-money ring-2 ring-surface" />
            </span>
          </div>
        </header>

        {isAdministrator && <SessionHealth />}

        <main id="main" className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">
          {children}
        </main>

        {/* THE FOOTER NAMES NOBODY.
            It used to say where task minutes and payment are held, which
            meant printing the source platform's name on every screen a
            tasker looks at. That is the same leak `source_url` is kept off
            the wire to prevent: a worker claims, works and submits entirely
            through this application, and the platform behind it is not
            theirs to know, act on, or go around. The line was also just
            noise -- nobody needs billing trivia on a queue.
            The year is computed. A hardcoded one is a bug that files itself
            every January. */}
        <footer className="mx-auto max-w-[1500px] px-4 pb-8 sm:px-6">
          <p className="border-t border-edge pt-5 text-2xs text-ink-mute">
            © {new Date().getFullYear()} Shurz
          </p>
        </footer>
      </div>
    </div>
  );
}

/**
 * Light or dark.
 *
 * Labelled with where it is GOING, not where it is, because that is what a
 * person is deciding when they reach for it.
 */
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className={`rounded-lg p-2 text-ink-mute transition-colors hover:bg-overlay hover:text-ink ${FOCUS}`}
    >
      {theme === 'dark' ? <SunIcon className="h-5 w-5" /> : <MoonIcon className="h-5 w-5" />}
    </button>
  );
}

/**
 * Platform trouble the operator needs to see, said out loud.
 *
 * TWO FAILURES, ONE POLL. Both freeze the queue in a way that looks like
 * nothing is wrong:
 *
 *   * A DEAD SESSION. When the Eagle cookie expires the poller stops
 *     refreshing anything -- the queue does not empty, it FREEZES, showing
 *     yesterday's tasks confidently while a tasker claims work taken hours
 *     ago.
 *   * NO MINUTES. A zero balance leaves every task showing "Ready now"
 *     while every claim fails with "not enough platform minutes". The team
 *     pounds on work nobody can start; the per-claim message says "an
 *     administrator has been notified" and, until this banner, that was not
 *     actually true.
 *
 * Admin-only, because it is the operator who fixes both and the response
 * carries operational state a tasker has no use for.
 */
function SessionHealth() {
  const [dead, setDead] = useState([]);
  const [noMinutes, setNoMinutes] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const data = await work.status();
        if (cancelled) return;
        setDead((data.platform || []).filter((p) => p.status === 'expired'));
        setNoMinutes(Boolean(data.minutes_exhausted));
      } catch {
        // A failing status call is not itself evidence of trouble;
        // claiming one would be its own false alarm.
      }
    };
    check();
    const timer = setInterval(check, 60000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  if (dead.length === 0 && !noMinutes) return null;

  return (
    <div className="mx-auto max-w-[1500px] space-y-3 px-4 pt-4 sm:px-6">
      {dead.length > 0 && (
        <div
          role="alert"
          className="rounded-xl2 border border-status-rejected/30 bg-status-rejected/10 px-4 py-3"
        >
          <p className="text-xs text-ink">
            <span className="font-semibold">
              {dead.map((p) => p.product).join(' and ')} session expired.
            </span>{' '}
            New tasks are not being discovered and claimed tasks are not
            disappearing, so these counts are stale. Sign in to that platform
            again and store a fresh cookie.
          </p>
        </div>
      )}

      {noMinutes && (
        <div
          role="alert"
          className="rounded-xl2 border border-status-rejected/30 bg-status-rejected/10 px-4 py-3"
        >
          <p className="text-xs text-ink">
            <span className="font-semibold">The platform is out of minutes.</span>{' '}
            Tasks still show as ready, but every claim is failing with
            &ldquo;not enough platform minutes&rdquo; and will keep failing
            until the Eagle account is topped up.
          </p>
        </div>
      )}
    </div>
  );
}
