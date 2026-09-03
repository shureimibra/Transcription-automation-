import { useCallback, useEffect, useState } from 'react';

// Light or dark.
//
// THE STAMP IS ALWAYS PRESENT, AND IT IS THE WHOLE TRUTH.
//
// `data-theme` on <html> is written on boot and on every change, never left
// absent. The stylesheet has exactly two rules keyed off it, so the page is
// dark if and only if the attribute says dark. Nothing about the appearance
// of this application depends on a media query, a `:not()`, or the order two
// CSS rules happen to appear in.
//
// That is a deliberate retreat from a cleverer design. The first version let
// an absent attribute mean "follow the system" and leaned on
// `prefers-color-scheme` to do the work -- which is tidier right up to the
// moment a light choice renders dark and the only way to find out why is to
// reason about specificity in a minified stylesheet.
//
// THE PREFERENCE AND THE STAMP ARE DIFFERENT THINGS. The preference is what
// the person asked for and is one of 'system' | 'light' | 'dark'. The stamp
// is the resolved answer and is only ever 'light' or 'dark'. Keeping "follow
// the system" in storage rather than in the absence of an attribute is what
// lets the stamp be unconditional.
//
// THE FIRST PAINT IS NOT OURS. `index.html` resolves and stamps before React
// exists, because by the time this hook runs the browser has already drawn a
// frame and someone who chose dark would watch a white page flash first.

const KEY = 'theme';
const SYSTEM = 'system';

const isTheme = (value) => value === 'dark' || value === 'light';

const root = () => (typeof document === 'undefined' ? null : document.documentElement);

/** The stamp currently on <html> — what the stylesheet is actually obeying. */
export function stampedTheme() {
  const node = root();
  const value = node && node.getAttribute('data-theme');
  return isTheme(value) ? value : null;
}

/** What the person asked for: 'system', 'light' or 'dark'. Never throws. */
export function themePreference() {
  try {
    const value = localStorage.getItem(KEY);
    return isTheme(value) ? value : SYSTEM;
  } catch {
    // Private windows and locked-down browsers throw on access rather than
    // returning null. Falling through to the stamp is the right answer:
    // storage is how a choice SURVIVES, not how it takes effect.
    return SYSTEM;
  }
}

/** What the system asks for, for someone who has chosen nothing. */
export function systemTheme() {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light';
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/**
 * What the viewer is looking at right now.
 *
 * The stamp wins, because it is what the stylesheet reads. Storage is only
 * consulted when there is no stamp yet -- during the very first render on a
 * page the boot script somehow did not reach.
 */
export function resolvedTheme() {
  const stamped = stampedTheme();
  if (stamped) return stamped;
  const preference = themePreference();
  return preference === SYSTEM ? systemTheme() : preference;
}

/** Write the stamp. The one function in this module that changes anything. */
function stamp(theme) {
  const node = root();
  if (node) node.setAttribute('data-theme', theme);
}

export function useTheme() {
  const [preference, setPreference] = useState(themePreference);
  const [theme, setThemeState] = useState(resolvedTheme);

  // Make sure the stamp and this hook agree from the first render onward,
  // including on a page where the boot script did not run.
  useEffect(() => { stamp(theme); }, [theme]);

  // Follow the system while the preference IS the system, so a machine that
  // dims at sunset dims this too without a reload.
  useEffect(() => {
    if (preference !== SYSTEM || typeof window === 'undefined' || !window.matchMedia) {
      return undefined;
    }
    let query;
    try {
      query = window.matchMedia('(prefers-color-scheme: dark)');
    } catch {
      return undefined;
    }
    const onChange = () => setThemeState(query.matches ? 'dark' : 'light');
    onChange();
    // `addEventListener` on a MediaQueryList is not universal; the deprecated
    // `addListener` is the fallback rather than a reason to skip the feature.
    if (query.addEventListener) query.addEventListener('change', onChange);
    else if (query.addListener) query.addListener(onChange);
    return () => {
      if (query.removeEventListener) query.removeEventListener('change', onChange);
      else if (query.removeListener) query.removeListener(onChange);
    };
  }, [preference]);

  /** `'light'`, `'dark'`, or `null`/`'system'` to hand control back. */
  const setTheme = useCallback((next) => {
    const wanted = isTheme(next) ? next : SYSTEM;
    const resolved = wanted === SYSTEM ? systemTheme() : wanted;

    // The stamp goes on FIRST and unconditionally. It is the only step that
    // changes what the viewer sees, so it must not sit behind a storage
    // write that browsers are allowed to refuse.
    stamp(resolved);

    try {
      if (wanted === SYSTEM) localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, wanted);
    } catch {
      // A theme that cannot be remembered still works for this session.
    }

    setPreference(wanted);
    setThemeState(resolved);
  }, []);

  const toggle = useCallback(() => {
    // Read the document, not our own state: another tab, the boot script or
    // a stale render could all disagree with it, and the document is the one
    // the stylesheet believes.
    setTheme(resolvedTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  return { theme, preference, choice: preference === SYSTEM ? null : preference, setTheme, toggle };
}
