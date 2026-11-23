/* The admin Sign-ups page's data: GET /admin/waitlist (api/routes/waitlist.py), one row per e-mail on the
 * propscout.cz waitlist (migration 700). Types and pure helpers; the page is pages/Signups.tsx. */

export type InvestorType = 'flip' | 'rent' | 'both' | 'starting';

export interface WaitlistSignup {
  id: number;
  email: string;
  created_at: string;
  consent_version: string;
  page_variant: string | null;
  utm: Record<string, string>;
  investor_type: InvestorType | null;
  note: string | null;
  answered_at: string | null;
  unsubscribed_at: string | null;
  invited_at: string | null;
}

export interface WaitlistResponse {
  data: WaitlistSignup[];
  truncated: boolean;
}

/* The thank-you page's answers, in the words it offered them (frontend/landing/dekujeme.html). */
export const INVESTOR_LABEL: Record<InvestorType, string> = {
  flip: 'Flip',
  rent: 'Pronájem',
  both: 'Obojí',
  starting: 'Teprve začíná',
};

/* A sign-up made to test the form (the go-live check used utm_source=claude-test): listed, never counted. */
export const isTest = (s: WaitlistSignup): boolean => /test/i.test(s.utm?.utm_source ?? '');

export interface SignupSummary {
  total: number;
  last7: number;
  answered: number;
  byType: Record<InvestorType | 'none', number>;
  tests: number;
}

const DAY_MS = 86_400_000;

export function summarize(rows: WaitlistSignup[], now: number = Date.now()): SignupSummary {
  const byType: SignupSummary['byType'] = { flip: 0, rent: 0, both: 0, starting: 0, none: 0 };
  let total = 0;
  let last7 = 0;
  let answered = 0;
  let tests = 0;
  for (const r of rows) {
    if (isTest(r)) {
      tests++;
      continue;
    }
    total++;
    if (now - new Date(r.created_at).getTime() < 7 * DAY_MS) last7++;
    if (r.answered_at) answered++;
    byType[r.investor_type ?? 'none']++;
  }
  return { total, last7, answered, byType, tests };
}

/* Where a sign-up came from, in one line: the ad's source, campaign and content, then the headline it saw. */
export function cameFrom(s: WaitlistSignup): string {
  const parts = [s.utm?.utm_source, s.utm?.utm_campaign, s.utm?.utm_content].filter(Boolean);
  const ad = parts.length ? parts.join(' · ') : 'direct';
  return s.page_variant && s.page_variant !== 'default' ? `${ad} (${s.page_variant})` : ad;
}

export interface SourceRow {
  source: string;
  campaign: string;
  variant: string;
  count: number;
}

/* Sign-ups per ad source × campaign × headline variant, most first; tests excluded. */
export function bySource(rows: WaitlistSignup[]): SourceRow[] {
  const m = new Map<string, SourceRow>();
  for (const r of rows) {
    if (isTest(r)) continue;
    const source = r.utm?.utm_source || 'direct';
    const campaign = r.utm?.utm_campaign || '—';
    const variant = r.page_variant || 'default';
    const key = `${source}\u0000${campaign}\u0000${variant}`;
    const row = m.get(key) ?? { source, campaign, variant, count: 0 };
    row.count++;
    m.set(key, row);
  }
  return [...m.values()].sort((a, b) => b.count - a.count || a.source.localeCompare(b.source)
    || a.campaign.localeCompare(b.campaign) || a.variant.localeCompare(b.variant));
}

const CSV_COLUMNS = ['created_at', 'email', 'investor_type', 'note', 'page_variant', 'utm_source', 'utm_medium',
  'utm_campaign', 'utm_content', 'utm_term', 'consent_version', 'invited_at', 'unsubscribed_at', 'test'] as const;

function csvCell(v: unknown): string {
  const s = v == null ? '' : String(v);
  // A leading = + - @ would run as a formula in a spreadsheet: prefix an apostrophe.
  const safe = /^[=+\-@]/.test(s) ? `'${s}` : s;
  return /[",\n\r;]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function toCsv(rows: WaitlistSignup[]): string {
  const lines = [CSV_COLUMNS.join(',')];
  for (const r of rows) {
    const cells: unknown[] = [r.created_at, r.email, r.investor_type, r.note, r.page_variant, r.utm?.utm_source,
      r.utm?.utm_medium, r.utm?.utm_campaign, r.utm?.utm_content, r.utm?.utm_term, r.consent_version, r.invited_at,
      r.unsubscribed_at, isTest(r) ? 'yes' : ''];
    lines.push(cells.map(csvCell).join(','));
  }
  return lines.join('\r\n') + '\r\n';
}
