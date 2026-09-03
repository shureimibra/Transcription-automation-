import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, setToken, setUnauthorizedHandler } from '../api/client.js';

// The contract this file pins down: the client never invents a message the
// server did not send, and never leaks one it should not have.

function jsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => 'application/json' },
    json: async () => body,
  };
}

describe('api client', () => {
  beforeEach(() => {
    setUnauthorizedHandler(null);
    setToken(null);
  });

  it('attaches the token when there is one', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    setToken('tok-1');

    await api.get('/workforce/tasks/');

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe('Token tok-1');
  });

  it('sends no Authorization header when signed out', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    vi.stubGlobal('fetch', fetchMock);

    await api.get('/workforce/me/');

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("surfaces the server's own error text rather than rewriting it", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(409, { error: 'Someone else is already working on this task.' })
    ));

    await expect(api.post('/workforce/tasks/x/claim/')).rejects.toThrow(
      'Someone else is already working on this task.'
    );
  });

  it('marks a 409 as a conflict so callers can treat it differently', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, { error: 'taken' })));

    const error = await api.post('/workforce/tasks/x/claim/').catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.isConflict).toBe(true);
    expect(error.isAuth).toBe(false);
  });

  it('falls back to a plain sentence when the server sends no message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(500, {})));

    const error = await api.get('/anything').catch((e) => e);
    expect(error.message).toBe('The server ran into a problem. Please try again.');
    // No status code, no internals.
    expect(error.message).not.toMatch(/500|traceback|exception/i);
  });

  it('clears the token and notifies once on a 401', async () => {
    setToken('tok-1');
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'bad token' })));

    await expect(api.get('/workforce/me/')).rejects.toThrow(/session has ended/i);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('wf.token')).toBeNull();
  });

  it('reports a network failure as unreachable, not as a server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const error = await api.get('/workforce/me/').catch((e) => e);
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/could not reach the server/i);
  });

  it('returns null for a 204 rather than trying to parse a body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 204, ok: true, headers: { get: () => null },
      json: async () => { throw new Error('should not be called'); },
    }));

    await expect(api.post('/workforce/auth/logout/')).resolves.toBeNull();
  });
});
