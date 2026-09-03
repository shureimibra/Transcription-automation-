import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useTheme, resolvedTheme, stampedTheme } from '../lib/theme.jsx';

// The toggle is the one control whose effect is entirely OUTSIDE React: it
// writes an attribute onto <html> and the stylesheet does the rest. Nothing
// in a component tree fails loudly when that write does not land, so it gets
// its own tests.

function Probe() {
  const { theme, choice, toggle, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <span data-testid="choice">{String(choice)}</span>
      <button onClick={toggle}>Toggle</button>
      <button onClick={() => setTheme(null)}>Follow system</button>
    </div>
  );
}

/** jsdom ships no matchMedia at all, which is also a real browser state. */
function mockSystem(prefersDark) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query.includes('dark') ? prefersDark : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }));
}

describe('the theme toggle', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    mockSystem(false);
  });

  afterEach(() => {
    delete window.matchMedia;
  });

  it('stamps the document on mount, before anyone touches the toggle', () => {
    // The stylesheet reads this attribute and nothing else, so it is never
    // allowed to be absent -- an unstamped page has no defined appearance.
    render(<Probe />);
    expect(stampedTheme()).toBe('light');
  });

  it('stamps the choice onto the document, which is what the stylesheet reads', async () => {
    render(<Probe />);

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('theme')).toBe('dark');
  });

  it('toggles BACK, which is the half that breaks', async () => {
    // The first click is easy. The second has to read the state it just
    // wrote -- and reading it from the wrong place is how a toggle ends up
    // being a one-way switch.
    render(<Probe />);
    const button = screen.getByRole('button', { name: 'Toggle' });

    await userEvent.click(button);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    await userEvent.click(button);
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');

    await userEvent.click(button);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('works on a machine that has no matchMedia', async () => {
    // jsdom is one such environment, and so is an old embedded browser. The
    // toggle must not depend on the system query to function.
    delete window.matchMedia;
    render(<Probe />);

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('reports what the viewer is actually looking at', async () => {
    render(<Probe />);
    expect(screen.getByTestId('theme')).toHaveTextContent('light');

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(screen.getByTestId('theme')).toHaveTextContent('dark');
    expect(screen.getByTestId('choice')).toHaveTextContent('dark');
  });

  it('overrides a dark system when light is chosen', async () => {
    mockSystem(true);
    render(<Probe />);
    expect(screen.getByTestId('theme')).toHaveTextContent('dark');

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    // Choosing must beat the system in BOTH directions, so the stamp has to
    // be present and say "light" -- removing it would hand control back to
    // the dark system and undo the click.
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(screen.getByTestId('theme')).toHaveTextContent('light');
  });

  it('hands control back to the system when the choice is cleared', async () => {
    mockSystem(true);
    render(<Probe />);

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');

    await userEvent.click(screen.getByRole('button', { name: 'Follow system' }));
    // The REMEMBERED choice is cleared, but the stamp still resolves to
    // something concrete -- "follow the system" is a preference, not an
    // absence of styling.
    expect(localStorage.getItem('theme')).toBeNull();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(screen.getByTestId('theme')).toHaveTextContent('dark');
    expect(screen.getByTestId('choice')).toHaveTextContent('null');
  });

  it('never leaves the document unstamped, whatever happens', async () => {
    // Every route through the hook has to end with a concrete stamp: this is
    // the invariant the whole stylesheet rests on.
    mockSystem(true);
    render(<Probe />);
    expect(stampedTheme()).not.toBeNull();

    for (const name of ['Toggle', 'Follow system', 'Toggle', 'Toggle']) {
      await userEvent.click(screen.getByRole('button', { name }));
      expect(stampedTheme()).not.toBeNull();
    }
  });

  it('survives a browser that throws on localStorage', async () => {
    // A private window does not return null, it throws. A theme that cannot
    // be remembered must still switch for this session.
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('access denied');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('access denied');
    });

    render(<Probe />);
    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    await userEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');

    getItem.mockRestore();
    setItem.mockRestore();
  });

  it('reads the live document, not only storage', () => {
    document.documentElement.setAttribute('data-theme', 'dark');
    expect(resolvedTheme()).toBe('dark');
  });
});
