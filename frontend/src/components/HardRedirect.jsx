import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

/**
 * Leave this bundle for the other one.
 *
 * The tasker app and the main app are separate bundles (see main.jsx), so
 * moving between them is a real page load, not a client-side route change:
 * neither app has the other's screens to render.
 */
export default function HardRedirect() {
  const location = useLocation();
  useEffect(() => {
    window.location.replace(location.pathname + location.search);
  }, [location.pathname, location.search]);
  return null;
}
