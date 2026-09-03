// Display formatting. Small, boring, and in one place so a duration reads
// the same on the worker's queue as it does on the admin's report.

/** 1245 -> "20:45". Media length, so the leading hour is dropped when zero. */
export function duration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** 5400 -> "1h 30m". For elapsed working time, where seconds are noise. */
export function elapsed(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** 0.8421 -> "84%". Null stays null, so callers can render an em dash. */
export function percent(rate, digits = 0) {
  if (rate === null || rate === undefined) return null;
  return `${(rate * 100).toFixed(digits)}%`;
}

export function dateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export function timeOnly(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/**
 * "3 minutes ago". Falls back to an absolute date past a week, because
 * "47 days ago" is harder to reason about than the date itself.
 */
export function relative(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const secondsAgo = Math.round((Date.now() - then) / 1000);
  if (secondsAgo < 45) return 'just now';
  if (secondsAgo < 3600) return `${Math.round(secondsAgo / 60)}m ago`;
  if (secondsAgo < 86400) return `${Math.round(secondsAgo / 3600)}h ago`;
  if (secondsAgo < 604800) return `${Math.round(secondsAgo / 86400)}d ago`;
  return dateTime(iso);
}

/** YYYY-MM-DD in the viewer's own timezone, for date inputs. */
export function isoDay(date) {
  const d = date instanceof Date ? date : new Date(date);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDay(d);
}

const LANGUAGE_NAMES =
  typeof Intl !== 'undefined' && Intl.DisplayNames
    ? new Intl.DisplayNames(undefined, { type: 'language' })
    : null;

/** "fr" -> "French", falling back to the raw code rather than to nothing. */
export function languageName(code) {
  if (!code) return '';
  try {
    return LANGUAGE_NAMES?.of(code) || code;
  } catch {
    return code;
  }
}

/** The one-line description of what a task is: kind plus language route. */
export function taskRoute(task) {
  if (!task) return '';
  const source = languageName(task.source_language);
  if (task.kind === 'translation' && task.target_language) {
    return `${source || 'Auto'} → ${languageName(task.target_language)}`;
  }
  return source || 'Auto-detect';
}

export function taskLabel(task) {
  if (!task) return '';
  // THE EAGLE ID LEADS. It is the only identifier that means anything on
  // both sides: it is what the platform shows, what a coach refers to, and
  // what you search for when something is disputed. Eagle's own `title` is
  // just the media filename ("C 64.mp3"), which identifies nothing and made
  // our screens impossible to line up against the platform's.
  if (task.external_task_id) {
    return task.title
      ? `${task.external_task_id} · ${task.title}`
      : `Task ${task.external_task_id}`;
  }
  if (task.title) return task.title;
  return `Task ${String(task.id).slice(0, 8)}`;
}
