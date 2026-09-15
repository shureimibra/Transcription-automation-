import React, { useEffect, useRef } from 'react';
import { Button } from './ui/index.jsx';

/**
 * The expanded search row -- see useSegmentSearch (frontend/src/hooks) for
 * why this only ever renders once a worker has asked for it, and why
 * matching is substring, case-insensitive.
 *
 * COLLAPSED IS THE CALLER'S JOB, NOT THIS COMPONENT'S. This renders
 * nothing at all while `search.open` is false -- the toggle button that
 * flips it on lives in each screen's own Card actions, next to Full guide
 * and Preview submission, so it costs a screen nothing until asked for
 * and never competes with those for space.
 *
 * `onJump(row)` fires whenever the current match changes -- typing to a
 * new first hit, Enter/Next, Shift+Enter/Previous. This component owns
 * only the query and the match list; the caller decides what "jump"
 * means for its own screen (seek the audio, scroll the row into view).
 */
export default function SegmentSearchBar({ search, onJump, placeholder = 'Search…' }) {
  const inputRef = useRef(null);
  const {
    open, query, setQuery, matches, index, current, next, prev, closeSearch,
  } = search;

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const currentKey = current ? (current.row.id ?? current.row.key) : null;
  useEffect(() => {
    if (current) onJump(current.row);
    // `onJump` is a fresh closure every render by design (it closes over
    // whatever the caller's own state currently is) -- re-running this
    // effect every time React decides to re-create it would jump the
    // audio on renders that have nothing to do with the search moving.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey]);

  if (!open) return null;

  return (
    <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-edge
                     bg-surface px-4 py-2">
      <span aria-hidden="true" className="text-ink-mute">⌕</span>
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') closeSearch();
          if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? prev() : next(); }
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-sm text-ink
                   placeholder:text-ink-mute focus:outline-none"
      />
      <span className="shrink-0 text-2xs tabular-nums text-ink-mute">
        {query.trim() === '' ? ''
          : matches.length === 0 ? 'No matches'
          : `${index + 1} of ${matches.length}`}
      </span>
      <Button size="sm" variant="ghost" disabled={!matches.length}
              onClick={prev} aria-label="Previous match" title="Previous match (Shift+Enter)">
        ↑
      </Button>
      <Button size="sm" variant="ghost" disabled={!matches.length}
              onClick={next} aria-label="Next match" title="Next match (Enter)">
        ↓
      </Button>
      <Button size="sm" variant="ghost" onClick={closeSearch} aria-label="Close search" title="Close search (Esc)">
        ×
      </Button>
    </div>
  );
}
