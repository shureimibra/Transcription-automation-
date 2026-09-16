import React from 'react';

// Interface primitives. Small on purpose: enough to keep spacing, focus and
// loading behaviour consistent across every screen, and no more. A component
// library would be a second thing to maintain for a six-person tool.
//
// EVERY COLOUR HERE IS A TOKEN. Nothing in this file names a hex value, so
// the whole set re-themes from `index.css` and a primitive cannot be the one
// thing on a screen that stayed dark.

const FOCUS =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ' +
  'focus-visible:ring-offset-2 focus-visible:ring-offset-canvas';

export function Spinner({ className = 'h-4 w-4' }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-90" fill="currentColor" d="M12 2a10 10 0 0 1 10 10h-3a7 7 0 0 0-7-7V2Z" />
    </svg>
  );
}

// ONE FILLED BUTTON PER SURFACE. `primary` is the deep brand navy and is
// spent on the single action a screen exists for; `accent` is the softer
// indigo for a claim, which is an action you take from a list of many.
// Everything else recedes. A card with two filled buttons has told the
// reader nothing about which one to press.
const VARIANTS = {
  primary:   'bg-brand hover:bg-brand-strong text-white shadow-sm',
  accent:    'bg-accent hover:bg-accent-strong text-white shadow-sm',
  secondary: 'bg-surface hover:bg-overlay text-ink border border-edge',
  soft:      'bg-accent-soft hover:bg-overlay text-accent-ink',
  ghost:     'hover:bg-overlay text-ink-dim hover:text-ink',
  danger:    'bg-status-rejected hover:bg-status-failed text-white',
};

/**
 * A button that cannot be left mid-action.
 *
 * `loading` both disables the control and swaps in a spinner, so the frozen
 * button the brief warns about is impossible by construction rather than by
 * every caller remembering to handle it.
 */
export function Button({
  variant = 'secondary', size = 'md', loading = false, disabled = false,
  icon = null, children, className = '', ...rest
}) {
  const scale =
    size === 'sm' ? 'text-xs px-2.5 py-1.5 gap-1.5'
    : size === 'lg' ? 'text-sm px-5 py-2.5 gap-2'
    : 'text-xs px-3 py-2 gap-2';
  return (
    <button
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors
        disabled:opacity-50 disabled:cursor-not-allowed ${VARIANTS[variant]} ${scale} ${FOCUS} ${className}`}
      {...rest}
    >
      {loading ? <Spinner className="h-3.5 w-3.5" /> : icon}
      {children}
    </button>
  );
}

export function Card({ title, subtitle, actions, children, className = '', padded = true }) {
  return (
    <section className={`rounded-xl2 border border-edge bg-surface shadow-panel ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-edge px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-mute">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? 'p-4' : ''}>{children}</div>
    </section>
  );
}

/**
 * A single headline number.
 *
 * Renders an em dash when the value is null rather than 0, because "no data
 * yet" and "measured zero" are different facts. Showing 0% approval for a
 * worker who has submitted nothing is exactly the misleading statistic the
 * brief warns against.
 */
export function Stat({ label, value, hint, tone = 'default' }) {
  const tones = {
    default: 'text-ink',
    good: 'text-status-approved',
    warn: 'text-status-review',
    bad: 'text-status-rejected',
  };
  const empty = value === null || value === undefined || value === '';
  return (
    <div className="rounded-xl2 border border-edge bg-surface px-4 py-3 shadow-panel">
      <div className="text-2xs font-medium uppercase tracking-wider text-ink-mute">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${empty ? 'text-ink-mute' : tones[tone]}`}>
        {empty ? '—' : value}
      </div>
      {hint && <div className="mt-0.5 text-2xs text-ink-mute">{hint}</div>}
    </div>
  );
}

export function EmptyState({ title, message, action, icon = null }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-overlay text-ink-mute">
        {icon || (
          <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path strokeLinecap="round" d="m20 20-3.5-3.5M8 11h6" />
          </svg>
        )}
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {message && <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-ink-mute">{message}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/**
 * A failure the user can act on.
 *
 * `role="alert"` so a screen reader announces it without the user going
 * looking, and a retry is offered wherever the caller can supply one.
 */
export function ErrorState({ message, onRetry, compact = false }) {
  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-lg border border-status-rejected/30 bg-status-rejected/10
        ${compact ? 'px-3 py-2' : 'px-4 py-3'}`}
    >
      <span aria-hidden="true" className="mt-0.5 text-status-rejected">⚠</span>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-ink">{message}</p>
        {onRetry && (
          <button onClick={onRetry} className={`mt-1.5 rounded text-2xs font-medium text-accent hover:text-accent-strong ${FOCUS}`}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function Skeleton({ className = 'h-4 w-full' }) {
  return <div className={`animate-pulse rounded bg-overlay ${className}`} aria-hidden="true" />;
}

/** Screen-reader announcement for status changes that have no visual anchor. */
export function LiveRegion({ message }) {
  return <div aria-live="polite" aria-atomic="true" className="sr-only">{message}</div>;
}

/**
 * A status line a SIGHTED worker can actually see.
 *
 * THE BUG THIS EXISTS FOR: a failed save was reaching `LiveRegion`, which
 * is `sr-only` -- present in the DOM (so a test asserting on the text still
 * passed) but invisible on screen. A worker whose save silently 404'd (the
 * task reclaimed out from under them) saw no error at all: no red text, no
 * banner, just a Save button that appeared to do nothing and a Preview
 * button that stayed disabled with no visible reason why. `aria-live` and
 * `role="status"` still make this announce to a screen reader, same as
 * LiveRegion did -- the fix is making it visible too, not choosing one
 * over the other.
 */
const NOTICE_TONES = {
  info: 'text-ink-dim',
  error: 'font-medium text-status-rejected',
  // A lead to go verify, not a failure -- the same amber used elsewhere
  // for "needs a look" states (StatusBadge's review status), not the red
  // reserved for something actually wrong.
  warn: 'font-medium text-status-review',
};

export function Notice({ message, tone = 'info' }) {
  if (!message) return null;
  return (
    <p
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={`text-xs ${NOTICE_TONES[tone] || NOTICE_TONES.info}`}
    >
      {message}
    </p>
  );
}

/**
 * The page-anchored banner half of useClaimNotice -- see that hook's own
 * docstring for why this exists as shared code rather than markup copied
 * into every screen that renders claimable task cards.
 */
export function ClaimNotice({ message }) {
  if (!message) return null;
  return (
    <div className="flex items-center gap-2 rounded-xl2 border border-status-ready/30
                     bg-status-ready/10 px-4 py-2.5">
      <Notice message={message} />
    </div>
  );
}

export function Field({ label, error, hint, children, htmlFor }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-ink-dim">{label}</label>
      {children}
      {hint && !error && <p className="text-2xs text-ink-mute">{hint}</p>}
      {error && <p id={`${htmlFor}-error`} className="text-2xs text-status-rejected">{error}</p>}
    </div>
  );
}

export const inputClass =
  'w-full rounded-lg border border-edge bg-surface px-3 py-2 text-sm text-ink ' +
  'placeholder:text-ink-mute disabled:opacity-50 transition-colors ' +
  'hover:border-edging ' + FOCUS;

/* ===================================================================== *
 * The pieces the queue is built from.
 * ===================================================================== */

// A chip's tone is a CLAIM ABOUT THE FACT IT CARRIES, not decoration.
// `money` is filled and shares its colour with nothing else on the card, so
// the number a tasker is deciding on is the first thing the eye finds.
const CHIP_TONES = {
  neutral: 'bg-overlay text-ink-dim',
  outline: 'border border-edge bg-surface text-ink-dim',
  accent:  'bg-accent-soft text-accent-ink',
  money:   'bg-money text-white',
  ready:   'bg-money-soft text-money-ink ring-1 ring-money/25',
  warn:    'bg-status-review/10 text-status-review ring-1 ring-status-review/25',
  danger:  'bg-status-rejected/10 text-status-rejected ring-1 ring-status-rejected/25',
};

export function Chip({ tone = 'neutral', icon = null, title, className = '', children }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1
        text-2xs font-medium ${CHIP_TONES[tone] || CHIP_TONES.neutral} ${className}`}
    >
      {icon}
      {children}
    </span>
  );
}

/** The rounded-square icon holder used for section and card marks. */
export function IconTile({ tone = 'accent', size = 'md', className = '', children }) {
  const tones = {
    accent: 'bg-accent-soft text-accent-ink',
    brand:  'bg-brand text-white',
    money:  'bg-money-soft text-money-ink',
    plain:  'bg-surface text-brand',
    ghost:  'bg-white/15 text-white',
  };
  const scale = size === 'sm' ? 'h-7 w-7 rounded-lg'
    : size === 'lg' ? 'h-12 w-12 rounded-xl2'
    : 'h-9 w-9 rounded-xl2';
  return (
    <span className={`inline-flex shrink-0 items-center justify-center ${scale} ${tones[tone]} ${className}`}>
      {children}
    </span>
  );
}

/** A small uppercase label that names a group without competing with it. */
export function SectionLabel({ children, className = '' }) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <span className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-mute">{children}</span>
      <span className="h-px flex-1 bg-edge" aria-hidden="true" />
    </div>
  );
}

/** A keyboard hint, rendered as a key. */
export function Kbd({ children }) {
  return (
    <kbd className="rounded border border-edge bg-surface px-1.5 py-0.5 font-sans text-2xs
                    font-medium text-ink-mute shadow-sm">
      {children}
    </kbd>
  );
}

/**
 * The band at the top of a page.
 *
 * It carries the brand gradient and it is the ONLY place on the page that
 * does. A screen where several things compete for the eye has no hierarchy;
 * this one element is allowed to be loud so nothing else has to be.
 */
export function PageHero({ icon, eyebrow, title, subtitle, actions, className = '' }) {
  return (
    <section
      className={`relative overflow-hidden rounded-xl2 bg-hero-brand px-5 py-6 text-white shadow-lift sm:px-7 ${className}`}
    >
      {/* A soft light source, drawn rather than imported, so the band has
          depth without a flat-gradient look and without a network request. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-white/10 blur-2xl"
      />
      <div className="relative flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-4">
          {icon && <IconTile tone="ghost" size="lg">{icon}</IconTile>}
          <div className="min-w-0">
            {eyebrow && (
              <p className="text-2xs font-semibold uppercase tracking-[0.16em] text-white/65">{eyebrow}</p>
            )}
            <h1 className="mt-0.5 truncate text-xl font-semibold sm:text-2xl">{title}</h1>
            {subtitle && <p className="mt-1 text-xs text-white/75">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </section>
  );
}

/**
 * Paging, said in entries rather than in pages.
 *
 * "Showing 26 to 50 of 73" answers the question a page number does not:
 * how much is left. The numbered buttons are a WINDOW around the current
 * page -- seventy pages of buttons is not navigation, it is wallpaper.
 */
export function Pagination({ page, pageSize, total, onPage, label = 'entries' }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;

  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  const window = [];
  const from = Math.max(1, Math.min(page - 2, pages - 4));
  const to = Math.min(pages, Math.max(page + 2, 5));
  for (let n = from; n <= to; n += 1) window.push(n);

  const step = (className, disabled, onClick, aria, path) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={aria}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border border-edge
        bg-surface text-ink-dim transition-colors hover:bg-overlay hover:text-ink
        disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS} ${className}`}
    >
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d={path} />
      </svg>
    </button>
  );

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 pt-1"
    >
      <p className="text-2xs text-ink-mute tabular-nums" aria-live="polite">
        Showing {first} to {last} of {total} {label}
      </p>
      <div className="flex items-center gap-1.5">
        {step('', page <= 1, () => onPage(page - 1), 'Previous page', 'm15 18-6-6 6-6')}
        {from > 1 && <span className="px-1 text-2xs text-ink-mute" aria-hidden="true">…</span>}
        {window.map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => onPage(n)}
            aria-current={n === page ? 'page' : undefined}
            className={`inline-flex h-8 min-w-8 items-center justify-center rounded-lg px-2 text-xs
              font-medium tabular-nums transition-colors ${FOCUS} ${
                n === page
                  ? 'bg-brand text-white'
                  : 'border border-edge bg-surface text-ink-dim hover:bg-overlay hover:text-ink'
              }`}
          >
            {n}
          </button>
        ))}
        {to < pages && <span className="px-1 text-2xs text-ink-mute" aria-hidden="true">…</span>}
        {step('', page >= pages, () => onPage(page + 1), 'Next page', 'm9 18 6-6-6-6')}
      </div>
    </nav>
  );
}

export { FOCUS };
