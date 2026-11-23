import { describe, expect, it } from 'vitest';
import { errorText, followupToken, isEmail, pagePath, utmFrom } from './shared';

describe('utmFrom', () => {
  it('keeps the five utm tags only, trimmed and capped', () => {
    const long = 'x'.repeat(300);
    expect(utmFrom(`?utm_source=facebook&utm_campaign=+launch+&fbclid=abc&utm_term=${long}&v=flip`)).toEqual({
      utm_source: 'facebook',
      utm_campaign: 'launch',
      utm_term: 'x'.repeat(200),
    });
  });
  it('is empty without tags', () => {
    expect(utmFrom('')).toEqual({});
  });
});

describe('pagePath', () => {
  it('uses the pretty paths on the brand domain', () => {
    expect(pagePath('dekujeme', '/')).toBe('/dekujeme');
    expect(pagePath('soukromi', '/dekujeme')).toBe('/soukromi');
    expect(pagePath('index', '/soukromi')).toBe('/');
  });
  it('uses the files under /landing/ (the dev server)', () => {
    expect(pagePath('dekujeme', '/landing/')).toBe('/landing/dekujeme.html');
    expect(pagePath('index', '/landing/soukromi.html')).toBe('/landing/');
  });
});

describe('isEmail', () => {
  it.each(['a@b.cz', ' investor@example.com '])('accepts %s', (v) => expect(isEmail(v)).toBe(true));
  it.each(['', 'a@b', 'no-at.cz', 'a b@c.cz'])('refuses %s', (v) => expect(isEmail(v)).toBe(false));
});

describe('errorText', () => {
  it('names each API reason and falls back to the server one', () => {
    expect(errorText(null)).toBeNull();
    expect(errorText('email')).toMatch(/e-mail/);
    expect(errorText('limit')).toMatch(/minut/);
    expect(errorText('something-new')).toBe(errorText('server'));
  });
});

describe('followupToken', () => {
  it('accepts a uuid only', () => {
    expect(followupToken('?t=33333333-3333-3333-3333-33333333AAAA')).toBe('33333333-3333-3333-3333-33333333aaaa');
    expect(followupToken('?t=nope')).toBeNull();
    expect(followupToken('')).toBeNull();
  });
});
