import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { auth as authApi } from '../api/auth.js';
import { setToken, setUnauthorizedHandler } from '../api/client.js';
import { TASKER_BASE } from '../tasker/brand.js';

// Session state for the whole app.
//
// THE SERVER DECIDES WHO YOU ARE. The role, the capabilities and the worker
// profile all come from GET /workforce/me/ on every load; nothing here
// infers them from a stored value. A tampered localStorage can at most
// produce a token the server rejects -- it cannot manufacture a role.
//
// CAPABILITIES ARE A LIST FROM THE SERVER, NOT A SWITCH ON THE ROLE STRING.
// The backend sends what this account may reach, so adding a fourth role
// later is a backend change and this file keeps working. A component asks
// `can('workforce')`, never `role === 'tasker'`.
const AuthContext = createContext(null);

/** The capability names the backend sends. Mirrors workforce/roles.py. */
export const CAPABILITY = {
  WORKFORCE: 'workforce',
  ADMINISTRATION: 'administration',
  STUDIO: 'studio',
  MEASURED: 'measured',
  /** Outside Eagle taskers: exact-ID lookup and a private copy. */
  DESK: 'desk',
};

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [capabilities, setCapabilities] = useState([]);
  // 'checking' until the bootstrap call settles, so nothing renders a
  // logged-out view for a frame and bounces an authenticated user to login.
  const [state, setState] = useState('checking');

  const clear = useCallback(() => {
    setToken(null);
    setUser(null);
    setProfile(null);
    setCapabilities([]);
    setState('anonymous');
  }, []);

  // Any 401 anywhere drops the whole app to the login screen, rather than
  // each caller handling an expired session itself.
  useEffect(() => {
    setUnauthorizedHandler(clear);
    return () => setUnauthorizedHandler(null);
  }, [clear]);

  // Bumped by every successful sign-in. The bootstrap call below captures it
  // before it starts and refuses to act on a stale answer -- without this, a
  // `me()` 401 that lands AFTER a fast sign-in clears the session that
  // sign-in had just established, and the user bounces back to the login
  // screen having done nothing wrong. Found in a real browser; invisible to
  // the component tests, where the two never overlap.
  const sessionVersion = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const startedAt = sessionVersion.current;
    (async () => {
      try {
        const me = await authApi.me();
        if (cancelled || sessionVersion.current !== startedAt) return;
        setUser({
          username: me.username, isAdmin: Boolean(me.is_admin), role: me.role || null,
          // Only taskers are ever unconfirmed; everyone else reads true.
          emailVerified: me.email_verified !== false,
        });
        setProfile(me.profile || null);
        setCapabilities(me.capabilities || []);
        setState('authenticated');
      } catch {
        if (cancelled || sessionVersion.current !== startedAt) return;
        clear();
      }
    })();
    return () => { cancelled = true; };
  }, [clear]);

  // Any answer that signs someone in -- password, sign-up, Google -- has the
  // same shape, so all three land here.
  const adopt = useCallback((result) => {
    sessionVersion.current += 1;
    setToken(result.token);
    setUser({
      username: result.user.username,
      isAdmin: Boolean(result.user.is_admin),
      role: result.user.role || null,
      emailVerified: result.email_verified !== false,
    });
    setProfile(result.profile || null);
    setCapabilities(result.capabilities || []);
    setState('authenticated');
    return result;
  }, []);

  /** After the emailed code is accepted -- no reload needed. */
  const markEmailVerified = useCallback(
    () => setUser((u) => (u ? { ...u, emailVerified: true } : u)), [],
  );

  const login = useCallback(
    async (username, password) => adopt(await authApi.login(username, password)),
    [adopt],
  );

  const logout = useCallback(async () => {
    // The local session is cleared even if the network call fails: leaving
    // someone signed in because logout errored is the wrong failure mode.
    try { await authApi.logout(); } catch { /* ignore */ }
    clear();
  }, [clear]);

  // `can` is the only thing components should ask. Everything else here is
  // for display (showing someone their own role) or for the legacy
  // `isAdmin` callers.
  const can = useCallback(
    (capability) => capabilities.includes(capability),
    [capabilities],
  );

  const value = useMemo(() => ({
    user, profile, state, login, adopt, markEmailVerified, logout, can, capabilities,
    role: user?.role || null,
    isAuthenticated: state === 'authenticated',
    isChecking: state === 'checking',
    isAdmin: Boolean(user?.isAdmin),
    // Where this account belongs when it lands on the app with no route in
    // mind. A tool user has no queue, so sending them to the worker
    // dashboard would show them an empty page they can do nothing with.
    homePath: capabilities.includes(CAPABILITY.WORKFORCE) ? '/'
      : capabilities.includes(CAPABILITY.STUDIO) ? '/studio'
      // An Eagle tasker has neither; their whole app lives under its own path.
      : capabilities.includes(CAPABILITY.DESK) ? TASKER_BASE
      : '/studio',
  }), [user, profile, state, login, adopt, markEmailVerified, logout, can, capabilities]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
