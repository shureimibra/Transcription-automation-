import React, { useMemo, useState } from 'react';

// Side-by-side review of one AI revision: the original text on the left, the
// AI's version on the right, and an Accept/Reject decision per segment.
//
// Rejecting is not a deletion. It flips `accepted` on the revision row, and
// the backend's AIRevisionSegment.effective_text then falls back to
// original_text -- so a reject is instant, reversible, and leaves the
// suggestion visible for as long as the revision exists. Nothing in this
// component can touch the original transcript; there is no endpoint here
// that writes to a TranscriptSegment.
//
// Only changed segments get a decision. An unchanged segment's `accepted`
// value is meaningless (effective_text returns the original either way), so
// showing it a button would imply a choice that doesn't exist -- they're
// collapsed behind a toggle instead, available for context but out of the way.

function formatTimestamp(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

const STATUS_LABELS = {
  queued: 'Queued',
  processing: 'Processing…',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export default function DiffReview({
  revision,
  diarizationEnabled,
  speakerAliases,
  onSegmentDecision,
  onBulkDecision,
  onCancel,
  onExport,
  onClose,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [pendingIds, setPendingIds] = useState(() => new Set());

  const segments = revision?.segments || [];
  const changed = useMemo(() => segments.filter(s => s.changed), [segments]);
  const unchanged = useMemo(() => segments.filter(s => !s.changed), [segments]);
  const acceptedCount = changed.filter(s => s.accepted).length;

  const isRunning = ['queued', 'processing'].includes(revision?.status);

  const decide = async (segment, accepted) => {
    setPendingIds(prev => new Set(prev).add(segment.id));
    try {
      await onSegmentDecision(segment.id, accepted);
    } finally {
      setPendingIds(prev => {
        const next = new Set(prev);
        next.delete(segment.id);
        return next;
      });
    }
  };

  const speakerFor = (segment) => {
    if (!diarizationEnabled) return null;
    const raw = segment.speaker_label;
    if (!raw) return null;
    return (speakerAliases && speakerAliases[raw]) || raw;
  };

  return (
    <div className="border border-zinc-800 rounded-xl bg-[#18181b] overflow-hidden shadow-lg">
      {/* ---------------- Header ---------------- */}
      <div className="px-4 py-3 flex items-start justify-between gap-3 flex-wrap">
        <div
          onClick={() => setIsOpen(v => !v)}
          className="min-w-0 cursor-pointer select-none group"
        >
          <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2 flex-wrap group-hover:text-white transition-colors">
            <span>✨ {revision?.label || 'AI Revision'}</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-normal ${
              revision?.status === 'completed' ? 'bg-emerald-950 text-emerald-400'
                : revision?.status === 'failed' ? 'bg-red-950 text-red-400'
                : revision?.status === 'cancelled' ? 'bg-zinc-800 text-zinc-400'
                : 'bg-blue-950 text-blue-400'
            }`}>
              {STATUS_LABELS[revision?.status] || revision?.status}
            </span>
          </h3>
          <p className="text-[11px] text-zinc-500 mt-1">
            {changed.length} of {segments.length} segments changed
            {changed.length > 0 && <> · {acceptedCount} accepted</>}
            {revision?.cache_hits !== undefined && revision?.cache_hits > 0 && (
              <span className="text-emerald-400 font-medium"> · {revision.cache_hits} cached</span>
            )}
            {revision?.cache_misses !== undefined && revision?.cache_misses > 0 && (
              <span> · {revision.cache_misses} from Gemini</span>
            )}
            {revision?.total_tokens ? <> · {revision.total_tokens.toLocaleString()} tokens</> : null}
            {revision?.estimated_tokens_saved !== undefined && revision?.estimated_tokens_saved > 0 && (
              <span className="text-emerald-400"> (~{revision.estimated_tokens_saved.toLocaleString()} saved)</span>
            )}
            <span className="text-zinc-400 ml-1.5 font-medium">({isOpen ? 'click to collapse' : 'click to open'})</span>
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {isRunning && (
            <button
              onClick={onCancel}
              className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] rounded-lg transition-colors cursor-pointer"
            >
              Cancel
            </button>
          )}
          {changed.length > 0 && (
            <>
              <button
                onClick={() => onBulkDecision('accept_all')}
                className="px-2.5 py-1 bg-emerald-700 hover:bg-emerald-600 text-white text-[11px] rounded-lg transition-colors cursor-pointer"
              >
                Accept all
              </button>
              <button
                onClick={() => onBulkDecision('reject_all')}
                className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] rounded-lg transition-colors cursor-pointer"
              >
                Reject all
              </button>
            </>
          )}
          {revision?.status === 'completed' && onExport && (
            <button
              onClick={onExport}
              className="px-2.5 py-1 bg-blue-600 hover:bg-blue-500 text-white text-[11px] rounded-lg transition-colors cursor-pointer"
            >
              Export this version
            </button>
          )}
          <button
            onClick={() => setIsOpen(v => !v)}
            className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[11px] font-medium rounded-lg transition-colors cursor-pointer flex items-center gap-1 border border-zinc-700/60"
            title={isOpen ? "Close segments" : "Open segments"}
          >
            <span>{isOpen ? 'Close' : 'Open'}</span>
            <span className="text-[10px] text-zinc-400">{isOpen ? '▲' : '▼'}</span>
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="px-1.5 py-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer text-xs"
              title="Close review panel"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Degraded: some batches fell back to the original text rather than
          risk misaligning text with timestamps. Surfaced explicitly because
          "some segments look unimproved" and "the model was fine, nothing
          needed changing" are otherwise indistinguishable to the user. */}
      {revision?.degraded && (
        <div className="px-4 py-2 bg-amber-950/30 border-t border-amber-900/40 text-[11px] text-amber-400">
          Some segments could not be processed reliably and kept their original text.
          Re-running may improve them.
        </div>
      )}

      {revision?.status === 'failed' && (
        <div className="px-4 py-2 bg-red-950/30 border-t border-red-900/40 text-[11px] text-red-400">
          {revision.error_message || 'AI processing failed.'}
        </div>
      )}

      {/* The quality verdict, on its own axis from `status` above.
          A revision can be 'completed' AND untrustworthy, which is exactly
          the state worth being loud about -- treating "the run finished" as
          "the text is good" is what let a rejected translation ship. */}
      {revision?.quality_status && revision.quality_status !== 'not_run' && (
        <div
          className={`px-4 py-2 border-t text-[11px] ${
            revision.quality_status === 'validated'
              ? 'bg-emerald-950/30 border-emerald-900/40 text-emerald-400'
              : revision.quality_status === 'technical_failure'
                ? 'bg-zinc-900 border-zinc-800 text-zinc-400'
                : 'bg-amber-950/30 border-amber-900/40 text-amber-400'
          }`}
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold">
              {revision.quality_status === 'validated' && '✓ Quality checks passed'}
              {revision.quality_status === 'quality_review_required' && '⚠ Review required'}
              {revision.quality_status === 'technical_failure' && '· Quality checks did not run'}
            </span>
            {revision.detected_domain && (
              <span className="text-zinc-500">
                {revision.detected_domain}
                {revision.detected_register ? ` · ${revision.detected_register}` : ''}
              </span>
            )}
            {revision.qa_iterations > 0 && (
              <span className="text-zinc-500">
                · {revision.qa_iterations} repair pass{revision.qa_iterations === 1 ? '' : 'es'}
              </span>
            )}
            {[
              ['critical', revision.critical_issue_count],
              ['high', revision.high_issue_count],
              ['medium', revision.medium_issue_count],
              ['low', revision.low_issue_count],
            ].filter(([, count]) => count > 0).map(([level, count]) => (
              <span key={level} className="text-zinc-500">· {count} {level}</span>
            ))}
          </div>
          {revision.quality_reason && (
            <p className="mt-1 text-zinc-400">{revision.quality_reason}</p>
          )}
          {revision.quality_status === 'quality_review_required' && (
            // Stated because the alternative reading -- "the AI failed" --
            // is wrong and would send someone to re-run rather than to read.
            <p className="mt-1 text-zinc-500">
              The translation is below. It has not been approved for
              submission; check the findings before sending it anywhere.
            </p>
          )}
        </div>
      )}

      {/* Live progress while the gate runs, so a long audit does not look
          like a stall. Suppressed once a verdict exists. */}
      {revision?.quality_message && revision?.status === 'processing' && (
        <div className="px-4 py-2 bg-blue-950/20 border-t border-blue-900/30 text-[11px] text-blue-300">
          {revision.quality_message}
        </div>
      )}

      {/* ---------------- Collapsible segments container ---------------- */}
      {isOpen && (
        <div className="border-t border-zinc-800 max-h-[560px] overflow-y-auto divide-y divide-zinc-800/70">
          <div>
            {changed.length === 0 && !isRunning && (
              <p className="px-4 py-6 text-xs text-zinc-500 text-center">
                The AI did not change anything in this range.
              </p>
            )}
            {changed.map(segment => {
              const pending = pendingIds.has(segment.id);
              const speaker = speakerFor(segment);
              return (
                <div key={segment.id} className="px-4 py-3 border-b border-zinc-800/50 last:border-b-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] font-mono text-zinc-600">
                      {formatTimestamp(segment.start_time)}
                    </span>
                    {speaker && (
                      <span className="text-[10px] text-blue-400 font-medium">{speaker}</span>
                    )}
                    <div className="flex-1" />
                    <button
                      onClick={() => decide(segment, true)}
                      disabled={pending || segment.accepted}
                      className={`px-2 py-0.5 text-[10px] rounded transition-colors cursor-pointer ${
                        segment.accepted
                          ? 'bg-emerald-700 text-white'
                          : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                      } ${pending ? 'opacity-50' : ''}`}
                    >
                      {segment.accepted ? '✓ Accepted' : 'Accept'}
                    </button>
                    <button
                      onClick={() => decide(segment, false)}
                      disabled={pending || !segment.accepted}
                      className={`px-2 py-0.5 text-[10px] rounded transition-colors cursor-pointer ${
                        !segment.accepted
                          ? 'bg-zinc-700 text-zinc-200'
                          : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                      } ${pending ? 'opacity-50' : ''}`}
                    >
                      {segment.accepted ? 'Reject' : '✕ Rejected'}
                    </button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div className={`px-2.5 py-2 rounded-lg text-xs leading-relaxed border ${
                      segment.accepted
                        ? 'bg-[#121214] border-zinc-800 text-zinc-500'
                        : 'bg-[#121214] border-emerald-900/50 text-zinc-300'
                    }`}>
                      <div className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1">
                        Original{!segment.accepted && ' · in use'}
                      </div>
                      {segment.original_text}
                    </div>
                    <div className={`px-2.5 py-2 rounded-lg text-xs leading-relaxed border ${
                      segment.accepted
                        ? 'bg-[#121214] border-emerald-900/50 text-zinc-200'
                        : 'bg-[#121214] border-zinc-800 text-zinc-500'
                    }`}>
                      <div className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1">
                        AI version{segment.accepted && ' · in use'}
                      </div>
                      {segment.processed_text}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* ---------------- Unchanged segments ---------------- */}
          {unchanged.length > 0 && (
            <div className="border-t border-zinc-800">
              <button
                onClick={() => setShowUnchanged(v => !v)}
                className="w-full px-4 py-2 text-left text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer"
              >
                {showUnchanged ? '▾' : '▸'} {unchanged.length} unchanged segment
                {unchanged.length === 1 ? '' : 's'}
              </button>
              {showUnchanged && (
                <div className="px-4 pb-3 space-y-1.5">
                  {unchanged.map(segment => (
                    <div key={segment.id} className="flex gap-2 text-xs">
                      <span className="text-[10px] font-mono text-zinc-700 pt-0.5 shrink-0">
                        {formatTimestamp(segment.start_time)}
                      </span>
                      <span className="text-zinc-500">{segment.original_text}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
