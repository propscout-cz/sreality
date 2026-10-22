import { describe, expect, it } from 'vitest';
import { tabFromAddress } from './PropertyTabs';

const at = (search: string, hash = '', canonical: number | null = 10) =>
  tabFromAddress(new URLSearchParams(search), hash, canonical);

describe('tabFromAddress', () => {
  it('opens the overview on a bare address', () => {
    expect(at('')).toBe('prehled');
  });

  it('lets ?tab= win over everything else', () => {
    expect(at('tab=trh&run=5', '#estimations')).toBe('trh');
    expect(at('tab=prehled&advert=11')).toBe('prehled');
  });

  it('ignores a ?tab= it does not know', () => {
    expect(at('tab=bogus')).toBe('prehled');
  });

  it('sends the estimation links to Cena a hodnota', () => {
    expect(at('run=5')).toBe('hodnota');
    expect(at('', '#estimations')).toBe('hodnota');
    expect(at('', '#feedback')).toBe('hodnota');
  });

  it('sends another advert, or a split by letters, to Inzeráty — the canonical advert stays on the overview', () => {
    expect(at('advert=11')).toBe('inzeraty');
    expect(at('letters=AB')).toBe('inzeraty');
    expect(at('advert=10')).toBe('prehled');
  });
});
