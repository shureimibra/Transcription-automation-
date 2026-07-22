import React, { useState, useEffect, useRef } from 'react';
import { fetchLanguages, submitTranscriptionJob } from '../api';

export default function Dashboard() {
  const [sourceUrl, setSourceUrl] = useState('');
  const [enableDiarization, setEnableDiarization] = useState(false);
  const [enableWordSync, setEnableWordSync] = useState(true);
  const [authText, setAuthText] = useState('');
  const [cookiesFile, setCookiesFile] = useState(null);
  const [languageOverride, setLanguageOverride] = useState('en');
  const [languageOptions, setLanguageOptions] = useState([]);
  const [languageSearch, setLanguageSearch] = useState('English (en)');
  const [showLanguageDropdown, setShowLanguageDropdown] = useState(false);
  const [jobStatus, setJobStatus] = useState('idle'); // idle, processing, completed, failed
  const [segments, setSegments] = useState([]);
  const [statusMessage, setStatusMessage] = useState('Ready');
  const [showRawText, setShowRawText] = useState(false);
  
  const [projectId, setProjectId] = useState(null);
  const pollIntervalRef = useRef(null);
  const wsRef = useRef(null);

  // Use Vite proxy during development so we avoid CORS and host-specific issues
  const API_BASE = '/api/v1';
  const WS_BASE = '/ws/transcription';

  // 1. Initial Setup: Fetch or Create default Project
  useEffect(() => {
    async function initProject() {
      try {
        const response = await fetch(`${API_BASE}/projects/`);
        if (response.ok) {
          const projects = await response.json();
          if (projects.length > 0) {
            setProjectId(projects[0].id);
          } else {
            // Create default project
            const createResp = await fetch(`${API_BASE}/projects/`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name: 'Default Project', description: 'Default workspace' }),
            });
            if (createResp.ok) {
              const newProj = await createResp.json();
              setProjectId(newProj.id);
            }
          }
        }
      } catch (err) {
        console.error('Backend API server not reachable yet. Please start the Django backend and refresh.', err);
      }
    }
    initProject();
    loadLanguages();

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const loadLanguages = async () => {
    const langs = await fetchLanguages();
    if (langs && langs.length > 0) {
      setLanguageOptions(langs);
    }
  };

  // Trigger real backend transcription
  const runTranscription = async () => {
    if (!sourceUrl || !sourceUrl.trim()) {
      alert("Please paste a public media URL to transcribe.");
      return;
    }

    let cleanUrl = sourceUrl.trim();
    if (cleanUrl && !cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')) {
      cleanUrl = 'https://' + cleanUrl;
      setSourceUrl(cleanUrl);
    }

    setSegments([]);
    setJobStatus('processing');
    setStatusMessage('Submitting transcription job...');

    if (!projectId) {
      setJobStatus('failed');
      setStatusMessage('Backend project initialization failed. Please verify the Django API is reachable.');
      return;
    }

    try {
      const job = await submitTranscriptionJob({
        projectId,
        sourceUrl: cleanUrl,
        language: languageOverride,
        authText,
        cookiesFile
      });

      setStatusMessage('Job created. Initializing connections...');
      connectWebSocket(job.id);

    } catch (err) {
      console.error(err);
      setJobStatus('failed');
      setStatusMessage(err.message || 'Backend error. Please check server logs.');
    }
  };

  // Connect Django Channels WebSocket
  const connectWebSocket = (jobId) => {
    try {
      const wsUrl = `${WS_BASE}/${jobId}/`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('Connected to transcription WebSocket');
        setStatusMessage('WebSocket connected. Processing...');
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'status_update') {
          setStatusMessage(msg.message);
          if (msg.status === 'completed') {
            setJobStatus('completed');
            ws.close();
          } else if (msg.status === 'failed') {
            setJobStatus('failed');
            ws.close();
          }
        } else if (msg.type === 'segment_added') {
          const seg = {
            speaker: msg.segment.speaker_label,
            start: formatTime(msg.segment.start_time),
            text: msg.segment.text,
            rawText: msg.segment.raw_text || msg.segment.text,
            hallucinated: msg.segment.hallucinated || false,
            logs: msg.segment.logs || [],
            corrections: msg.segment.corrections || []
          };
          setSegments(prev => [...prev, seg]);
          if (enableWordSync) {
            console.log(`[WORD SYNC EVENT] Typing: ${seg.speaker}: ${seg.text}`);
          }
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket error, falling back to HTTP polling...', err);
        startPolling(jobId);
      };

      ws.onclose = (event) => {
        if (event.code !== 1000 && jobStatus === 'processing') {
          console.warn('WebSocket closed abnormally, falling back to HTTP polling...');
          startPolling(jobId);
        }
      };

    } catch (e) {
      console.warn('Failed to start WebSocket, falling back to HTTP polling...', e);
      startPolling(jobId);
    }
  };

  // HTTP Polling Fallback
  const startPolling = (jobId) => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    
    pollIntervalRef.current = setInterval(async () => {
      try {
        // Fetch Job Details
        const jobResp = await fetch(`${API_BASE}/transcriptions/${jobId}/`);
        if (jobResp.ok) {
          const job = await jobResp.json();
          
          if (job.status === 'downloading') {
            setStatusMessage('Downloading media and extracting audio (Polling)...');
          } else if (job.status === 'transcribing') {
            setStatusMessage('Transcribing speech to text (Polling)...');
          }

          // Fetch segments
          const segsResp = await fetch(`${API_BASE}/transcriptions/${jobId}/segments/`);
          if (segsResp.ok) {
            const segs = await segsResp.json();
            const formatted = segs.map(s => ({
              speaker: s.speaker_label,
              start: formatTime(s.start_time),
              text: s.text,
              rawText: s.raw_text || s.text,
              hallucinated: s.hallucinated || false,
              logs: s.logs || [],
              corrections: s.corrections || []
            }));
            setSegments(formatted);
          }

          if (job.status === 'completed') {
            setJobStatus('completed');
            setStatusMessage('Transcription successfully completed.');
            clearInterval(pollIntervalRef.current);
          } else if (job.status === 'failed') {
            setJobStatus('failed');
            setStatusMessage(`Failed: ${job.error_message || 'Unknown error'}`);
            clearInterval(pollIntervalRef.current);
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 1500);
  };

  // Helper: format float seconds to MM:SS
  const formatTime = (secs) => {
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // Export Trigger from API
  const handleExport = async (format) => {
    if (segments.length === 0) {
      alert("No transcribed data exists to export.");
      return;
    }
    
    if (!projectId) {
      alert('Cannot export because the backend project was not initialized successfully.');
      return;
    }

    try {
      const jobsResp = await fetch(`${API_BASE}/transcriptions/`);
      if (jobsResp.ok) {
        const jobs = await jobsResp.json();
        if (jobs.length > 0) {
          const jobId = jobs[0].id;
          const exportUrl = `${API_BASE}/transcriptions/${jobId}/export/${format}/`;
          window.open(exportUrl, '_blank');
          return;
        }
      }
      exportLocal(format);
    } catch (e) {
      exportLocal(format);
    }
  };

  const exportLocal = (format) => {
    let content = "";
    let mimeType = "text/plain";
    let filename = `transcript.${format}`;

    if (format === 'txt') {
      content = segments.map(s => `${s.start} ${s.speaker}: ${s.text}`).join('\n');
    } else {
      content = segments.map(s => `${s.start} ${s.speaker}: ${s.text}`).join('\n');
      mimeType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    }

    const blob = new Blob([content], { type: mimeType });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
  };

  return (
    <div className="min-h-screen bg-[#121214] text-[#e4e4e7] p-8 font-sans">
      <div className="max-w-4xl mx-auto space-y-6">
        
        {/* Header Title */}
        <header className="text-center space-y-2">
          <h1 className="text-4xl font-extrabold tracking-tight bg-gradient-to-r from-blue-500 to-indigo-400 bg-clip-text text-transparent">
             Transcription Studio
          </h1>
          <p className="text-sm text-zinc-400">
            Enterprise Multilingual ASR & 3-Layer Orthographic Validation Framework (200+ Languages)
          </p>
        </header>

        {/* Upload Container */}
        <section className="border-2 border-dashed rounded-xl p-8 text-center bg-[#18181b] border-zinc-800">
          <div className="space-y-4">
            <div className="flex flex-col items-center justify-center">
              <svg className="w-12 h-12 text-zinc-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <p className="text-sm font-semibold">Paste a media or transcription service URL below</p>
              <p className="text-xs text-zinc-500 mt-1">Supports YouTube, TikTok, GoTranscript, Rev, and public media URLs</p>
            </div>

            <input 
              type="text" 
              placeholder="Paste a public media URL here (e.g. https://vt.tiktok.com/...)"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              className="w-full max-w-2xl px-4 py-3 bg-[#121214] border border-zinc-800 rounded-lg focus:outline-none focus:border-blue-500 text-sm transition-all"
            />

            <div className="mt-3 space-y-2 max-w-2xl w-full text-left">
              <label className="text-xs text-zinc-400">Authentication helper (paste headers JSON, header lines "Key: Value", cookie string "k=v", or upload cookies.txt)</label>
              <div className="flex items-start space-x-2">
                <textarea
                  placeholder="Paste headers JSON, header lines, or cookie string here"
                  value={authText}
                  onChange={(e) => setAuthText(e.target.value)}
                  className="flex-1 px-3 py-2 bg-[#121214] border border-zinc-800 rounded-lg text-xs text-zinc-300"
                  rows={3}
                />
                <div className="flex flex-col items-start">
                  <input type="file" accept=".txt,.cookies" onChange={(e) => setCookiesFile(e.target.files && e.target.files[0])} className="text-xs" title="Upload cookies.txt" />
                  <button type="button" className="mt-2 text-xs text-zinc-400 underline" onClick={() => { setAuthText(''); setCookiesFile(null); }}>Clear</button>
                </div>
              </div>
            </div>

            {/* Language Search & Select Component */}
            <div className="mt-3 space-y-2 max-w-2xl w-full text-left relative">
              <div className="flex justify-between items-center">
                <label className="text-xs text-zinc-400 font-medium">Target Language / Orthography (200+ Languages)</label>
                {languageOverride && (
                  <span className="text-[11px] text-blue-400 font-semibold bg-blue-500/10 px-2 py-0.5 rounded border border-blue-500/20 font-mono">
                    Target: {languageOverride.toUpperCase()}
                  </span>
                )}
              </div>
              <div className="relative flex items-center">
                <input
                  type="text"
                  placeholder="Search language (e.g. Kikuyu, Oromo, Somali, Russian, Arabic, German, French, Turkish...)"
                  value={languageSearch}
                  onChange={(e) => {
                    const val = e.target.value;
                    setLanguageSearch(val);
                    setShowLanguageDropdown(true);
                    if (!val.trim()) {
                      setLanguageOverride('');
                    } else {
                      const match = languageOptions.find(l => 
                        l.code.toLowerCase() === val.trim().toLowerCase() || 
                        l.name.toLowerCase() === val.trim().toLowerCase()
                      );
                      if (match) {
                        setLanguageOverride(match.code);
                      } else {
                        setLanguageOverride(val.trim().toLowerCase());
                      }
                    }
                  }}
                  onFocus={() => setShowLanguageDropdown(true)}
                  onBlur={() => setTimeout(() => setShowLanguageDropdown(false), 200)}
                  className="w-full px-3 py-2 pr-8 bg-[#121214] border border-zinc-800 rounded-lg text-xs text-zinc-200 focus:outline-none focus:border-blue-500 transition-colors"
                />
                {languageSearch && (
                  <button
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setLanguageSearch('');
                      setLanguageOverride('');
                    }}
                    className="absolute right-2 text-zinc-400 hover:text-zinc-200 text-sm focus:outline-none"
                    title="Clear language selection"
                  >
                    ✕
                  </button>
                )}
              </div>

              {showLanguageDropdown && (
                <div className="absolute z-30 mt-1 w-full max-h-60 overflow-y-auto rounded-xl border border-zinc-800 bg-[#0f172a] shadow-2xl divide-y divide-zinc-800/50">
                  {languageOptions.length === 0 ? (
                    <div className="p-3 text-xs text-zinc-400 italic">Loading languages...</div>
                  ) : (
                    (() => {
                      const query = languageSearch.toLowerCase().trim();
                      const filtered = languageOptions.filter(lang => 
                        !query || 
                        lang.name.toLowerCase().includes(query) || 
                        lang.code.toLowerCase().includes(query)
                      );

                      if (filtered.length === 0) {
                        return (
                          <div 
                            onMouseDown={(e) => {
                              e.preventDefault();
                              const customCode = languageSearch.trim().toLowerCase();
                              setLanguageOverride(customCode);
                              setShowLanguageDropdown(false);
                            }}
                            className="p-3 text-xs text-zinc-300 hover:bg-zinc-800 cursor-pointer"
                          >
                            Use custom language code: <span className="font-semibold text-blue-400">"{languageSearch}"</span>
                          </div>
                        );
                      }

                      return filtered.map((lang) => {
                        const isSelected = languageOverride === lang.code;
                        return (
                          <button
                            key={lang.code}
                            type="button"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              setLanguageOverride(lang.code);
                              setLanguageSearch(`${lang.name} (${lang.code})`);
                              setShowLanguageDropdown(false);
                            }}
                            className={`w-full text-left px-3.5 py-2.5 text-xs flex justify-between items-center transition-colors ${
                              isSelected ? 'bg-blue-600/20 text-blue-300 font-semibold' : 'text-zinc-200 hover:bg-zinc-800/80'
                            }`}
                          >
                            <span className="font-medium">{lang.name}</span>
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                              {lang.code}
                            </span>
                          </button>
                        );
                      });
                    })()
                  )}
                </div>
              )}
              <p className="text-[10px] text-zinc-500 mt-1">
                Selected code (<span className="font-mono text-zinc-400">{languageOverride || 'Auto-detect'}</span>) will enforce orthography rules and character whitelisting.
              </p>
            </div>
          </div>
        </section>

        {/* Job Configurations Panel */}
        <section className="bg-[#18181b] p-6 rounded-xl border border-zinc-800 space-y-4">
          <h2 className="text-lg font-bold text-zinc-300">Job Configurations</h2>
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <label className="flex items-center space-x-3 cursor-pointer text-sm">
              <input 
                type="checkbox" 
                checked={enableWordSync}
                onChange={(e) => setEnableWordSync(e.target.checked)}
                className="accent-blue-500" 
              />
              <span>Live Microsoft Word Sync</span>
            </label>

            <label className="flex items-center space-x-3 cursor-pointer text-sm">
              <input 
                type="checkbox" 
                checked={enableDiarization}
                onChange={(e) => setEnableDiarization(e.target.checked)}
                className="accent-blue-500" 
              />
              <span>Enable Speaker Diarization</span>
            </label>

            <button 
              onClick={runTranscription}
              disabled={jobStatus === 'processing'}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-800 text-white font-bold py-2 rounded-lg text-sm transition-colors cursor-pointer"
            >
              {jobStatus === 'processing' ? 'Processing...' : 'Run Transcription'}
            </button>
          </div>

          <div className="pt-2">
            <div className="flex items-center justify-between text-xs text-zinc-400">
              <span>Status: <span className="font-semibold text-blue-400">{statusMessage}</span></span>
              {jobStatus === 'processing' && (
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-t-blue-500 border-zinc-800" />
              )}
            </div>
          </div>
        </section>

        {/* Live Preview Display Panel */}
        <section className="bg-[#18181b] p-6 rounded-xl border border-zinc-800 space-y-4">
          <div className="flex justify-between items-center">
            <div className="flex items-center space-x-3">
              <h2 className="text-lg font-bold text-zinc-300">Live Transcript Preview</h2>
              <button
                onClick={() => setShowRawText(!showRawText)}
                className="text-xs px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-medium transition-all"
              >
                {showRawText ? 'Showing Raw Stream' : 'Showing Clean Transcript'}
              </button>
            </div>
            <div className="flex space-x-2">
              <button 
                onClick={() => handleExport('docx')}
                className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-xs rounded transition-all"
              >
                Export DOCX
              </button>
              <button 
                onClick={() => handleExport('txt')}
                className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-xs rounded transition-all"
              >
                Export TXT
              </button>
            </div>
          </div>

          <div className="min-h-[220px] max-h-[380px] overflow-y-auto p-4 bg-[#121214] rounded-lg border border-zinc-900 text-sm space-y-4">
            {segments.length === 0 ? (
              <p className="text-zinc-500 italic text-center pt-16">Transcripts will load live here during processing...</p>
            ) : (
              segments.map((seg, idx) => (
                <div key={idx} className="flex flex-col space-y-1.5 transition-all duration-300 animate-fadeIn border-b border-zinc-900/50 pb-3">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center space-x-2">
                      <span className="font-semibold text-blue-400">{seg.speaker}</span>
                      <span className="text-zinc-500">({seg.start})</span>
                    </div>
                    {seg.hallucinated && (
                      <span className="text-[10px] bg-amber-500/10 border border-amber-500/30 text-amber-400 px-2 py-0.5 rounded font-mono font-medium">
                        ⚠️ Orthography Rules Applied
                      </span>
                    )}
                  </div>

                  <p className="text-zinc-200 leading-relaxed font-sans">
                    {showRawText ? seg.rawText : seg.text}
                  </p>

                  {/* Corrections Diff Chips */}
                  {seg.corrections && seg.corrections.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {seg.corrections.map((corr, cIdx) => (
                        <span key={cIdx} className="text-[10px] px-2 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-zinc-300 font-mono" title={corr.reason}>
                          <span className="line-through text-red-400 mr-1">{corr.original}</span>
                          <span className="text-green-400 font-bold">→ {corr.cleaned}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Diagnostic Logs */}
                  {seg.logs && seg.logs.length > 0 && (
                    <div className="text-[10px] text-amber-300/80 font-mono space-y-0.5 pt-1 bg-amber-500/5 p-2 rounded border border-amber-500/10">
                      {seg.logs.map((logMsg, lIdx) => (
                        <div key={lIdx}>{logMsg}</div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </section>

      </div>
    </div>
  );
}
