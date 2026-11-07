/* personName — a degree is never a surname or an initial (the saved contact's N field,
 * every broker avatar). */

import { describe, expect, it } from 'vitest';

import { nameInitials, splitName } from './personName';

describe('splitName', () => {
  it('puts degrees before and after the name in their own parts', () => {
    expect(splitName('Ing. Jan Novák, Ph.D.')).toEqual({ family: 'Novák', given: 'Jan', prefix: 'Ing.', suffix: 'Ph.D.' });
    expect(splitName('Ing. arch. Eva Malá MBA')).toEqual({ family: 'Malá', given: 'Eva', prefix: 'Ing. arch.', suffix: 'MBA' });
    expect(splitName('Mgr. Petr van der Berg')).toEqual({ family: 'Berg', given: 'Petr van der', prefix: 'Mgr.', suffix: '' });
  });

  it('knows degrees without their dots, "et" between two, and keeps a one-letter initial', () => {
    expect(splitName('Mgr. et Mgr. Jana Nováková')).toEqual({ family: 'Nováková', given: 'Jana', prefix: 'Mgr. et Mgr.', suffix: '' });
    expect(splitName('MUDr Jan Novák PhD')).toEqual({ family: 'Novák', given: 'Jan', prefix: 'MUDr', suffix: 'PhD' });
    expect(splitName('Jan N.')).toEqual({ family: 'N.', given: 'Jan', prefix: '', suffix: '' });
    expect(nameInitials('Mgr. et Mgr. Jana Nováková')).toBe('JN');
  });

  it('reads a one-word name as a surname and a bare degree as no name', () => {
    expect(splitName('Novák')).toEqual({ family: 'Novák', given: '', prefix: '', suffix: '' });
    expect(splitName('Bc.')).toEqual({ family: '', given: '', prefix: 'Bc.', suffix: '' });
  });
});

describe('nameInitials', () => {
  it('draws initials from the first and last word of the name, degrees aside', () => {
    expect(nameInitials('Jana Kubíčková')).toBe('JK');
    expect(nameInitials('Ing. Petr van der Berg')).toBe('PB');
    expect(nameInitials('Petr Novák, MBA')).toBe('PN');
    expect(nameInitials('Bc.')).toBe('?');
    expect(nameInitials(null)).toBe('?');
  });
});
