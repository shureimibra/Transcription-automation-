import React, { useMemo, useState } from 'react';

// The pre-flight workspace: everything the user wants to adjust BEFORE a
// transcript is sent to the AI, in one place -- document metadata, which part
// of the transcript to send, which operation to run, and (for Custom) the
// prompt itself.
//
// This panel deliberately holds no opinion about what the AI is allowed to
// do. `capabilities` arrives from GET /ai-capabilities/ -- the server decides
// whether a key is configured, which operations exist, and how much budget
// is left; this only renders that answer. Every submission is re-validated
// and re-costed server-side regardless of what this component sends, so a
// tampered-with request gains nothing.
//
// Metadata and the range are saved/sent separately on purpose: metadata is
// job-level display data that exports read at render time (same model as
// speaker aliases), so it can be edited and re-saved any number of times
// without re-running anything and without spending a token.

const OPERATION_HELP = {
  correct: 'Fixes mis-heard words, punctuation, and capitalization. Does not rewrite style.',
  academic: 'Converts spoken technical phrases into notation ("x squared" -> x²). Plain Unicode, no LaTeX.',
  translate: 'Translates each segment into the chosen language, keeping timings and speakers.',
  improve: 'Tightens wording and removes filler while keeping the speaker\'s meaning and voice.',
  custom: 'Applies your own instruction to every segment in the range.',
};

// Mirrors the backend whitelist (TranscriptionJobViewSet.METADATA_FIELDS).
// Anything not listed here is rejected by the metadata endpoint with a 400
// naming the offending key, so this list and that one must agree.
const METADATA_FIELDS = [
  { key: 'title', label: 'Document title', placeholder: 'Used as the export header' },
  { key: 'owner', label: 'Owner / author', placeholder: 'Who this transcript belongs to' },
  { key: 'recorded_date', label: 'Recorded on', placeholder: 'e.g. 2026-03-14' },
  { key: 'location', label: 'Location', placeholder: 'Where it was recorded' },
  { key: 'subject', label: 'Subject', placeholder: 'e.g. Physics lecture' },
  { key: 'notes', label: 'Notes', placeholder: 'Anything worth recording', multiline: true },
];

const MAX_CUSTOM_INSTRUCTION_CHARS = 2000;

function formatTimestamp(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

export default function AIAssistPanel({
  segments,
  metadata,
  capabilities,
  translationLanguages,
  revisions,
  loadRevisionSegments,
  busy,
  onSaveMetadata,
  onSubmit,
  onClose,
}) {
  const [operation, setOperation] = useState('correct');
  const [targetLanguage, setTargetLanguage] = useState('');
  const [customInstruction, setCustomInstruction] = useState('');
  // Which version to read FROM. '' is the original transcript.
  //
  // The reason this exists: correcting ASR mistakes and then translating the
  // corrected text is a much better result than translating raw ASR output,
  // and without a source picker every operation would fork off the original
  // and there would be no way to compose them.
  const [sourceRevisionId, setSourceRevisionId] = useState('');
  // The chosen source's segments, once fetched. Null means "using the
  // original", which is already in props.
  const [sourceSegments, setSourceSegments] = useState(null);
  const [loadingSource, setLoadingSource] = useState(false);
  // Inclusive in the UI (a user picking "segment 1 to segment 40" means both
  // ends), converted to the backend's exclusive end on submit. Getting this
  // wrong by one silently drops the last segment the user asked for.
  const [startIndex, setStartIndex] = useState(0);
  const [endIndex, setEndIndex] = useState(segments.length > 0 ? segments.length - 1 : 0);
  // Which quality gate to run, '' meaning "whatever the server defaults to
  // for this operation". Resolved against capabilities below rather than
  // hard-coded: the server owns the registry, and a list duplicated here
  // would drift out of step with it the first time a profile is added.
  const [qualityProfileChoice, setQualityProfileChoice] = useState('');
  const [metadataDraft, setMetadataDraft] = useState(() => ({ ...(metadata || {}) }));
  const [tagsDraft, setTagsDraft] = useState(() => (metadata?.tags || []).join(', '));
  const [metadataSaved, setMetadataSaved] = useState(false);
  const [savingMetadata, setSavingMetadata] = useState(false);

  // Everything downstream -- the range dropdowns, the count, the cost
  // estimate -- reads this rather than the original transcript, because a
  // chained run's range is indexed against the version it reads from. A
  // previous operation may itself have covered only part of the transcript,
  // so the two lists are not interchangeable.
  const rangeSegments = sourceSegments || segments;
  const total = rangeSegments.length;
  const textOf = (seg) => (seg.effective_text ?? seg.text ?? '');

  // Only finished versions can be built on: an in-flight one has no complete
  // text yet, and the server refuses it anyway.
  const sourceOptions = (revisions || []).filter(r => r.status === 'completed');

  const handleSourceChange = async (revisionId) => {
    setSourceRevisionId(revisionId);
    setStartIndex(0);
    if (!revisionId) {
      setSourceSegments(null);
      setEndIndex(segments.length > 0 ? segments.length - 1 : 0);
      return;
    }
    setLoadingSource(true);
    try {
      // Summaries carry no segments (see AIRevisionSummarySerializer), so the
      // chosen version is fetched in full -- its length and its text are both
      // needed here to show the range and price the request.
      const loaded = (await loadRevisionSegments?.(revisionId)) || [];
      setSourceSegments(loaded);
      setEndIndex(loaded.length > 0 ? loaded.length - 1 : 0);
    } finally {
      setLoadingSource(false);
    }
  };

  const operations = capabilities?.operations || [];
  const aiEnabled = Boolean(capabilities?.ai_enabled);

  const qualityProfiles = capabilities?.quality_profiles || [];
  // What the server says it would do for this operation if we sent nothing.
  // Shown as "(recommended)" and used as the initial selection, so an
  // operator who sets AI_QUALITY_DEFAULT_PROFILE=auto gets the gate switched
  // on in the UI too, without the frontend deciding policy for itself.
  const serverDefaultProfile =
    capabilities?.default_quality_profile?.[operation] || 'none';
  const qualityProfile = qualityProfileChoice || serverDefaultProfile;
  const selectedQualityProfile = qualityProfiles.find(p => p.value === qualityProfile);

  const selectedCount = Math.max(0, Math.min(endIndex, total - 1) - startIndex + 1);
  const selectedChars = useMemo(
    () => rangeSegments
      .slice(startIndex, Math.min(endIndex, total - 1) + 1)
      .reduce((sum, seg) => sum + textOf(seg).length, 0),
    [rangeSegments, startIndex, endIndex, total]
  );
  // Same rule of thumb the server uses (ai_budget.CHARS_PER_TOKEN, doubled
  // for the echoed output) so the number shown here can't wildly disagree
  // with the one that refuses the request. Labelled an estimate because it is.
  const estimatedTokens = Math.ceil(selectedChars / 4) * 2 + 400;
  const overBudget =
    capabilities?.tokens_remaining_today !== undefined &&
    estimatedTokens > capabilities.tokens_remaining_today;
  const overPerRequest =
    capabilities?.max_tokens_per_request !== undefined &&
    estimatedTokens > capabilities.max_tokens_per_request;

  const customTooLong = customInstruction.length > MAX_CUSTOM_INSTRUCTION_CHARS;
  const canSubmit =
    aiEnabled &&
    !busy &&
    !loadingSource &&
    total > 0 &&
    selectedCount > 0 &&
    !overPerRequest &&
    (operation !== 'translate' || Boolean(targetLanguage)) &&
    (operation !== 'custom' || (customInstruction.trim().length > 0 && !customTooLong));

  const handleMetadataChange = (key, value) => {
    setMetadataDraft(prev => ({ ...prev, [key]: value }));
    setMetadataSaved(false);
  };

  const handleSaveMetadata = async () => {
    setSavingMetadata(true);
    try {
      const payload = { ...metadataDraft };
      const tags = tagsDraft.split(',').map(t => t.trim()).filter(Boolean);
      // Sent as [] rather than omitted so clearing every tag actually clears
      // them -- the endpoint treats an empty value as "remove this field".
      payload.tags = tags;
      await onSaveMetadata(payload);
      setMetadataSaved(true);
    } finally {
      setSavingMetadata(false);
    }
  };

  const handleSubmit = () => {
    if (!canSubmit) return;
    const inclusiveEnd = Math.min(endIndex, total - 1);
    onSubmit({
      operation,
      target_language: operation === 'translate' ? targetLanguage : undefined,
      custom_instruction: operation === 'custom' ? customInstruction.trim() : undefined,
      source_revision: sourceRevisionId || undefined,
      quality_profile: qualityProfile,
      segment_start_index: startIndex,
      // +1: the UI's inclusive end becomes the backend's exclusive slice end.
      segment_end_index: inclusiveEnd + 1,
    });
  };

  return (
    <div className="border border-zinc-800 rounded-xl bg-[#18181b] p-4 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">✨ AI Assist</h3>
          <p className="text-xs text-zinc-500 mt-0.5">
            Review and edit before submitting. Your original transcript is never changed.
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer"
        >
          Close
        </button>
      </div>

      {!aiEnabled && (
        <div className="text-xs text-amber-400 bg-amber-950/30 border border-amber-900/50 rounded-lg p-3">
          AI assistance is not configured on this server. The offline Academic Format
          and Translate options above still work.
        </div>
      )}



      {/* ---------------- Range ---------------- */}
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
          Range to process
        </h4>
        {total === 0 ? (
          <p className="text-xs text-zinc-500">This job has no segments yet.</p>
        ) : (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <label className="flex items-center gap-1.5">
                <span className="text-[11px] text-zinc-500">From</span>
                <select
                  value={startIndex}
                  onChange={(e) => {
                    const next = Number(e.target.value);
                    setStartIndex(next);
                    if (next > endIndex) setEndIndex(next);
                  }}
                  className="px-2 py-1 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500 max-w-[11rem]"
                >
                  {rangeSegments.map((seg, i) => (
                    <option key={seg.id || i} value={i}>
                      {i + 1}. {formatTimestamp(seg.startTime ?? seg.start_time)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5">
                <span className="text-[11px] text-zinc-500">To</span>
                <select
                  value={Math.min(endIndex, total - 1)}
                  onChange={(e) => setEndIndex(Number(e.target.value))}
                  className="px-2 py-1 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500 max-w-[11rem]"
                >
                  {rangeSegments.map((seg, i) => (
                    <option key={seg.id || i} value={i} disabled={i < startIndex}>
                      {i + 1}. {formatTimestamp(seg.startTime ?? seg.start_time)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => { setStartIndex(0); setEndIndex(total - 1); }}
                className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] rounded-lg transition-colors cursor-pointer"
              >
                Whole transcript
              </button>
              <button
                onClick={() => setEndIndex(total - 1)}
                className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] rounded-lg transition-colors cursor-pointer"
              >
                To the end
              </button>
            </div>
            <p className="text-[11px] text-zinc-600">
              {selectedCount} of {total} segments · ~{estimatedTokens.toLocaleString()} tokens estimated
              {capabilities?.tokens_remaining_today !== undefined && (
                <> · {capabilities.tokens_remaining_today.toLocaleString()} left in today's budget</>
              )}
            </p>
            {overPerRequest && (
              <p className="text-[11px] text-red-400">
                This selection is larger than one request allows. Choose a smaller range.
              </p>
            )}
            {!overPerRequest && overBudget && (
              <p className="text-[11px] text-amber-400">
                This selection likely exceeds today's remaining budget and may be refused.
              </p>
            )}
          </>
        )}
      </section>

      {/* ---------------- Operation ---------------- */}
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
          What should the AI do?
        </h4>
        <div className="flex flex-wrap gap-1.5">
          {(operations.length ? operations : [{ value: 'correct', label: 'Corrected' }]).map(op => (
            <button
              key={op.value}
              onClick={() => setOperation(op.value)}
              className={`px-3 py-1.5 text-xs rounded-full transition-all cursor-pointer ${
                operation === op.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
              }`}
            >
              {op.label}
            </button>
          ))}
        </div>
        {OPERATION_HELP[operation] && (
          <p className="text-[11px] text-zinc-600">{OPERATION_HELP[operation]}</p>
        )}

        {operation === 'translate' && (
          <select
            value={targetLanguage}
            onChange={(e) => setTargetLanguage(e.target.value)}
            className="w-full sm:w-auto px-3 py-1.5 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500"
          >
            <option value="">Translate to…</option>
            {(translationLanguages || []).map(lang => (
              <option key={lang.code} value={lang.code}>{lang.name}</option>
            ))}
          </select>
        )}

        {operation === 'custom' && (
          <div className="space-y-1">
            <textarea
              rows={3}
              value={customInstruction}
              onChange={(e) => setCustomInstruction(e.target.value)}
              placeholder="e.g. Expand every abbreviation on first use and mark uncertain terms with [?]."
              className="w-full px-2.5 py-2 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500 resize-y"
            />
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-zinc-600">
                Applied to every segment in the range. Timings and speakers are never changed.
              </span>
              <span className={`text-[11px] ${customTooLong ? 'text-red-400' : 'text-zinc-600'}`}>
                {customInstruction.length}/{MAX_CUSTOM_INSTRUCTION_CHARS}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* ---------------- Quality review ---------------- */}
      {qualityProfiles.length > 1 && (
        <section className="space-y-2">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
            Quality review
          </h4>
          <select
            value={qualityProfile}
            onChange={(e) => setQualityProfileChoice(e.target.value)}
            className="w-full px-3 py-1.5 bg-[#121214] border border-zinc-800 rounded-lg text-xs focus:outline-none focus:border-blue-500"
          >
            {qualityProfiles.map(profile => (
              <option key={profile.value} value={profile.value}>
                {profile.label}
                {profile.value === serverDefaultProfile ? ' (recommended)' : ''}
              </option>
            ))}
          </select>
          {selectedQualityProfile?.description && (
            <p className="text-[11px] text-zinc-600">
              {selectedQualityProfile.description}
            </p>
          )}
          {qualityProfile !== 'none' && (
            // Said plainly and up front, because it is the honest trade and
            // the user is the one paying for it: a gated run costs more and
            // takes longer, and can finish by REFUSING to mark the result
            // ready. Discovering that after the fact reads as a malfunction.
            <p className="text-[11px] text-amber-500/80">
              Adds a source-fidelity audit after translation. Uses more tokens
              and takes longer, and may finish as “review required” rather
              than approved.
            </p>
          )}
        </section>
      )}

      <div className="flex items-center gap-2 pt-1 border-t border-zinc-800">
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="mt-3 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer"
        >
          {busy ? 'Processing…' : `Send ${selectedCount} segment${selectedCount === 1 ? '' : 's'} to AI`}
        </button>
        {capabilities?.model && (
          <span className="mt-3 text-[11px] text-zinc-600">{capabilities.model}</span>
        )}
      </div>
    </div>
  );
}
