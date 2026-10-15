import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readChoice, readValue, writeValue } from './persistedFlag';

describe('readValue / writeValue', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('returns the fallback until a value is written, then the value verbatim', () => {
    expect(readValue('k', 'none')).toBe('none');
    writeValue('k', 'flood_q5,metro');
    expect(readValue('k', 'none')).toBe('flood_q5,metro');
    expect(localStorage.getItem('k')).toBe('flood_q5,metro');
  });

  it('keeps an empty string as a value, distinct from unset', () => {
    writeValue('k', '');
    expect(readValue('k', 'fallback')).toBe('');
  });

  it('never throws when storage does', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => writeValue('k', 'x')).not.toThrow();
    expect(readValue('k', 'fallback')).toBe('fallback');
  });
});

describe('readChoice (now over readValue)', () => {
  beforeEach(() => localStorage.clear());

  it('accepts an allowed stored member and falls back on anything else', () => {
    expect(readChoice('size', ['sm', 'lg'], 'sm')).toBe('sm');
    localStorage.setItem('size', 'lg');
    expect(readChoice('size', ['sm', 'lg'], 'sm')).toBe('lg');
    localStorage.setItem('size', 'xxl');
    expect(readChoice('size', ['sm', 'lg'], 'sm')).toBe('sm');
  });
});
