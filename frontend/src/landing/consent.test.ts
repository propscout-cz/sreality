import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { initConsent, storedChoice } from './consent';

const BAR = `
  <button type="button" data-cookie-settings hidden>Nastavení cookies</button>
  <div data-cookie-bar hidden><button data-consent="denied">Odmítnout</button><button data-consent="granted">Přijmout</button></div>`;

const pixelScripts = (): number => document.querySelectorAll('script[src*="connect.facebook.net"]').length;

describe('consent', () => {
  beforeEach(() => {
    document.body.innerHTML = BAR;
    localStorage.clear();
  });
  afterEach(() => {
    document.head.querySelectorAll('script').forEach((s) => s.remove());
    delete window.fbq;
  });

  it('shows nothing and loads nothing without a Pixel id', () => {
    initConsent('');
    expect(document.querySelector<HTMLElement>('[data-cookie-bar]')!.hidden).toBe(true);
    expect(document.querySelector<HTMLElement>('[data-cookie-settings]')!.hidden).toBe(true);
    expect(pixelScripts()).toBe(0);
  });

  it('asks first and loads the Pixel only after Přijmout', () => {
    initConsent('1234567890');
    const bar = document.querySelector<HTMLElement>('[data-cookie-bar]')!;
    expect(bar.hidden).toBe(false);
    expect(pixelScripts()).toBe(0);
    bar.querySelector<HTMLButtonElement>('[data-consent="granted"]')!.click();
    expect(bar.hidden).toBe(true);
    expect(storedChoice()).toBe('granted');
    expect(pixelScripts()).toBe(1);
  });

  it('remembers Odmítnout and never loads the Pixel', () => {
    initConsent('1234567890');
    document.querySelector<HTMLButtonElement>('[data-consent="denied"]')!.click();
    expect(storedChoice()).toBe('denied');
    expect(pixelScripts()).toBe(0);
  });

  it('reads a stored choice back', () => {
    expect(storedChoice({ getItem: () => 'granted' })).toBe('granted');
    expect(storedChoice({ getItem: () => 'maybe' })).toBeNull();
  });
});
