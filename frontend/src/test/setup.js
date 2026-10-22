import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
// The worker review screens' default endpoints, as the main app registers them.
import '../worker/workerApi.js';

// Every test starts from a signed-out browser with no network. A test that
// needs either has to say so, which keeps an accidental real fetch from
// quietly passing.
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
