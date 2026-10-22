import { api } from './client.js';

// Sign-in, sign-out and "who am I" -- the only calls every kind of account
// makes. A file of its own so the tasker bundle can sign in without
// carrying the workforce and admin endpoints in api/workforce.js.
//
// The authentication calls opt out of the global 401 handling: for these two
// a 401 is the answer to the question being asked, not a session that ended
// mid-flight. See the note in client.js.
export const auth = {
  login: (username, password) => api.post(
    '/workforce/auth/login/', { username, password }, { unauthorizedIsExpected: true },
  ),
  /** The tasker sign-in page's own door: admits tasker accounts only. */
  deskLogin: (username, password) => api.post(
    '/workforce/auth/desk-login/', { username, password }, { unauthorizedIsExpected: true },
  ),
  logout: () => api.post('/workforce/auth/logout/'),
  me: () => api.get('/workforce/me/', { unauthorizedIsExpected: true }),

  // Public sign-up for taskers (backend tasker_auth.py).
  /** Whether sign-up is open, and the Google client id if Google is set up. */
  options: () => api.get('/workforce/auth/options/', { unauthorizedIsExpected: true }),
  signup: (fields) => api.post('/workforce/auth/signup/', fields, { unauthorizedIsExpected: true }),
  /** Confirm the account's email with the 6-digit code that was sent. */
  verifyEmail: (code) => api.post('/workforce/auth/verify-email/', { code }),
  resendCode: () => api.post('/workforce/auth/resend-code/'),
  /** `credential` is the signed ID token Google's button hands back. */
  google: (credential) => api.post(
    '/workforce/auth/google/', { credential }, { unauthorizedIsExpected: true },
  ),
};
