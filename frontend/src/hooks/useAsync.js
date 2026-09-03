import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Load-once-and-refresh, with the three states every screen actually needs:
 * loading, error, data.
 *
 * WHY NOT JUST useEffect + useState EVERYWHERE. Each screen that rolls its
 * own gets one of these wrong -- usually the unmount case, where a slow
 * response calls setState on a gone component, or the refresh case, where a
 * background reload blanks the screen back to a skeleton. Both are handled
 * once here.
 *
 * `silent` refreshes leave the current data on screen while new data is
 * fetched, which is what a polling list wants; a visible spinner every few
 * seconds reads as instability.
 */
export function useAsync(fn, deps = [], { immediate = true } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(immediate);
  const alive = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const run = useCallback(async ({ silent = false } = {}) => {
    const id = ++requestId.current;
    if (!silent) setLoading(true);
    try {
      const result = await fn();
      // A slower earlier request must not overwrite a newer result.
      if (!alive.current || id !== requestId.current) return null;
      setData(result);
      setError(null);
      return result;
    } catch (err) {
      if (!alive.current || id !== requestId.current) return null;
      // A silent background failure keeps the last good data on screen and
      // surfaces the error beside it, rather than replacing work in view
      // with an error page.
      setError(err);
      return null;
    } finally {
      if (alive.current && id === requestId.current) setLoading(false);
    }
    // fn is intentionally excluded: callers pass an inline closure, and the
    // deps they declare are the real dependency list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (immediate) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, immediate]);

  return { data, error, loading, refresh: run, setData };
}

/**
 * Refresh on an interval, but only while the tab is visible.
 *
 * A background tab polling every ten seconds spends the day making requests
 * nobody reads. Pausing on hide also means the first thing a returning user
 * sees is fresh, because we refresh immediately on becoming visible again.
 */
export function usePolling(refresh, intervalMs = 15000, enabled = true) {
  useEffect(() => {
    if (!enabled || !intervalMs) return undefined;

    let timer = null;
    const tick = () => refresh({ silent: true });

    const start = () => {
      if (timer) return;
      timer = setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') { tick(); start(); }
      else stop();
    };

    if (document.visibilityState === 'visible') start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [refresh, intervalMs, enabled]);
}
