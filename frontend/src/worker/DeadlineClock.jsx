import React, { useEffect, useRef, useState } from 'react';

/**
 * Time left on the platform's clock.
 *
 * WHY IT IS PROMINENT. Eagle gives one hour from claim to submission, and a
 * task that expires costs the purchased minutes AND the work already done.
 * This is the highest-stakes number on the screen, so it is legible at a
 * glance and it changes character as the deadline nears rather than staying
 * politely grey to the end.
 *
 * THE DEADLINE IS THE SERVER'S. It counts down from `deadline_at` as the
 * platform set it — never from a duration this component invents, because a
 * clock that disagrees with Eagle's is worse than no clock.
 */

function findDeadline(task) {
  // Populated by the sync from Eagle's own field; absent until claimed.
  const raw = task?.deadline_at || task?.raw?.deadline_at || null;
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export default function DeadlineClock({ task, onExpiring }) {
  const deadline = findDeadline(task);
  const [now, setNow] = useState(() => Date.now());
  const warned = useRef(false);

  useEffect(() => {
    if (!deadline) return undefined;
    // One second, the same cadence the platform's own page uses.
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [deadline]);

  if (!deadline) return null;

  const remaining = Math.max(0, deadline.getTime() - now);
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  const expired = remaining <= 0;

  if (!warned.current && minutes < 10 && !expired) {
    warned.current = true;
    onExpiring?.();
  }

  // Three bands, because "plenty of time", "wrap up" and "now" are different
  // instructions, and colour alone should not be the one carrying them.
  const urgent = minutes < 5;
  const soon = minutes < 10;

  const tone = expired || urgent
    ? 'border-status-rejected/50 bg-status-rejected/10 text-status-rejected'
    : soon
      ? 'border-status-review/50 bg-status-review/10 text-status-review'
      : 'border-edge bg-surface text-ink-dim';

  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 rounded-xl2 border px-4 py-2.5 ${tone}`}
      role={soon ? 'alert' : undefined}
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-lg font-semibold tabular-nums">
          {expired ? '0:00' : `${minutes}:${String(seconds).padStart(2, '0')}`}
        </span>
        <span className="text-2xs uppercase tracking-wide">
          {expired ? 'deadline passed' : 'left to submit'}
        </span>
      </div>
      <p className="text-2xs">
        {expired
          ? 'This task may no longer be accepted. Tell an administrator.'
          : urgent
            ? 'Submit now — an imperfect submission beats losing the task.'
            : soon
              ? 'Start wrapping up.'
              : 'Review against the audio, then preview before submitting.'}
      </p>
    </div>
  );
}
