import React, { useState, useRef, useEffect, useMemo } from 'react';

// Searchable replacement for the plain <select> language dropdown -- UI
// enhancement only, no change to what "selecting a language" means
// server-side (value is still just the language code string, '' for Auto
// Detect, exactly as before). Preserves the caller-supplied option order
// (Auto Detect first, then whatever order `languages` arrives in from the
// API) rather than re-sorting -- filtering narrows the list, it never
// reorders it.
export default function SearchableLanguageSelect({ languages, value, onChange, placeholder }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef(null);
  const inputRef = useRef(null);

  const options = useMemo(
    () => [{ code: '', name: 'Auto Detect', engine: null }, ...languages],
    [languages]
  );

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (opt) => opt.name.toLowerCase().includes(q) || opt.code.toLowerCase().includes(q)
    );
  }, [options, query]);

  const selected = options.find((opt) => opt.code === value) || options[0];

  useEffect(() => {
    if (!open) setQuery('');
  }, [open]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [query, open]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const commitSelection = (opt) => {
    onChange(opt.code);
    setOpen(false);
    setQuery('');
  };

  const handleKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) {
      setOpen(true);
      return;
    }
    if (!open) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightedIndex((i) => Math.min(i + 1, filteredOptions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (filteredOptions[highlightedIndex]) commitSelection(filteredOptions[highlightedIndex]);
    } else if (e.key === 'Escape') {
      setOpen(false);
      setQuery('');
      inputRef.current?.blur();
    }
  };

  const labelFor = (opt) =>
    opt.name + (opt.engine && opt.engine !== 'whisper'
      ? ` (via ${opt.engine[0].toUpperCase()}${opt.engine.slice(1)})`
      : '');

  return (
    <div ref={containerRef} className="relative w-full max-w-md">
      <input
        ref={inputRef}
        type="text"
        value={open ? query : labelFor(selected)}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder || 'Search languages...'}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        className="w-full px-4 py-2 bg-[#121214] border border-zinc-800 rounded-lg focus:outline-none focus:border-blue-500 text-sm transition-all"
      />
      {open && (
        <ul className="absolute z-10 mt-1 w-full max-h-64 overflow-y-auto bg-[#18181b] border border-zinc-800 rounded-lg shadow-lg py-1">
          {filteredOptions.length === 0 ? (
            <li className="px-4 py-2 text-xs text-zinc-500 italic">No matching languages</li>
          ) : (
            filteredOptions.map((opt, idx) => (
              <li
                key={opt.code || '__auto__'}
                onMouseDown={(e) => { e.preventDefault(); commitSelection(opt); }}
                onMouseEnter={() => setHighlightedIndex(idx)}
                className={`px-4 py-2.5 sm:py-1.5 text-sm cursor-pointer ${
                  idx === highlightedIndex ? 'bg-blue-600 text-white' : 'text-zinc-300'
                } ${opt.code === value ? 'font-semibold' : ''}`}
              >
                {labelFor(opt)}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
