/* Pure helpers the landing page's scripts share (main.ts, thanks.ts); tested in shared.test.ts. */

export const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'] as const;

/** The ad's utm_* tags from a query string, trimmed to what the API keeps (200 characters). */
export function utmFrom(search: string): Record<string, string> {
  const q = new URLSearchParams(search);
  const out: Record<string, string> = {};
  for (const key of UTM_KEYS) {
    const value = q.get(key)?.trim();
    if (value) out[key] = value.slice(0, 200);
  }
  return out;
}

/** A landing page's address. On the brand domain the pages are /, /dekujeme and /soukromi; in the Vite dev server
 * (and any host that serves the build's /landing/ folder) they are the files themselves. */
export function pagePath(page: string, pathname: string): string {
  const name = page === 'index' ? '' : page;
  if (pathname.startsWith('/landing/')) return name ? `/landing/${name}.html` : '/landing/';
  return `/${name}`;
}

/** Loose on purpose: the API decides (api/waitlist.py normalize_email); this only catches typos before a round trip. */
export function isEmail(value: string): boolean {
  const v = value.trim();
  return v.length >= 3 && v.length <= 254 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v);
}

/** The API's refusal reasons (api/routes/waitlist.py `fail(...)`), in the reader's words. */
export const ERRORS: Record<string, string> = {
  email: 'Tenhle e-mail nám nesedí. Zkontrolujte ho prosím, třeba chybí tečka nebo zavináč.',
  limit: 'Z tohoto připojení teď přišlo hodně přihlášek. Zkuste to prosím za pár minut.',
  obnovit: 'Stránka mezitím zastarala. Obnovte ji prosím a zkuste to znovu.',
  server: 'Něco se u nás pokazilo a e-mail se neuložil. Zkuste to prosím za chvíli znovu.',
};

export function errorText(code: string | null): string | null {
  if (!code) return null;
  return ERRORS[code] ?? ERRORS.server;
}

const TOKEN_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function followupToken(search: string): string | null {
  const t = new URLSearchParams(search).get('t');
  return t && TOKEN_RE.test(t) ? t.toLowerCase() : null;
}
