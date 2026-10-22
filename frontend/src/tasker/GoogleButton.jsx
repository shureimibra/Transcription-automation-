import React, { useEffect, useRef, useState } from 'react';
import { auth as authApi } from '../api/auth.js';
import { useAuth } from '../auth/AuthContext.jsx';

const GIS_SRC = 'https://accounts.google.com/gsi/client';

/** Google's own button script, loaded once and only on the pages that need it. */
function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!loadGoogleScript.pending) {
    loadGoogleScript.pending = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = GIS_SRC;
      script.async = true;
      script.onload = resolve;
      script.onerror = () => { loadGoogleScript.pending = null; reject(new Error('blocked')); };
      document.head.appendChild(script);
    });
  }
  return loadGoogleScript.pending;
}

/**
 * "Continue with Google".
 *
 * GOOGLE'S OWN BUTTON, NOT A LOOK-ALIKE. Google renders it and hands back a
 * signed ID token; the server verifies the signature before trusting it
 * (backend tasker_auth.GoogleSignInView). Nothing about the person's Google
 * account -- no password, no access to their mail -- ever reaches us.
 *
 * Renders nothing when the server has no Google client id configured, so
 * the sign-in page works the same with or without it.
 */
export default function GoogleButton({ clientId, onError }) {
  const { adopt } = useAuth();
  const box = useRef(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!clientId) return undefined;
    let cancelled = false;
    loadGoogleScript().then(() => {
      if (cancelled || !box.current) return;
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async ({ credential }) => {
          try {
            adopt(await authApi.google(credential));
          } catch (err) {
            onError?.(err.message);
          }
        },
      });
      window.google.accounts.id.renderButton(box.current, {
        theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular',
        width: box.current.offsetWidth || 320,
      });
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [clientId, adopt, onError]);

  if (!clientId) return null;
  if (failed) {
    return <p className="text-2xs text-ink-mute">Google sign-in could not load. Use your username instead.</p>;
  }
  return <div ref={box} className="flex min-h-[44px] w-full justify-center" />;
}
