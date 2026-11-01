import { describe, expect, it } from 'vitest';
import { authErrorCs, redirectError } from './authErrors';

const REFUSED = 'Tato adresa zatím nemá pozvánku. Přidejte se na čekací listinu na propscout.cz.';

describe('authErrorCs', () => {
  it('words what it knows in Czech', () => {
    expect(authErrorCs({ code: 'invalid_credentials', message: 'Invalid login credentials' })).toBe('Nesprávný e-mail nebo heslo.');
    expect(authErrorCs({ code: 'signup_disabled', message: 'Signups not allowed for this instance' })).toBe(REFUSED);
  });

  it('passes the invitation refusal through whatever its code', () => {
    expect(authErrorCs({ code: 'hook_error', message: REFUSED })).toBe(REFUSED);
  });

  it('never shows an unknown English message', () => {
    expect(authErrorCs({ code: 'unexpected', message: 'Database error saving new user' })).toBe('Nepovedlo se to. Zkuste to prosím znovu.');
    expect(authErrorCs(undefined)).toBe('Nepovedlo se to. Zkuste to prosím znovu.');
  });
});

describe('redirectError', () => {
  it('finds the reason in a query or a hash', () => {
    const encoded = encodeURIComponent(REFUSED);
    expect(redirectError(`?error=access_denied&error_description=${encoded}`)).toBe(REFUSED);
    expect(redirectError('', undefined, `#error=server_error&error_description=${encoded}`)).toBe(REFUSED);
  });

  it('is silent on an ordinary address', () => {
    expect(redirectError('?deal=prodej', '#estimations', undefined)).toBeNull();
  });
});
