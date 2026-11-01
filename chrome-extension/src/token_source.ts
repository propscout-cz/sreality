/* The access token for the next API call, refreshed first when it is near expiry. Pure: the auth
 * server, the clock and the storage come in as `TokenDeps` (auth.ts wires the real ones), so the
 * rules below are tested without Chrome.
 *
 * Only a refresh the auth server REFUSES signs the user out. Supabase rotates refresh tokens with
 * reuse detection, so a refused token is dead for good; anything else (no answer, a timeout, a 5xx,
 * a 429, another 4xx from something in between, a 200 that carries no token) says nothing about the
 * session, which is kept, and the call reports a retryable error. */

import type { StoredSession } from './session.ts';

/* The auth server's answer to a refresh; `refresh` resolves null when none came (network, timeout). */
export interface RefreshReply {
  status: number;
  body: unknown;
}

export interface TokenDeps {
  load(): Promise<StoredSession | null>;
  save(session: StoredSession): Promise<void>;
  clear(): Promise<void>;
  /* Epoch seconds. */
  now(): number;
  refresh(refreshToken: string): Promise<RefreshReply | null>;
}

export type AccessToken =
  | { kind: 'token'; token: string }
  | { kind: 'signed_out' }
  | { kind: 'retry'; detail: string };

export const REFRESH_RETRY_DETAIL = 'Přihlášení se teď nepodařilo obnovit (výpadek spojení), zkuste to znovu.';

/* Refresh a little before actual expiry so a request never races a token that is valid on read but
 * expired on arrival at the API. */
const EXPIRY_SKEW_SECONDS = 60;

/* GoTrue's token answer (the PKCE exchange and a refresh alike). */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  expires_at?: number;
}

export function isTokenResponse(body: unknown): body is TokenResponse {
  const b = body as Partial<TokenResponse> | null;
  return b != null && typeof b === 'object' && typeof b.access_token === 'string'
    && typeof b.refresh_token === 'string' && typeof b.expires_in === 'number';
}

export function toStoredSession(t: TokenResponse, now: number): StoredSession {
  return { access_token: t.access_token, refresh_token: t.refresh_token, expires_at: t.expires_at ?? now + t.expires_in };
}

/* GoTrue refused the refresh token itself: its 400, or a body naming a refresh-token or session
 * error. Any other 4xx comes from something in between (a proxy, a firewall) and proves nothing. */
function refused(reply: RefreshReply): boolean {
  if (reply.status === 400) return true;
  const code = (reply.body as { error_code?: unknown } | null)?.error_code;
  return reply.status >= 400 && reply.status < 500 && typeof code === 'string'
    && /^(refresh_token|session)_/.test(code);
}

export function createTokenSource(deps: TokenDeps): { accessToken(): Promise<AccessToken> } {
  const fresh = (s: StoredSession): boolean => s.expires_at - deps.now() > EXPIRY_SKEW_SECONDS;

  /* At most one refresh in flight: two refreshes on the same token would trip the reuse detection
   * and revoke the session. The flight re-reads the session first, so a caller that read it before
   * another refresh landed takes that refresh's token instead of refreshing again. */
  let inFlight: Promise<AccessToken> | null = null;

  async function refreshOnce(): Promise<AccessToken> {
    const s = await deps.load();
    if (s == null) return { kind: 'signed_out' };
    if (fresh(s)) return { kind: 'token', token: s.access_token };
    const reply = await deps.refresh(s.refresh_token);
    if (reply != null && refused(reply)) {
      await deps.clear();
      return { kind: 'signed_out' };
    }
    if (reply == null || !isTokenResponse(reply.body)) return { kind: 'retry', detail: REFRESH_RETRY_DETAIL };
    await deps.save(toStoredSession(reply.body, deps.now()));
    return { kind: 'token', token: reply.body.access_token };
  }

  return {
    async accessToken(): Promise<AccessToken> {
      const s = await deps.load();
      if (s == null) return { kind: 'signed_out' };
      if (fresh(s)) return { kind: 'token', token: s.access_token };
      if (inFlight == null) inFlight = refreshOnce().finally(() => { inFlight = null; });
      return inFlight;
    },
  };
}
