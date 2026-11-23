import { describe, expect, it } from 'vitest';
import { bySource, cameFrom, isTest, summarize, toCsv, type WaitlistSignup } from './waitlist';

const NOW = Date.parse('2026-11-10T12:00:00Z');

function row(over: Partial<WaitlistSignup> = {}): WaitlistSignup {
  return {
    id: 1, email: 'a@example.cz', created_at: '2026-11-09T10:00:00Z', consent_version: '2026-10-31', page_variant: 'default',
    utm: {}, investor_type: null, note: null, answered_at: null, unsubscribed_at: null, invited_at: null, ...over,
  };
}

describe('summarize', () => {
  it('counts real sign-ups, the last 7 days, answers and investor types, and sets tests apart', () => {
    const s = summarize([
      row({ investor_type: 'flip', answered_at: '2026-11-09T10:01:00Z' }),
      row({ created_at: '2026-10-20T10:00:00Z', investor_type: 'rent', answered_at: '2026-10-20T10:01:00Z' }),
      row(),
      row({ utm: { utm_source: 'claude-test' }, investor_type: 'flip' }),
    ], NOW);
    expect(s).toEqual({ total: 3, last7: 2, answered: 2, tests: 1,
      byType: { flip: 1, rent: 1, both: 0, starting: 0, none: 1 } });
  });
});

describe('cameFrom', () => {
  it('names the ad and the headline variant', () => {
    expect(cameFrom(row({ utm: { utm_source: 'facebook', utm_campaign: 'launch', utm_content: 'flip-1' }, page_variant: 'flip' })))
      .toBe('facebook · launch · flip-1 (flip)');
    expect(cameFrom(row())).toBe('direct');
  });
});

describe('bySource', () => {
  it('groups by source, campaign and variant, most first, without tests', () => {
    const rows = [
      row({ utm: { utm_source: 'facebook', utm_campaign: 'a' } }),
      row({ utm: { utm_source: 'facebook', utm_campaign: 'a' } }),
      row(),
      row({ utm: { utm_source: 'test' } }),
    ];
    expect(bySource(rows)).toEqual([
      { source: 'facebook', campaign: 'a', variant: 'default', count: 2 },
      { source: 'direct', campaign: '—', variant: 'default', count: 1 },
    ]);
  });
});

describe('toCsv', () => {
  it('quotes what needs quoting and defuses spreadsheet formulas', () => {
    const csv = toCsv([row({ note: 'Brno, "do 4 M"', email: '=cmd@example.cz' })]);
    const [head, line] = csv.trim().split('\r\n');
    expect(head.split(',')[1]).toBe('email');
    expect(line).toContain('"Brno, ""do 4 M"""');
    expect(line).toContain("'=cmd@example.cz");
  });
});

describe('isTest', () => {
  it('marks a test source', () => {
    expect(isTest(row({ utm: { utm_source: 'claude-test' } }))).toBe(true);
    expect(isTest(row({ utm: { utm_source: 'facebook' } }))).toBe(false);
  });
});
