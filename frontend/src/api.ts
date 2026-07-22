export interface LanguageOption {
  code: string;
  name: string;
}

export interface WordCorrection {
  index: number;
  original: string;
  cleaned: string;
  reason: string;
}

export interface TranscriptionValidationResponse {
  success: boolean;
  language_code: string;
  language_name: string;
  raw_text: string;
  clean_text: string;
  hallucinated: boolean;
  logs: string[];
  corrections: WordCorrection[];
  error?: string;
  details?: Record<string, any>;
}

export interface JobSubmissionRequest {
  projectId: string;
  sourceUrl?: string;
  language?: string;
  authText?: string;
  cookiesFile?: File | null;
}

const API_BASE = '/api/v1';

/**
 * Fetches all registered language profiles from the 3-Layer backend registry.
 */
export async function fetchLanguages(): Promise<LanguageOption[]> {
  try {
    const response = await fetch(`${API_BASE}/languages/`);
    if (response.ok) {
      const data = await response.json();
      return data.languages || [];
    }
    return [];
  } catch (error) {
    console.warn('Failed to load dynamic language registry:', error);
    return [];
  }
}

/**
 * Validates raw transcription text through Layer 3 Sanitization & Orthography Engine.
 */
export async function validateTranscriptionText(
  text: string,
  language: string
): Promise<TranscriptionValidationResponse> {
  try {
    const response = await fetch(`${API_BASE}/validate-transcription/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, language }),
    });

    const data = await response.json();
    if (response.ok) {
      return data;
    }
    return {
      success: false,
      language_code: language,
      language_name: language,
      raw_text: text,
      clean_text: text,
      hallucinated: true,
      logs: [`[API ERROR] ${data.error || 'Server validation error'}`],
      corrections: [],
      error: data.error,
      details: data.details,
    };
  } catch (error: any) {
    console.error('Linguistic validation API error:', error);
    return {
      success: false,
      language_code: language,
      language_name: language,
      raw_text: text,
      clean_text: text,
      hallucinated: true,
      logs: ['[NETWORK ERROR] Could not reach backend validation endpoint.'],
      corrections: [],
    };
  }
}

/**
 * Submits a new transcription job to DRF backend with dynamic language parameters and authentication headers.
 */
export async function submitTranscriptionJob(req: JobSubmissionRequest): Promise<any> {
  let cleanUrl = (req.sourceUrl || '').trim();
  if (cleanUrl && !cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')) {
    cleanUrl = 'https://' + cleanUrl;
  }

  const hasAuth = req.authText && req.authText.trim();
  let response: Response;

  if (hasAuth || req.cookiesFile) {
    const form = new FormData();
    form.append('project', req.projectId);
    form.append('input_source', 'url');
    form.append('source_url', cleanUrl);
    form.append('file_path', '');
    form.append('enable_code_switching', 'true');
    if (req.language) {
      form.append('language', req.language.trim());
    }

    if (req.cookiesFile) {
      form.append('cookies_file', req.cookiesFile);
    } else if (hasAuth) {
      const txt = req.authText!.trim();
      try {
        const parsed = JSON.parse(txt);
        if (parsed && typeof parsed === 'object') {
          form.append('headers', JSON.stringify(parsed));
        } else {
          form.append('cookies', txt);
        }
      } catch (e) {
        if (txt.includes(':')) {
          const lines = txt.split(/\r?\n/);
          const obj: Record<string, string> = {};
          lines.forEach(l => {
            const idx = l.indexOf(':');
            if (idx > 0) {
              const k = l.slice(0, idx).trim();
              const v = l.slice(idx + 1).trim();
              if (k) obj[k] = v;
            }
          });
          if (Object.keys(obj).length > 0) {
            form.append('headers', JSON.stringify(obj));
          } else {
            form.append('cookies', txt);
          }
        } else {
          form.append('cookies', txt);
        }
      }
    }

    response = await fetch(`${API_BASE}/transcriptions/`, {
      method: 'POST',
      body: form,
    });
  } else {
    const payload = {
      project: req.projectId,
      input_source: 'url',
      source_url: cleanUrl,
      file_path: '',
      language: req.language ? req.language.trim() : '',
      enable_code_switching: true
    };
    response = await fetch(`${API_BASE}/transcriptions/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  if (!response.ok) {
    let msg = `Server returned status ${response.status}`;
    try {
      const errData = await response.json();
      if (errData.error) msg = errData.error;
      if (errData.details) msg += `: ${JSON.stringify(errData.details)}`;
    } catch (_) {
      const raw = await response.text();
      if (raw) msg = raw;
    }
    throw new Error(msg);
  }

  return await response.json();
}
