// The single place the frontend talks to the backend.
//
// WHY A CLIENT LAYER AT ALL. The existing workspace calls `fetch` inline in
// ~30 places, each re-deriving how to read an error. That was survivable for
// one self-contained view; with authentication it is not, because every call
// now needs a token, and a 401 has to mean "your session ended" rather than
// "this particular button broke".
//
// ERRORS ARE NORMALISED, NEVER RE-PHRASED. Phase 2's API already returns a
// client-safe `error` string on every failure (the `.safe_message` split the
// backend uses throughout). This layer surfaces that verbatim and invents a
// message only when the server sent none -- so a user never sees a stack
// trace, and never sees the frontend's guess at what the server meant.

const TOKEN_KEY = 'wf.token';

export class ApiError extends Error {
  constructor(message, { status = 0, payload = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }

  /** 409: the request was fine, the world moved. Worth a different message. */
  get isConflict() { return this.status === 409; }
  get isAuth() { return this.status === 401; }
  get isForbidden() { return this.status === 403; }
}

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || null; } catch { return null; }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode: the session still works, it just won't persist */ }
}

// Notified on any 401 so the app can drop to the login screen from anywhere,
// rather than each caller having to handle an expired session itself.
let onUnauthorized = null;
export function setUnauthorizedHandler(handler) { onUnauthorized = handler; }

const BASE = '/api/v1';

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/**
 * Django's CSRF cookie, when it has set one.
 *
 * The token header is what normally authenticates us, and Django's CSRF
 * check does not apply to it. But signing in also opens a session -- the
 * WebSocket needs one, because a browser cannot set an Authorization header
 * on a socket -- and a request that falls back to that session is
 * CSRF-protected. Sending the header when the cookie exists costs nothing
 * and keeps that path working.
 */
function csrfToken() {
  try {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : null;
  } catch {
    return null;
  }
}

async function request(path, {
  method = 'GET', body, signal, raw = false, unauthorizedIsExpected = false,
} = {}) {
  const headers = { Accept: 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Token ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (UNSAFE_METHODS.has(method)) {
    const csrf = csrfToken();
    if (csrf) headers['X-CSRFToken'] = csrf;
  }

  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    if (cause.name === 'AbortError') throw cause;
    // Genuinely offline or the server is down. Distinct from an HTTP error.
    throw new ApiError('Could not reach the server. Check your connection.', { status: 0 });
  }

  // A 401 on the authentication endpoints is an ANSWER, not an expired
  // session. Two things went wrong when they shared one code path:
  //
  //  1. A failed sign-in showed "your session has ended" -- confusing advice
  //     to someone who is trying to start one -- instead of the server's own
  //     "Invalid credentials."
  //  2. The bootstrap `me()` call races a fast sign-in. Its 401 arrives
  //     AFTER the login succeeded, and the global handler then cleared the
  //     token that login had just stored, dropping the user back to the
  //     login screen. A human typing for a few seconds rarely hit it; a slow
  //     network or an automated form fill hits it every time.
  //
  // Callers that already handle their own 401 pass `unauthorizedIsExpected`.
  if (response.status === 401 && !unauthorizedIsExpected) {
    setToken(null);
    if (onUnauthorized) onUnauthorized();
    throw new ApiError('Your session has ended. Please sign in again.', { status: 401 });
  }

  if (response.status === 204) return null;

  let payload = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try { payload = await response.json(); } catch { payload = null; }
  } else if (raw) {
    return response;
  }

  if (!response.ok) {
    // The server's own client-safe message, used verbatim when present.
    const message =
      (payload && (payload.error || payload.detail)) ||
      fallbackMessage(response.status);
    throw new ApiError(message, { status: response.status, payload });
  }
  return payload;
}

function fallbackMessage(status) {
  if (status === 403) return 'You do not have access to that.';
  if (status === 404) return 'That item could not be found.';
  if (status >= 500) return 'The server ran into a problem. Please try again.';
  return 'That request could not be completed.';
}

export const api = {
  get:   (path, opts) => request(path, { ...opts, method: 'GET' }),
  post:  (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
  patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
  del:   (path, opts) => request(path, { ...opts, method: 'DELETE' }),
};
