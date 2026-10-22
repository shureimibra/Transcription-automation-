import React from 'react';

// The workers' app brand (Shurz). Its own file, not icons.jsx: the tasker
// bundle shares icons.jsx and must not carry this name.

/**
 * The Shurz mark.
 *
 * A MONOGRAM, NOT A PICTURE. The mark's only job is to be recognisable at
 * 36 pixels in a sidebar and 16 pixels in a browser tab, and at that size an
 * illustration becomes a smudge -- which is exactly what happened to the
 * bird this replaces. A single letter in the product's own typeface reads at
 * any size and never needs redrawing.
 *
 * The gradient runs navy to indigo, the same one the page hero uses, so the
 * mark and the band at the top of every screen are visibly the same brand
 * rather than two things that happen to be blue. The blurred disc behind the
 * letter is the same trick as the hero's, and it is what stops a flat two
 * colour tile from looking like a placeholder.
 *
 * The letter is set in the page font, so the mark and the wordmark beside it
 * are cut from the same cloth without shipping a second asset.
 */
export function BrandMark({ className = 'h-9 w-9', letter = 'text-lg' }) {
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden
        rounded-xl2 bg-gradient-to-br from-brand via-accent-strong to-accent text-white
        shadow-sm ${className}`}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -right-2 -top-3 h-8 w-8 rounded-full bg-white/25 blur-md"
      />
      <span className={`relative font-semibold leading-none tracking-tight ${letter}`}>S</span>
    </span>
  );
}

/**
 * The full lockup: mark plus wordmark.
 *
 * The wordmark is one word and a full stop. The stop is set in the accent so
 * the eye finishes on colour, which is the whole of the flourish -- a name
 * this short does not need more than that, and anything else here would
 * compete with the hero it sits above.
 */
export function BrandLockup({ size = 'md', className = '' }) {
  const mark = size === 'lg' ? 'h-10 w-10' : 'h-9 w-9';
  const type = size === 'lg' ? 'text-lg' : 'text-base';
  return (
    <span className={`flex items-center gap-3 ${className}`}>
      <BrandMark className={mark} letter={size === 'lg' ? 'text-xl' : 'text-lg'} />
      <span className="min-w-0">
        <span className={`block truncate font-semibold tracking-tight text-ink ${type}`}>
          Shurz<span className="text-accent">.</span>
        </span>
        <span className="block truncate text-2xs text-ink-mute">
          Transcription &amp; translation
        </span>
      </span>
    </span>
  );
}
