import React, { useCallback, useRef, useState } from 'react';
import { useAsync } from '../hooks/useAsync.js';
import { useWorkspaceApi } from '../worker/workspaceApi.js';
import Verdict from './Verdict.jsx';
import { Button, ErrorState, Skeleton } from './ui/index.jsx';

/**
 * The Eagle tasker's last step: the finished document, and a Copy button.
 *
 * IN PLACE OF "SUBMIT TO PLATFORM". Nothing is sent from here -- the tasker
 * pastes this into Eagle under their own account. So the findings are
 * advice, never a lock: Copy works whatever the checks say, and the tasker
 * decides.
 *
 * THE NAME IS A PLACEHOLDER. The header's "Transcribed By" / "Translated By"
 * line carries `[Your name]`, highlighted, because the server cannot know
 * the name the tasker uses on Eagle.
 */
export default function FinalDraftDialog({ taskId, onClose }) {
  const { submission } = useWorkspaceApi();
  const load = useCallback(() => submission.preview(taskId), [submission, taskId]);
  const { data, error, loading } = useAsync(load, [taskId]);
  const [copied, setCopied] = useState('');
  const textRef = useRef(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data?.content || '');
      setCopied('done');
      setTimeout(() => setCopied(''), 2500);
    } catch {
      // Clipboard refused: select the text so Ctrl+C works instead.
      const range = document.createRange();
      range.selectNodeContents(textRef.current);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      setCopied('select');
    }
  };

  const placeholder = data?.placeholder || '[Your name]';
  const parts = String(data?.content || '').split(placeholder);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        role="dialog" aria-modal="true" aria-labelledby="final-draft-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl2 border border-edge bg-surface shadow-panel"
      >
        <header className="border-b border-edge px-5 py-3">
          <h2 id="final-draft-title" className="text-sm font-semibold text-ink">
            Your final draft
          </h2>
          <p className="mt-0.5 text-xs text-ink-mute">
            Ready to paste. Replace{' '}
            <mark className="rounded bg-accent/25 px-1 text-ink">{placeholder}</mark>{' '}
            with your name, then paste the whole thing into the platform.
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading && <Skeleton className="h-64" />}
          {error && <ErrorState message={error.message} />}
          {data && (
            <Verdict findings={data.findings || []} blocking={Boolean(data.blocking)}
                     className="mb-4" />
          )}
          {data && (
            <pre ref={textRef}
                 className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-canvas p-3 font-mono text-2xs leading-relaxed text-ink">
              {parts.map((part, i) => (
                <React.Fragment key={i}>
                  {part}
                  {i < parts.length - 1 && (
                    <mark className="rounded bg-accent/25 px-0.5 text-ink">{placeholder}</mark>
                  )}
                </React.Fragment>
              ))}
            </pre>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-edge px-5 py-3">
          <span className="text-2xs text-ink-mute" aria-live="polite">
            {copied === 'done' ? 'Copied. Now paste it into the platform.'
              : copied === 'select' ? 'Selected — press Ctrl+C to copy.'
              : data ? `${data.words} words · ${data.characters} characters` : ''}
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>Back</Button>
            <Button variant="primary" onClick={copy} disabled={!data?.content}>
              {copied === 'done' ? 'Copied' : 'Copy final draft'}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
