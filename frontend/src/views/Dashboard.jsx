import React, { useState, useEffect, useRef } from 'react';

export default function Dashboard() {
  const [sourceUrl, setSourceUrl] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState(null);
  const [enableDiarization, setEnableDiarization] = useState(false);
  const [enableWordSync, setEnableWordSync] = useState(true);
  const [jobStatus, setJobStatus] = useState('idle'); // idle, processing, completed, failed
  const [segments, setSegments] = useState([]);
  const [statusMessage, setStatusMessage] = useState('Ready');
  
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

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // Drag and drop event handlers
  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
      setStatusMessage(`Loaded local file: ${e.dataTransfer.files[0].name}`);
    }
  };

  // Trigger real backend transcription or fall back to mock
  const runTranscription = async () => {
    if (!file && !sourceUrl) {
      alert("Please upload an audio/video file or paste a public media URL.");
      return;
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
      let body;
      let headers = {};

      if (file) {
        body = new FormData();
        body.append('project', projectId);
        body.append('input_source', 'upload');
        body.append('file', file);
        body.append('source_url', '');
      } else {
        body = JSON.stringify({
          project: projectId,
          input_source: 'url',
          source_url: sourceUrl || '',
          file_path: ''
        });
        headers['Content-Type'] = 'application/json';
      }

      const response = await fetch(`${API_BASE}/transcriptions/`, {
        method: 'POST',
        headers,
        body,
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error('Backend create job error:', response.status, errorText);
        throw new Error('Failed to create job on backend.');
      }

      const job = await response.json();
      setStatusMessage('Job created. Initializing connections...');
      
      // Connect WS or fallback to Polling
      connectWebSocket(job.id);

    } catch (err) {
      console.error(err);
      setJobStatus('failed');
      setStatusMessage('Backend error. Please check the server logs for details.');
      console.error(err);
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
            text: msg.segment.text
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
              text: s.text
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

    // Try fetching from Django API
    try {
      // Find the last transcription job
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
      // Basic mock docx download format fallback in browser
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
            Antigravity Transcription Studio
          </h1>
          <p className="text-sm text-zinc-400">
            Enterprise Multilingual AI Transcription & Desktop Word Integration
          </p>
        </header>

        {/* Upload Container */}
        <section 
          className={`border-2 border-dashed rounded-xl p-8 text-center transition-all duration-300 ${
            dragActive ? 'border-blue-500 bg-blue-500/5' : 'border-zinc-800 bg-[#18181b]'
          }`}
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
        >
          <div className="space-y-4">
            <div className="flex flex-col items-center justify-center">
              <svg className="w-12 h-12 text-zinc-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              {file ? (
                <p className="text-sm font-semibold text-blue-400">Selected: {file.name}</p>
              ) : (
                <p className="text-sm font-semibold">Drag & Drop media here, or browse files</p>
              )}
              <p className="text-xs text-zinc-500 mt-1">Supports MP3, WAV, M4A, MP4, MKV</p>
            </div>
            
            <div className="flex items-center justify-center space-x-2">
              <span className="text-xs text-zinc-500">OR</span>
            </div>

            <input 
              type="text" 
              placeholder="Paste public media URL (YouTube, Vimeo, etc.)"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              className="w-full max-w-md px-4 py-2 bg-[#121214] border border-zinc-800 rounded-lg focus:outline-none focus:border-blue-500 text-sm transition-all"
            />
          </div>
        </section>

        {/* Options Dashboard */}
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
            <h2 className="text-lg font-bold text-zinc-300">Live Transcript Preview</h2>
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

          <div className="min-h-[200px] max-h-[350px] overflow-y-auto p-4 bg-[#121214] rounded-lg border border-zinc-900 text-sm space-y-3">
            {segments.length === 0 ? (
              <p className="text-zinc-500 italic text-center pt-16">Transcripts will load live here during processing...</p>
            ) : (
              segments.map((seg, idx) => (
                <div key={idx} className="flex flex-col space-y-1 transition-all duration-300 animate-fadeIn">
                  <div className="flex items-center space-x-2 text-xs">
                    <span className="font-semibold text-blue-400">{seg.speaker}</span>
                    <span className="text-zinc-500">({seg.start})</span>
                  </div>
                  <p className="text-zinc-300">{seg.text}</p>
                </div>
              ))
            )}
          </div>
        </section>

      </div>
    </div>
  );
}
