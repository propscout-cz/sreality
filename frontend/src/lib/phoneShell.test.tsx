/* The shell switch (#1766): the phone frame is on when the screen is narrow AND
 * this device has not asked for the desktop view; the override survives storage
 * failures by meaning "no override". */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DESKTOP_VIEW_KEY,
  PHONE_SHELL_QUERY,
  isPhoneRoot,
  phoneTabFor,
  readDesktopView,
  usePhoneShell,
  writeDesktopView,
} from './phoneShell';

type Listener = () => void;

function mockWidth(initiallyNarrow: boolean) {
  let narrow = initiallyNarrow;
  const listeners = new Set<Listener>();
  const original = window.matchMedia;
  window.matchMedia = vi.fn((query: string) => ({
    get matches() {
      return query === PHONE_SHELL_QUERY ? narrow : false;
    },
    media: query,
    onchange: null,
    addEventListener: (_: string, l: Listener) => listeners.add(l),
    removeEventListener: (_: string, l: Listener) => listeners.delete(l),
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return {
    resize(nextNarrow: boolean) {
      narrow = nextNarrow;
      listeners.forEach((l) => l());
    },
    restore() {
      window.matchMedia = original;
    },
  };
}

let width: ReturnType<typeof mockWidth>;

beforeEach(() => localStorage.clear());
afterEach(() => {
  width?.restore();
  vi.restoreAllMocks();
});

describe('usePhoneShell', () => {
  it('is on for a narrow screen', () => {
    width = mockWidth(true);
    expect(renderHook(() => usePhoneShell()).result.current).toBe(true);
  });

  it('is off for a wide screen', () => {
    width = mockWidth(false);
    expect(renderHook(() => usePhoneShell()).result.current).toBe(false);
  });

  it('follows the width live, across the breakpoint', () => {
    width = mockWidth(false);
    const { result } = renderHook(() => usePhoneShell());
    expect(result.current).toBe(false);
    act(() => width.resize(true));
    expect(result.current).toBe(true);
    act(() => width.resize(false));
    expect(result.current).toBe(false);
  });

  it('stays off on a narrow screen when this device asked for the desktop view', () => {
    width = mockWidth(true);
    writeDesktopView(true);
    expect(localStorage.getItem(DESKTOP_VIEW_KEY)).toBe('1');
    expect(renderHook(() => usePhoneShell()).result.current).toBe(false);
  });

  it('comes back once the override is cleared', () => {
    width = mockWidth(true);
    writeDesktopView(true);
    writeDesktopView(false);
    expect(localStorage.getItem(DESKTOP_VIEW_KEY)).toBeNull();
    expect(renderHook(() => usePhoneShell()).result.current).toBe(true);
  });

  it('reads a throwing storage as "no override"', () => {
    width = mockWidth(true);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(readDesktopView()).toBe(false);
    expect(renderHook(() => usePhoneShell()).result.current).toBe(true);
  });
});

describe('phone tabs', () => {
  it('gives each path its area', () => {
    expect(phoneTabFor('/feed')?.key).toBe('feed');
    expect(phoneTabFor('/pipeline')?.key).toBe('pipeline');
    expect(phoneTabFor('/collections')?.key).toBe('collections');
    expect(phoneTabFor('/collection/12')?.key).toBe('collections');
    expect(phoneTabFor('/brokers/7')?.key).toBe('brokers');
    expect(phoneTabFor('/property/304388')).toBeNull();
    expect(phoneTabFor('/feedback')).toBeNull();
  });

  it('draws no back affordance on a tab root only', () => {
    expect(isPhoneRoot('/feed')).toBe(true);
    expect(isPhoneRoot('/brokers')).toBe(true);
    expect(isPhoneRoot('/brokers/7')).toBe(false);
    expect(isPhoneRoot('/property/1')).toBe(false);
  });
});
