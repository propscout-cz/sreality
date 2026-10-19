/* The overrides a Valuo POST carries are exactly the user's changes from the defaults. */
import { describe, expect, it } from 'vitest';

import { missingRequired, withEdit, type ValuoField } from './valuo';

describe('withEdit', () => {
  const defaults = { kind: 'sale', floor: 3 };

  it('keeps a change, drops a value set back, and keeps a clear as null', () => {
    const changed = withEdit({}, defaults, 'floor', 4);
    expect(changed).toEqual({ floor: 4 });
    expect(withEdit(changed, defaults, 'floor', 3)).toEqual({});
    expect(withEdit({}, defaults, 'kind', null)).toEqual({ kind: null });
  });

  it('treats a parameter with no default as null: clearing a pick sends nothing', () => {
    expect(withEdit(withEdit({}, defaults, 'rating', 'good'), defaults, 'rating', null)).toEqual({});
  });
});

it('missingRequired names the required parameters still empty', () => {
  const f = (name: string, required: boolean) =>
    ({ name, label: name.toUpperCase(), required }) as ValuoField;
  expect(missingRequired([f('rating', true), f('floor', false), f('kind', true)], { kind: 'sale' })).toEqual([
    'RATING',
  ]);
});
