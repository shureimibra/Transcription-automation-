// Typed-ish wrappers over the Phase 2 workforce endpoints.
//
// Every task state change is a POST to a named action, mirroring the backend
// exactly: the server refuses a PATCH to `status` (405) because state moves
// only through the service layer that holds the row lock. The frontend
// therefore has no `updateStatus` at all -- there is nothing for it to call.
import { api } from './client.js';

// The authentication calls opt out of the global 401 handling: for these two
// a 401 is the answer to the question being asked, not a session that ended
// mid-flight. See the note in client.js.
export const auth = {
  login: (username, password) => api.post(
    '/workforce/auth/login/', { username, password }, { unauthorizedIsExpected: true },
  ),
  logout: () => api.post('/workforce/auth/logout/'),
  me: () => api.get('/workforce/me/', { unauthorizedIsExpected: true }),
};

export const tasks = {
  list: (params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== '' && v !== null)
    ).toString();
    return api.get(`/workforce/tasks/${query ? `?${query}` : ''}`);
  },
  get:    (id) => api.get(`/workforce/tasks/${id}/`),
  events: (id) => api.get(`/workforce/tasks/${id}/events/`),
  create: (data) => api.post('/workforce/tasks/', data),
  // Records that one accuracy-check finding was applied -- permanent and
  // additive, so a later check re-flagging the same phrase can be told
  // apart from a new one. Does not touch the transcript itself.
  applyFinding: (id, phrase, suggestion) =>
    api.post(`/workforce/tasks/${id}/apply-finding/`, { phrase, suggestion }),

  // Worker actions.
  claim:       (id) => api.post(`/workforce/tasks/${id}/claim/`),
  release:     (id, reason) => api.post(`/workforce/tasks/${id}/release/`, { reason }),
  start:       (id) => api.post(`/workforce/tasks/${id}/start/`),
  beginReview: (id) => api.post(`/workforce/tasks/${id}/begin-review/`),
  ready:       (id, revision) => api.post(`/workforce/tasks/${id}/ready/`, { revision }),
  submit:      (id, reference) => api.post(`/workforce/tasks/${id}/submit/`, { reference }),

  // Admin actions.
  assign:   (id, worker) => api.post(`/workforce/tasks/${id}/assign/`, { worker }),
  unassign: (id) => api.post(`/workforce/tasks/${id}/unassign/`),
  approve:  (id, note) => api.post(`/workforce/tasks/${id}/approve/`, { note }),
  reject:   (id, reason) => api.post(`/workforce/tasks/${id}/reject/`, { reason }),
  cancel:   (id, reason) => api.post(`/workforce/tasks/${id}/cancel/`, { reason }),
};

export const workers = {
  list:       () => api.get('/workforce/workers/'),
  create:     (data) => api.post('/workforce/workers/', data),
  activate:   (id) => api.post(`/workforce/workers/${id}/activate/`),
  deactivate: (id) => api.post(`/workforce/workers/${id}/deactivate/`),
};

// Accounts and roles. Admin-only on the server.
//
// `setRole` is a named action rather than a PATCH for the same reason task
// transitions are: promoting someone to tasker has to create their worker
// profile, and demoting one who holds work has to be refused. Those rules
// live in one server-side place, and a field write would go around them.
export const accounts = {
  list:    () => api.get('/workforce/accounts/'),
  create:  (data) => api.post('/workforce/accounts/', data),
  setRole: (id, role, extra = {}) =>
    api.post(`/workforce/accounts/${id}/set-role/`, { role, ...extra }),
  /** Reset a forgotten password. The admin picks it; nothing is mailed,
   *  because this deployment has no outbound mail and a reset link that
   *  silently goes nowhere is worse than no reset link. */
  setPassword: (id, password) =>
    api.post(`/workforce/accounts/${id}/set-password/`, { password }),
};

export const stats = {
  /** Live counters. No date range -- a queue depth does not have one. */
  overview: () => api.get('/workforce/stats/'),

  /** Date-scoped history. `from`/`to` are YYYY-MM-DD, both inclusive. */
  performance: ({ from, to } = {}) => {
    const query = new URLSearchParams(
      Object.entries({ from, to }).filter(([, v]) => v)
    ).toString();
    return api.get(`/workforce/stats/performance/${query ? `?${query}` : ''}`);
  },
};
