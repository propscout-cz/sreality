/* The landing page's behaviour (frontend/landing/index.html, soukromi.html). Everything here is an enhancement: the
 * forms post to the API and work without it. */

import { initConsent } from './consent';
import { errorText, isEmail, pagePath, utmFrom } from './shared';

function setHidden(form: HTMLFormElement, name: string, value: string): void {
  let input = form.querySelector<HTMLInputElement>(`input[type="hidden"][name="${name}"]`);
  if (!input) {
    input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    form.appendChild(input);
  }
  input.value = value;
}

function showError(form: HTMLFormElement, text: string | null): void {
  const box = form.querySelector<HTMLElement>('[data-error]');
  const input = form.querySelector<HTMLInputElement>('input[name="email"]');
  if (box) {
    box.textContent = text ?? '';
    box.classList.toggle('on', Boolean(text));
  }
  if (input) {
    if (text) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  }
}

function setBusy(form: HTMLFormElement, busy: boolean): void {
  const btn = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  if (!btn) return;
  const label = btn.firstChild;
  if (busy) {
    btn.dataset.label = label?.textContent ?? '';
    if (label) label.textContent = 'Přidávám vás… ';
  } else if (btn.dataset.label && label) {
    label.textContent = btn.dataset.label;
  }
  btn.disabled = busy;
}

const forms = Array.from(document.querySelectorAll<HTMLFormElement>('form[data-signup]'));
const variant = document.documentElement.dataset.v || 'default';
const utm = utmFrom(location.search);

for (const form of forms) {
  setHidden(form, 'v', variant);
  setHidden(form, 'next', pagePath('dekujeme', location.pathname));
  for (const [key, value] of Object.entries(utm)) setHidden(form, key, value);
  const input = form.querySelector<HTMLInputElement>('input[name="email"]');
  input?.addEventListener('input', () => showError(form, null));
  form.addEventListener('submit', (event) => {
    if (!input || !isEmail(input.value)) {
      event.preventDefault();
      showError(form, errorText('email'));
      input?.focus();
      return;
    }
    setBusy(form, true);
  });
}

// Back from the thank-you page, a cached page would keep its buttons disabled.
window.addEventListener('pageshow', () => forms.forEach((f) => setBusy(f, false)));

document.querySelectorAll<HTMLAnchorElement>('a[data-page]').forEach((a) => {
  a.href = pagePath(a.dataset.page ?? '', location.pathname) + a.hash;
});

const refused = errorText(new URLSearchParams(location.search).get('chyba'));
if (refused && forms[0]) {
  showError(forms[0], refused);
  forms[0].querySelector<HTMLInputElement>('input[name="email"]')?.focus({ preventScroll: true });
}

// The phone's sticky button: shown once both forms are off screen, never over the consent bar.
const sticky = document.querySelector<HTMLElement>('[data-sticky]');
const bar = document.querySelector<HTMLElement>('[data-cookie-bar]');
if (sticky && forms.length && 'IntersectionObserver' in window) {
  const inView = new Set<Element>();
  const update = (): void => {
    const on = inView.size === 0 && window.scrollY > 320 && (!bar || bar.hidden);
    sticky.classList.toggle('on', on);
  };
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) inView.add(e.target);
      else inView.delete(e.target);
    }
    update();
  });
  forms.forEach((f) => io.observe(f));
  window.addEventListener('scroll', update, { passive: true });
  document.addEventListener('ps:consent', update);
  sticky.querySelector('a')?.addEventListener('click', (event) => {
    const target = document.getElementById('pridat');
    const email = document.getElementById('email-offer');
    if (!target || !email) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => email.focus({ preventScroll: true }), 450);
  });
}

initConsent();
