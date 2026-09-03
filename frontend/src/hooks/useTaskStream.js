import { useEffect, useRef } from 'react';

/**
 * Live task updates over `ws/workforce/`.
 *
 * THE SOCKET NEVER CARRIES DATA WE TRUST. A message says only that some task
 * changed; the handler re-fetches through the REST API, which applies the
 * caller's visibility rules and serializer. So a compromised or confused
 * socket can at worst cause an extra authorised fetch.
 *
 * IT IS AN OPTIMISATION, NOT A DEPENDENCY. Every screen that uses this also
 * polls (see usePolling). If the socket never connects -- an old browser, a
 * proxy that strips upgrades, Daphne not running -- the UI is a few seconds
 * slower and nothing else. That is why a failed connection is not surfaced
 * as an error to the user.
 *
 * RECONNECTION BACKS OFF. A server restart with six tabs open should not
 * become six reconnect attempts a second; the delay grows to 30s and resets
 * on a successful open.
 */
const MAX_BACKOFF_MS = 30000;

export function useTaskStream(onChange, enabled = true) {
  const handlerRef = useRef(onChange);
  handlerRef.current = onChange;

  useEffect(() => {
    if (!enabled || typeof WebSocket === 'undefined') return undefined;

    let socket = null;
    let retryTimer = null;
    let attempt = 0;
    let closed = false;

    const url = () => {
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      return `${scheme}://${window.location.host}/ws/workforce/`;
    };

    const connect = () => {
      if (closed) return;
      try {
        socket = new WebSocket(url());
      } catch {
        schedule();
        return;
      }

      socket.onopen = () => { attempt = 0; };

      socket.onmessage = (event) => {
        let message = null;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message?.type === 'task.changed') {
          handlerRef.current?.(message);
        }
      };

      socket.onclose = (event) => {
        socket = null;
        // 4401/4403 mean the server refused us: not signed in, or not an
        // active worker. Retrying cannot fix either, and the 401 handler in
        // the API client already deals with an ended session.
        if (event.code === 4401 || event.code === 4403) return;
        schedule();
      };

      // onerror is followed by onclose; leave the retry to one place.
      socket.onerror = () => {};
    };

    const schedule = () => {
      if (closed) return;
      const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (socket) {
        // Drop the handlers first: a close we asked for must not schedule a
        // reconnect on a component that is going away.
        socket.onclose = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.close();
      }
    };
  }, [enabled]);
}
