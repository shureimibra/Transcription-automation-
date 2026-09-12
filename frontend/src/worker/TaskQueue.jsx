import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { tasks as tasksApi } from '../api/workforce.js';
import { useAsync, usePolling } from '../hooks/useAsync.js';
import { useTaskStream } from '../hooks/useTaskStream.js';
import TaskCard from '../components/TaskCard.jsx';
import { STATUS_ORDER, statusMeta } from '../components/StatusBadge.jsx';
import {
  Button, EmptyState, ErrorState, FOCUS, Kbd, LiveRegion, PageHero, Pagination, Skeleton,
} from '../components/ui/index.jsx';
import { BoltIcon, LanguagesIcon, ListIcon, MicIcon, SearchIcon } from '../components/icons.jsx';
import { taskLabel } from '../lib/format.js';
import TranslationGuideModal from './TranslationGuideModal.jsx';
import TranscriptionGuideModal from './TranscriptionGuideModal.jsx';

// Every task this person is allowed to see, with filters.
//
// FILTERING IS CLIENT-SIDE ON A SERVER-SCOPED LIST. The server has already
// narrowed the list to what this caller may see; searching within it is a
// display concern and does not need a round trip per keystroke. The scope
// filter (mine / available / all) IS sent to the server, because it changes
// which rows exist rather than which are shown.
//
// THIS PAGE IS A SPEED INSTRUMENT. A tasker works three tasks an hour
// against a clock they cannot pause, so the search box takes a keystroke to
// reach and the one fact that decides a claim -- whether the machine work is
// already done -- is visible before anything else on the card.

const SCOPES = [
  { id: 'available', label: 'Available', params: { available: 'true' } },
  { id: 'mine', label: 'Mine', params: { mine: 'true' } },
  { id: 'all', label: 'All visible', params: {} },
];

// TRANSCRIPTION AND TRANSLATION ARE DIFFERENT JOBS. Different instructions,
// different graded work, and wildly different supply -- a handful of
// transcription tasks against seventy translations. Mixed into one list the
// smaller kind is effectively invisible, so each gets its own page and the
// filter is applied by the SERVER: the counts on the page then describe the
// kind you are looking at rather than the whole pool.
const KINDS = {
  transcription: {
    title: 'Transcription',
    blurb: 'Listen, correct the text and label who is speaking.',
    icon: <MicIcon className="h-6 w-6" />,
  },
  translation: {
    title: 'Translation',
    blurb: 'Check the translated text against the audio and the source.',
    icon: <LanguagesIcon className="h-6 w-6" />,
  },
};

const ALL = {
  title: 'All tasks',
  blurb: 'Everything you can see, both kinds together.',
  icon: <ListIcon className="h-6 w-6" />,
};

const PAGE_SIZE = 25;

export default function TaskQueue({ kind = null }) {
  const meta = KINDS[kind] || null;
  const page_meta = meta || ALL;
  const [scope, setScope] = useState('available');
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const searchRef = useRef(null);
  // One guide per kind -- the content genuinely differs (speaker labels and
  // proper nouns here, French typography and numbers on the translation
  // side), each written from its own real-submission review rather than
  // guessed. Which modal actually renders is decided below by `kind`.
  const [showGuide, setShowGuide] = useState(false);

  // Changing what you are looking at must return you to the first page --
  // page 3 of a filter with one page of results renders as "no tasks".
  const reset = (fn) => (value) => { fn(value); setPage(1); };

  const load = useCallback(
    () => tasksApi.list({
      ...SCOPES.find((s) => s.id === scope).params,
      ...(meta ? { kind } : {}),
      page,
      page_size: PAGE_SIZE,
    }),
    [scope, kind, meta, page]
  );
  const { data: envelope, error, loading, refresh } = useAsync(load, [scope, kind, page]);

  // The server pages only when asked, and answers with {count, results}.
  // Tolerate a plain list too, so this keeps working if the request ever
  // goes out without `page`.
  const data = Array.isArray(envelope) ? envelope : envelope?.results;
  const total = Array.isArray(envelope) ? envelope.length : (envelope?.count ?? 0);
  usePolling(refresh, 20000);
  useTaskStream(useCallback(() => refresh({ silent: true }), [refresh]));

  const onChange = useCallback(() => refresh({ silent: true }), [refresh]);

  // A PAGE THAT NO LONGER EXISTS IS NOT AN ERROR TO SHOW SOMEONE.
  //
  // The two queues have wildly lopsided supply -- seventy translations
  // against a handful of transcriptions -- so a page number that was valid a
  // moment ago stops being valid when the list shrinks, or when tasks are
  // claimed out from under it. The server answers 404 "Invalid page.", which
  // is true and useless: the reader did nothing wrong and there is no button
  // that fixes it. Worse, the last good data stays on screen underneath the
  // warning, so the page reads as a list of tasks with an inexplicable red
  // box over it.
  //
  // Snapping back to the first page turns that into a non-event. Narrowed to
  // 404 so that a dropped connection on page three does not silently move
  // the reader somewhere they did not ask to be.
  useEffect(() => {
    if (page > 1 && error?.status === 404) setPage(1);
  }, [error, page]);

  // THE SEARCH BOX IS ONE KEYSTROKE AWAY. Reaching for the mouse to filter a
  // list you are scanning is the kind of small tax that only shows up as
  // "the tool is slow" months later. `/` is the convention; Ctrl/Cmd-K is
  // the one our taskers already have in their fingers from the platform.
  useEffect(() => {
    const onKey = (event) => {
      const target = event.target;
      const typing = target instanceof HTMLElement
        && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      const shortcut = (event.key === '/' && !typing)
        || (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey));
      if (shortcut) {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const list = useMemo(() => {
    const all = data || [];
    const needle = query.trim().toLowerCase();
    return all.filter((task) => {
      if (statusFilter && task.status !== statusFilter) return false;
      if (!needle) return true;
      return (
        taskLabel(task).toLowerCase().includes(needle) ||
        String(task.external_task_id || '').toLowerCase().includes(needle) ||
        String(task.source_language || '').toLowerCase().includes(needle) ||
        String(task.target_language || '').toLowerCase().includes(needle)
      );
    });
  }, [data, query, statusFilter]);

  // Ready work is what a tasker actually cares about: the count of tasks
  // they could start this minute without waiting on the machine.
  //
  // OVER THE FULL FILTER, NOT THE LOADED PAGE. Counting `data` -- only the
  // 25 rows on screen -- capped this at page_size regardless of how many
  // were actually ready (a real bug: 25 shown when 40+ were ready). The
  // server now computes this over the whole filtered-but-unpaginated
  // queryset and returns it as `ready_count`; only fall back to counting
  // the page when that is missing, i.e. the unpaginated plain-list shape.
  const readyCount = envelope?.ready_count
    ?? (data || []).filter((t) => t.is_ready).length;

  const presentStatuses = useMemo(() => {
    const present = new Set((data || []).map((t) => t.status));
    return STATUS_ORDER.filter((s) => present.has(s));
  }, [data]);

  const filtered = Boolean(query || statusFilter);

  return (
    <div className="space-y-5">
      <PageHero
        icon={page_meta.icon}
        eyebrow="Task queue"
        title={page_meta.title}
        subtitle={page_meta.blurb}
        actions={
          <>
            {/* The headline number is READY WORK, not total supply. Seventy
                tasks none of which are warm is not seventy tasks. */}
            <span className="inline-flex items-center gap-2 rounded-xl2 bg-white/15 px-3 py-2
                             text-xs font-semibold text-white backdrop-blur">
              <BoltIcon className="h-4 w-4" />
              <span className="tabular-nums">{readyCount}</span> ready now
            </span>
            {(kind === 'translation' || kind === 'transcription') && (
              <button
                type="button"
                onClick={() => setShowGuide(true)}
                className="rounded-xl2 bg-white/15 px-3 py-2 text-xs font-semibold text-white backdrop-blur
                  transition-colors hover:bg-white/25
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
              >
                {kind === 'translation' ? 'Translation guide' : 'Transcription guide'}
              </button>
            )}
            <button
              type="button"
              onClick={() => refresh()}
              disabled={loading}
              className={`rounded-xl2 bg-white/15 px-3 py-2 text-xs font-semibold text-white
                backdrop-blur transition-colors hover:bg-white/25 disabled:opacity-50
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70`}
            >
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </>
        }
      />

      <div className="rounded-xl2 border border-edge bg-surface p-4 shadow-panel">
        <div className="flex flex-wrap items-center gap-3">
          <div
            role="tablist"
            aria-label="Task scope"
            className="flex rounded-xl2 bg-overlay p-1"
          >
            {SCOPES.map((s) => (
              <button
                key={s.id}
                role="tab"
                aria-selected={scope === s.id}
                onClick={() => reset(setScope)(s.id)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${FOCUS} ${
                  scope === s.id
                    ? 'bg-surface text-ink shadow-sm'
                    : 'text-ink-dim hover:text-ink'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          <div className="relative min-w-[14rem] flex-1">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-mute" />
            <input
              id="queue-search"
              ref={searchRef}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search task id, title or language"
              aria-label="Search tasks"
              className={`w-full rounded-xl2 border border-edge bg-surface py-2 pl-9 pr-14 text-sm text-ink
                placeholder:text-ink-mute transition-colors hover:border-edging ${FOCUS}`}
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2">
              <Kbd>/</Kbd>
            </span>
          </div>

          <select
            id="queue-status"
            value={statusFilter}
            onChange={(e) => reset(setStatusFilter)(e.target.value)}
            aria-label="Filter by status"
            className={`w-44 rounded-xl2 border border-edge bg-surface px-3 py-2 text-sm text-ink
              transition-colors hover:border-edging ${FOCUS}`}
          >
            <option value="">All statuses</option>
            {presentStatuses.map((s) => (
              <option key={s} value={s}>{statusMeta(s).label}</option>
            ))}
          </select>

          <p className="ml-auto shrink-0 text-xs text-ink-mute tabular-nums">
            {loading && !data ? 'Loading…' : `${total} task${total === 1 ? '' : 's'}`}
          </p>
        </div>
      </div>

      {error && <ErrorState message={error.message} onRetry={() => refresh()} />}

      <LiveRegion message={loading ? '' : `${list.length} tasks shown, ${readyCount} ready to start`} />

      {loading && !data ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-56 rounded-xl2" />)}
        </div>
      ) : list.length === 0 ? (
        <div className="rounded-xl2 border border-edge bg-surface shadow-panel">
          <EmptyState
            title={filtered ? 'No tasks match your filters.' : 'Nothing available right now.'}
            message={
              filtered
                ? 'Try a different search, or clear the status filter.'
                : 'The platform is not offering work of this kind at the moment. '
                  + 'This page updates itself the instant a task appears — you do not need to reload.'
            }
            action={
              filtered && (
                <Button
                  variant="soft"
                  size="sm"
                  onClick={() => { setQuery(''); setStatusFilter(''); }}
                >
                  Clear filters
                </Button>
              )
            }
          />
        </div>
      ) : (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            {list.map((task) => (
              <TaskCard key={task.id} task={task} onChange={onChange} showWorker />
            ))}
          </div>

          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPage={(n) => setPage(n)}
            label="tasks"
          />
        </>
      )}

      {showGuide && kind === 'translation' && (
        <TranslationGuideModal onClose={() => setShowGuide(false)} />
      )}
      {showGuide && kind === 'transcription' && (
        <TranscriptionGuideModal onClose={() => setShowGuide(false)} />
      )}
    </div>
  );
}
