/* The thank-you page (frontend/landing/dekujeme.html): the optional question, the Pixel's Lead and sharing. */

import { initConsent, track } from './consent';
import { followupToken, pagePath } from './shared';

const SHARE_URL = 'https://propscout.cz/?utm_source=share&utm_medium=referral';
const SHARE_TEXT = 'Hlídací pes na 9 realitních portálech najednou, rovnou v mobilu. Mají čekací listinu:';

const query = new URLSearchParams(location.search);
const token = followupToken(location.search);
const answered = query.get('hotovo') === '1';

const card = document.querySelector<HTMLElement>('[data-question]');
const form = document.querySelector<HTMLFormElement>('[data-question] form');
const done = document.querySelector<HTMLElement>('[data-done]');

if (form) {
  const next = form.querySelector<HTMLInputElement>('input[name="next"]');
  if (next) next.value = pagePath('dekujeme', location.pathname);
  const t = form.querySelector<HTMLInputElement>('input[name="t"]');
  if (t && token) t.value = token;
  form.addEventListener('submit', () => {
    const btn = form.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (btn) btn.disabled = true;
  });
}
if (answered) {
  if (form) form.hidden = true;
  done?.classList.add('on');
} else if (!token && card) {
  // Without a token (a repeat signup, or a direct visit) there is no row to attach an answer to.
  card.hidden = true;
}

document.querySelectorAll<HTMLAnchorElement>('a[data-page]').forEach((a) => {
  a.href = pagePath(a.dataset.page ?? '', location.pathname) + a.hash;
});

// One Lead per new signup: only a fresh token counts, and only once, whenever consent arrives.
function fireLead(): void {
  if (!token) return;
  const key = `ps-lead-${token}`;
  try {
    if (localStorage.getItem(key)) return;
    track('Lead');
    localStorage.setItem(key, '1');
  } catch {
    track('Lead');
  }
}

initConsent();
fireLead();
document.addEventListener('ps:consent', (event) => {
  if ((event as CustomEvent<string>).detail === 'granted') fireLead();
});

const shareBtn = document.querySelector<HTMLButtonElement>('[data-share]');
const shareLabel = shareBtn?.querySelector('span');
shareBtn?.addEventListener('click', async () => {
  if (navigator.share) {
    try {
      await navigator.share({ title: 'PropScout.cz', text: SHARE_TEXT, url: SHARE_URL });
    } catch {
      /* the visitor closed the share sheet */
    }
    return;
  }
  try {
    await navigator.clipboard.writeText(SHARE_URL);
    if (shareLabel) shareLabel.textContent = 'Odkaz zkopírován';
  } catch {
    window.prompt('Zkopírujte si odkaz:', SHARE_URL);
  }
});
const wa = document.querySelector<HTMLAnchorElement>('[data-whatsapp]');
if (wa) wa.href = `https://wa.me/?text=${encodeURIComponent(`${SHARE_TEXT} ${SHARE_URL}`)}`;
