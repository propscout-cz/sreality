/* The delegated stamp's one rule (migration 641, ruling 5; lib/delegatedStamp). */

import { describe, expect, it } from 'vitest';

import { delegatedStamp, delegatedStampTitle, stampNeedsParties } from './delegatedStamp';
import { fmtAbsolute } from './format';

const AT = '2026-10-20T10:00:00Z';
const T = fmtAbsolute(AT);

describe('delegatedStamp', () => {
  it('says nothing without a delegated edit', () => {
    expect(delegatedStamp({})).toBeNull();
    expect(delegatedStamp({ edited_by_email: 'c@x.cz', edited_at: null })).toBeNull();
  });

  it('names the API projection’s email, in the surface’s language', () => {
    const src = { edited_by_email: 'c@x.cz', edited_at: AT };
    expect(delegatedStamp(src)).toBe(`upravil c@x.cz · ${T}`);
    expect(delegatedStamp(src, { lang: 'en' })).toBe(`edited by c@x.cz · ${T}`);
  });

  it('names a board row’s account only from the parties it is given', () => {
    const src = { edited_by_account_id: 'acc-c', edited_at: AT };
    expect(delegatedStamp(src, { parties: new Map([['acc-c', 'c@x.cz']]) })).toBe(`upravil c@x.cz · ${T}`);
    // A delegate on a shared board (no parties) or a former delegate: the time alone.
    expect(delegatedStamp(src)).toBe(`upraveno · ${T}`);
    expect(delegatedStamp(src, { parties: new Map([['acc-d', 'd@x.cz']]) })).toBe(`upraveno · ${T}`);
    expect(delegatedStamp(src, { lang: 'en' })).toBe(`edited · ${T}`);
  });

  it('asks for the share lists only for a view row that names no email', () => {
    expect(stampNeedsParties({ edited_by_account_id: 'a', edited_at: AT })).toBe(true);
    expect(stampNeedsParties({ edited_by_account_id: 'a', edited_by_email: 'c@x.cz', edited_at: AT })).toBe(false);
    expect(stampNeedsParties({ edited_by_account_id: 'a', edited_at: null })).toBe(false);
    expect(stampNeedsParties({ edited_at: AT })).toBe(false);
  });

  it('titles the line in the same language, from the same module', () => {
    expect(delegatedStampTitle({ edited_at: AT })).toBe(`Naposledy změněno přes sdílení pipeline, ${T}`);
    expect(delegatedStampTitle({ edited_at: AT }, 'en')).toBe(`Last changed through a pipeline share, ${T}`);
    expect(delegatedStampTitle({})).toBeNull();
  });
});
