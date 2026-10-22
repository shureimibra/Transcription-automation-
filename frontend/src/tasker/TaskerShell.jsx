import React, { useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext.jsx';
import { useTheme } from '../lib/theme.jsx';
import { FOCUS } from '../components/ui/index.jsx';
import { MoonIcon, SunIcon } from '../components/icons.jsx';
import { PRODUCT_NAME, taskerPath } from './brand.js';
import VerifyEmail from './VerifyEmail.jsx';

/**
 * The frame around every Eagle tasker page.
 *
 * NOT AppShell. That one carries the workforce navigation, the queue
 * status and the Shurz brand; none of it means anything to an outside
 * tasker, and some of it would describe our own operation to them.
 *
 * THE LOOK IS SWITCHED HERE. While a tasker page is mounted, <html> carries
 * `theme-desk`, which re-points every colour token at the landing-page
 * palette (index.css). Removed on unmount, so an admin who opens a tasker
 * page and goes back to the workforce screens gets the indigo back.
 */
export function useEagleTheme() {
  useEffect(() => {
    const root = document.documentElement;
    const previousTitle = document.title;
    root.classList.add('theme-desk');
    document.title = PRODUCT_NAME;
    return () => {
      root.classList.remove('theme-desk');
      document.title = previousTitle;
    };
  }, []);
}

export default function TaskerShell({ children }) {
  useEagleTheme();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';

  const signOut = async () => {
    await logout();
    navigate(taskerPath.login, { replace: true });
  };

  return (
    <div className="min-h-screen bg-canvas text-ink">
      {/* Studio Ink in both themes: the one constant dark band, like the
          landing page's own header. */}
      <header className="bg-[#17151A] text-[#F7F3EC]">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link to={taskerPath.search}
                className={`flex items-center gap-2 rounded-lg ${FOCUS}`}>
            <span aria-hidden="true"
                  className="flex h-7 w-7 items-center justify-center rounded-md bg-[#C2542D] font-display text-sm font-semibold text-white">
              {PRODUCT_NAME.charAt(0)}
            </span>
            <span className="font-display text-base font-semibold tracking-tight">{PRODUCT_NAME}</span>
          </Link>
          <div className="flex items-center gap-1 text-xs">
            {user && <span className="mr-2 hidden text-[#F7F3EC]/70 sm:inline">{user.username}</span>}
            <button type="button" onClick={toggle}
                    aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}
                    className={`rounded-lg p-2 text-[#F7F3EC]/70 hover:bg-white/10 hover:text-[#F7F3EC] ${FOCUS}`}>
              {theme === 'dark' ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
            </button>
            {user && (
              <button type="button" onClick={signOut}
                      className={`rounded-lg px-3 py-2 font-medium text-[#F7F3EC]/80 hover:bg-white/10 hover:text-[#F7F3EC] ${FOCUS}`}>
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
        {/* Nothing behind the fence until the email is confirmed. The server
            refuses every task call too; this just says what to do about it. */}
        {user && user.emailVerified === false ? <VerifyEmail /> : children}
      </main>
    </div>
  );
}
