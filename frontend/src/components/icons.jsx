import React from 'react';

// The icon set, drawn inline.
//
// NO ICON LIBRARY. Two dozen glyphs do not justify a dependency, a build
// step and a licence, and inlining them means an icon can never be the one
// asset that fails to load on a slow hotspot -- which is the connection our
// taskers actually work on.
//
// EVERY ICON IS DECORATIVE. Each is `aria-hidden`, drawn in `currentColor`
// and sized by its caller, because an icon here always sits beside a word
// that carries the meaning. Nothing in this application is identified by a
// picture alone.

function Icon({ children, className = 'h-4 w-4', stroke = 1.7, filled = false, viewBox = '0 0 24 24' }) {
  return (
    <svg
      className={className}
      viewBox={viewBox}
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? 'none' : 'currentColor'}
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const MicIcon = (p) => (
  <Icon {...p}>
    <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
    <path d="M19 11a7 7 0 0 1-14 0M12 18v3" />
  </Icon>
);

export const LanguagesIcon = (p) => (
  <Icon {...p}>
    <path d="M3 6h9M7.5 6V4.5M10 6c0 3.5-2.5 6.5-6 8" />
    <path d="M5 10.5c1.6 2.4 3.7 3.8 6 4.5" />
    <path d="m13 20 4-9 4 9M14.5 17h5" />
  </Icon>
);

export const HomeIcon = (p) => (
  <Icon {...p}>
    <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1v-8.5Z" />
  </Icon>
);

export const ListIcon = (p) => (
  <Icon {...p}>
    <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />
  </Icon>
);

export const StudioIcon = (p) => (
  <Icon {...p}>
    <path d="M4 9v6M8 5v14M12 8v8M16 4v16M20 10v4" />
  </Icon>
);

export const GaugeIcon = (p) => (
  <Icon {...p}>
    <path d="M12 20a8 8 0 1 1 8-8" />
    <path d="M12 13.5 16 9" />
    <circle cx="12" cy="14" r="1.2" />
  </Icon>
);

export const UsersIcon = (p) => (
  <Icon {...p}>
    <circle cx="9" cy="8" r="3" />
    <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
    <path d="M16 5.6a3 3 0 0 1 0 4.8M17.5 19a5.6 5.6 0 0 0-2-4.2" />
  </Icon>
);

export const KeyIcon = (p) => (
  <Icon {...p}>
    <circle cx="8" cy="12" r="4" />
    <path d="M12 12h9M18 12v3.5M15.5 12v2.5" />
  </Icon>
);

export const SearchIcon = (p) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </Icon>
);

export const BellIcon = (p) => (
  <Icon {...p}>
    <path d="M18 15V10a6 6 0 1 0-12 0v5l-1.5 2.5h15L18 15Z" />
    <path d="M10 19.5a2.2 2.2 0 0 0 4 0" />
  </Icon>
);

export const SunIcon = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </Icon>
);

export const MoonIcon = (p) => (
  <Icon {...p}>
    <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.4 8.4 0 1 0 20 14.2Z" />
  </Icon>
);

export const EyeIcon = (p) => (
  <Icon {...p}>
    <path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z" />
    <circle cx="12" cy="12" r="2.8" />
  </Icon>
);

export const CheckIcon = (p) => (
  <Icon {...p}>
    <path d="m5 12.5 4.5 4.5L19 7" />
  </Icon>
);

export const PlayIcon = (p) => (
  <Icon {...p} filled>
    <path d="M8 5.2v13.6a.8.8 0 0 0 1.22.68l11-6.8a.8.8 0 0 0 0-1.36l-11-6.8A.8.8 0 0 0 8 5.2Z" />
  </Icon>
);

export const AlertIcon = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5M12 16h.01" />
  </Icon>
);

export const ClockIcon = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7v5.2l3.2 2" />
  </Icon>
);

export const BoltIcon = (p) => (
  <Icon {...p}>
    <path d="M13 3 5.5 13.2h5.2L10 21l7.5-10.2h-5.2L13 3Z" />
  </Icon>
);

export const ArrowRightIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 12h15M14 6.5l5.5 5.5L14 17.5" />
  </Icon>
);

export const ChevronLeftIcon = (p) => (
  <Icon {...p}>
    <path d="m15 18-6-6 6-6" />
  </Icon>
);

export const LogOutIcon = (p) => (
  <Icon {...p}>
    <path d="M15 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3" />
    <path d="M11 8.5 14.5 12 11 15.5M14 12H4" />
  </Icon>
);

export const MenuIcon = (p) => (
  <Icon {...p}>
    <path d="M4 7h16M4 12h16M4 17h10" />
  </Icon>
);

export const FilterIcon = (p) => (
  <Icon {...p}>
    <path d="M4 6h16M7 12h10M10 18h4" />
  </Icon>
);

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
