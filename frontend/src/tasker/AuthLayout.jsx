import React from 'react';
import { PRODUCT_NAME } from './brand.js';
import { useEagleTheme } from './TaskerShell.jsx';
import DeskScene from './DeskScene.jsx';

/**
 * The frame shared by sign-in and sign-up: the product shown in motion on
 * the left (Studio Ink), the form on the right (Paper). On a phone the
 * scene shrinks to a single audio strip so the form stays near the top.
 */
export default function AuthLayout({ children }) {
  useEagleTheme();
  return (
    <main className="flex min-h-screen flex-col bg-canvas text-ink md:flex-row">
      <section className="relative flex flex-col justify-between gap-8 overflow-hidden bg-[#17151A] px-6 py-8 text-[#F7F3EC] md:w-1/2 md:px-10 md:py-12 lg:w-5/12">
        {/* A warm light behind the scene, so the dark panel has depth. */}
        <span aria-hidden="true"
              className="pointer-events-none absolute -left-24 top-1/3 h-80 w-80 rounded-full bg-[#C2542D]/20 blur-3xl" />
        <div className="relative flex items-center gap-2">
          <span aria-hidden="true"
                className="flex h-8 w-8 items-center justify-center rounded-md bg-[#C2542D] font-display text-base font-semibold text-white">
            {PRODUCT_NAME.charAt(0)}
          </span>
          <span className="font-display text-lg font-semibold tracking-tight">{PRODUCT_NAME}</span>
        </div>

        <div className="relative hidden md:block"><DeskScene /></div>

        <div className="relative max-w-sm">
          <h1 className="font-display text-3xl font-semibold leading-tight tracking-tight">
            Your task, already drafted.
          </h1>
          <div className="mt-4 md:hidden"><DeskScene compact /></div>
          <ol className="mt-6 space-y-3 text-sm text-[#F7F3EC]/75">
            <li className="flex gap-3">
              <span className="font-mono text-[#E2805C]">01</span>
              Claim your task and copy its link.
            </li>
            <li className="flex gap-3">
              <span className="font-mono text-[#E2805C]">02</span>
              Paste it here and open the prepared transcript or translation.
            </li>
            <li className="flex gap-3">
              <span className="font-mono text-[#E2805C]">03</span>
              Check it against the audio, then copy your final draft.
            </li>
          </ol>
        </div>
      </section>

      <section className="desk-paper flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm rounded-2xl border border-edge bg-surface/80 p-6 shadow-lift backdrop-blur-sm sm:p-8">
          {children}
        </div>
      </section>
    </main>
  );
}

/** "or" between Google and the password form. */
export function OrDivider() {
  return (
    <div className="flex items-center gap-3 text-2xs uppercase tracking-[0.14em] text-ink-mute">
      <span className="h-px flex-1 bg-edge" /> or <span className="h-px flex-1 bg-edge" />
    </div>
  );
}
