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

  // Follow the page's light/dark theme: Google draws its own button, so
  // it is told which of its two looks to use, and redrawn when it changes.
  const [dark, setDark] = useState(
    () => document.documentElement.getAttribute('data-theme') === 'dark');
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(
      document.documentElement.getAttribute('data-theme') === 'dark'));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

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
      box.current.innerHTML = '';
      window.google.accounts.id.renderButton(box.current, {
        type: 'standard',
        theme: dark ? 'filled_black' : 'outline',
        size: 'large',
        text: 'continue_with',
        shape: 'pill',
        logo_alignment: 'left',
        // Google caps the width at 400; the form is narrower than that.
        width: Math.min(400, box.current.offsetWidth || 320),
      });
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [clientId, adopt, onError, dark]);

  if (!clientId) return null;
  if (failed) {
    return <p className="text-2xs text-ink-mute">Google sign-in could not load. Use your username instead.</p>;
  }
  // COLOR-SCHEME: LIGHT IS THE FIX FOR A WHITE STRIP. Google's button is an
  // iframe. On a dark page the browser paints an iframe whose colour scheme
  // differs from its parent's with an opaque white background -- the white
  // edge that showed under the pill. Declaring this box light makes the
  // iframe's background transparent again, so only Google's own pill shows.
  return (
    <div ref={box} style={{ colorScheme: 'light' }}
         className="flex min-h-[44px] w-full items-center justify-center" />
  );
}
