/* registerServiceWorker: production only, the worker at the root scope, and
 * the hourly update check on resume that reaches an installed app the browser's
 * own navigation-time check never sees. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { registerServiceWorker } from './serviceWorker';

const update = vi.fn();
const register = vi.fn();
/* document outlives each case; drop the listeners a case's registration added. */
let added: Array<[string, EventListenerOrEventListenerObject]> = [];
const addEventListener = document.addEventListener.bind(document);

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  update.mockResolvedValue(undefined);
  register.mockResolvedValue({ update });
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { register } });
  Object.defineProperty(document, 'readyState', { configurable: true, value: 'complete' });
  vi.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
    added.push([type, listener]);
    addEventListener(type, listener, options);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  for (const [type, listener] of added) document.removeEventListener(type, listener);
  added = [];
  update.mockReset();
  register.mockReset();
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: undefined });
});

describe('registerServiceWorker', () => {
  it('does nothing outside a production build', () => {
    vi.stubEnv('PROD', false);
    registerServiceWorker();
    expect(register).not.toHaveBeenCalled();
  });

  it('registers /sw.js at the root scope', async () => {
    vi.stubEnv('PROD', true);
    registerServiceWorker();
    expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/' });
  });

  it('checks for a new worker on resume, at most once an hour', async () => {
    vi.stubEnv('PROD', true);
    registerServiceWorker();
    await vi.advanceTimersByTimeAsync(0);

    setVisibility('visible');
    expect(update).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(60 * 60_000);
    setVisibility('hidden');
    expect(update).not.toHaveBeenCalled();
    setVisibility('visible');
    expect(update).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30 * 60_000);
    setVisibility('visible');
    expect(update).toHaveBeenCalledTimes(1);
  });
});
