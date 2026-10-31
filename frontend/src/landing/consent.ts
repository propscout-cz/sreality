/* Marketing-cookie consent and the Meta Pixel for the landing page (frontend/landing/).
 *
 * Czech law (zákon 127/2005 Sb. § 89 odst. 3, opt-in since 2022) needs consent BEFORE a marketing tracker stores or
 * reads anything on the device, so the Pixel script is not even fetched until the visitor taps "Přijmout". With no
 * Pixel id configured there is no tracker at all: no bar, no "Nastavení cookies" link, nothing loads. The choice itself
 * is kept in localStorage (strictly necessary: it only remembers the answer). */

/** The Meta Pixel id (digits only). Empty = no Pixel, no consent bar. */
export const META_PIXEL_ID = '';

const KEY = 'ps-consent';
export type Choice = 'granted' | 'denied';

type Fbq = ((...args: unknown[]) => void) & { callMethod?: (...a: unknown[]) => void; queue: unknown[][]; loaded: boolean; version: string; push: unknown };
declare global {
  interface Window { fbq?: Fbq; _fbq?: Fbq }
}

export function storedChoice(storage: Pick<Storage, 'getItem'> = localStorage): Choice | null {
  try {
    const v = storage.getItem(KEY);
    return v === 'granted' || v === 'denied' ? v : null;
  } catch {
    return null;
  }
}

function store(choice: Choice): void {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    /* private mode: the bar simply asks again next visit */
  }
}

let loaded = false;

function loadPixel(id: string): void {
  if (loaded || !/^\d{6,20}$/.test(id)) return;
  loaded = true;
  // Meta's own bootstrap, typed: a queueing stub until fbevents.js arrives and replays the queue.
  const fbq = function (...args: unknown[]) {
    if (fbq.callMethod) fbq.callMethod(...args);
    else fbq.queue.push(args);
  } as Fbq;
  fbq.push = fbq;
  fbq.loaded = true;
  fbq.version = '2.0';
  fbq.queue = [];
  window.fbq = window._fbq = fbq;
  const s = document.createElement('script');
  s.async = true;
  s.src = 'https://connect.facebook.net/en_US/fbevents.js';
  document.head.appendChild(s);
  fbq('init', id);
  fbq('track', 'PageView');
}

/** Send a standard Pixel event; a no-op without consent or without a Pixel id. */
export function track(event: 'Lead' | 'CompleteRegistration', params?: Record<string, unknown>): void {
  if (loaded && window.fbq) window.fbq('track', event, params ?? {});
}

/** Wire the bar and the footer link; load the Pixel when consent was given earlier. */
export function initConsent(pixelId: string = META_PIXEL_ID): void {
  if (!pixelId) return;
  const bar = document.querySelector<HTMLElement>('[data-cookie-bar]');
  const settings = document.querySelector<HTMLElement>('[data-cookie-settings]');
  if (settings) settings.hidden = false;
  const choice = storedChoice();
  if (choice === 'granted') loadPixel(pixelId);
  if (!bar) return;
  if (choice === null) bar.hidden = false;
  bar.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-consent]');
    if (!btn) return;
    const picked = btn.dataset.consent === 'granted' ? 'granted' : 'denied';
    store(picked);
    bar.hidden = true;
    if (picked === 'granted') loadPixel(pixelId); // before the event: a listener may track() at once
    document.dispatchEvent(new CustomEvent('ps:consent', { detail: picked }));
    if (picked === 'denied' && loaded) location.reload(); // a withdrawn consent stops the running tracker
  });
  settings?.addEventListener('click', () => {
    bar.hidden = false;
  });
}
