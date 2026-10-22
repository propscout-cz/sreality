/* The Browse view an operator last left, remembered so the two routes back that
 * do not step through history (the nav's Browse entry, the property crumb on a
 * page opened in a new tab) land on it instead of a bare, unfiltered /browse. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import {
  lastBrowsePath,
  readLastBrowseSearch,
  useRememberBrowseView,
  writeLastBrowseSearch,
} from './lastBrowseView';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('the remembered Browse search', () => {
  it('reads as the bare view until something is written', () => {
    expect(readLastBrowseSearch()).toBe('');
    expect(lastBrowsePath()).toBe('/browse');
  });

  it('round-trips the query string and builds the path from it', () => {
    writeLastBrowseSearch('?price=3000000-6000000&sort=price_asc');
    expect(readLastBrowseSearch()).toBe('?price=3000000-6000000&sort=price_asc');
    expect(lastBrowsePath()).toBe('/browse?price=3000000-6000000&sort=price_asc');
  });

  it('never reads a foreign value as a destination', () => {
    writeLastBrowseSearch('https://elsewhere.example/?x=1');
    expect(readLastBrowseSearch()).toBe('');
    expect(lastBrowsePath()).toBe('/browse');
  });

  it('reads as the bare view when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(lastBrowsePath()).toBe('/browse');
  });
});

let go: (to: string) => void = () => {};
function Probe() {
  useRememberBrowseView();
  go = useNavigate();
  return null;
}

describe('useRememberBrowseView', () => {
  it('writes the URL the page shows, and follows it', () => {
    render(
      <MemoryRouter initialEntries={['/browse?price=3000000-6000000']}>
        <Probe />
      </MemoryRouter>,
    );
    expect(readLastBrowseSearch()).toBe('?price=3000000-6000000');
    act(() => go('/browse?price=3000000-6000000&area=50-80'));
    expect(readLastBrowseSearch()).toBe('?price=3000000-6000000&area=50-80');
  });

  it('remembers a reset too, so the bare page is what comes back', () => {
    writeLastBrowseSearch('?price=3000000-6000000');
    render(
      <MemoryRouter initialEntries={['/browse']}>
        <Probe />
      </MemoryRouter>,
    );
    expect(readLastBrowseSearch()).toBe('');
  });
});
