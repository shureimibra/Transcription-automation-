import React, { useEffect, useId, useRef, useState } from 'react';
import { Button, Field, inputClass } from './ui/index.jsx';

/**
 * A modal that either confirms an action or collects one line of text.
 *
 * WHY NOT window.confirm/prompt. Those block the whole tab, cannot show a
 * loading state while the request is in flight, and cannot be styled or
 * translated. Rejecting a task needs a reason the server requires -- a
 * native prompt would let an empty string through and turn a considered
 * refusal into a 400.
 *
 * Focus moves in on open and returns to whatever opened it on close, Escape
 * cancels, and Tab is trapped inside -- so a keyboard user is never left
 * tabbing through a page they cannot see.
 */
export default function PromptDialog({
  title, body, fieldLabel = null, required = false,
  confirmLabel = 'Confirm', busy = false, onConfirm, onCancel,
}) {
  const [value, setValue] = useState('');
  const [touched, setTouched] = useState(false);
  const panelRef = useRef(null);
  const firstFieldRef = useRef(null);
  const restoreFocusRef = useRef(null);
  const titleId = useId();
  const bodyId = useId();

  const missing = required && !value.trim();

  useEffect(() => {
    restoreFocusRef.current = document.activeElement;
    // With a field, focus it; without one, focus the first control so the
    // dialog never opens with focus still behind it on the page.
    const target = firstFieldRef.current || panelRef.current?.querySelector('button');
    target?.focus();
    return () => {
      // Returning focus matters most on the path nobody tests: cancelling.
      const target = restoreFocusRef.current;
      if (target && typeof target.focus === 'function') target.focus();
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) {
        event.stopPropagation();
        onCancel?.();
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = panelRef.current?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href]'
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [busy, onCancel]);

  const submit = (event) => {
    event.preventDefault();
    setTouched(true);
    if (missing) return;
    onConfirm?.(value.trim());
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel?.(); }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={body ? bodyId : undefined}
        className="w-full max-w-md rounded-xl2 border border-edge bg-surface p-5 shadow-panel"
      >
        <h2 id={titleId} className="text-sm font-semibold text-ink">{title}</h2>
        {body && <p id={bodyId} className="mt-1.5 text-xs text-ink-mute">{body}</p>}

        <form onSubmit={submit} className="mt-4 space-y-4" noValidate>
          {fieldLabel && (
            <Field
              label={fieldLabel}
              htmlFor="prompt-value"
              error={touched && missing ? 'This is required.' : null}
            >
              <textarea
                id="prompt-value"
                ref={firstFieldRef}
                rows={3}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                disabled={busy}
                aria-invalid={touched && missing}
                aria-describedby={touched && missing ? 'prompt-value-error' : undefined}
                className={`${inputClass} resize-y`}
              />
            </Field>
          )}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={busy}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
