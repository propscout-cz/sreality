import { describe, expect, it } from 'vitest';

import {
  CURATED_SETS,
  cycle,
  cycleSet,
  cycleElement,
  matchesScope,
  normalizeScope,
  parseSetScope,
  scopeActive,
  scopeArgs,
  scopeIncludes,
  scopeWords,
  setScopeToParams,
  type CuratedSet,
  type SetScope,
} from './setScope';

const S = (set: SetScope['set'], inc: number[] = [], out: number[] = []): SetScope => ({
  set, in: inc, out,
});

describe('cycle', () => {
  it('moves a value off → included → excluded → off, the rest in place', () => {
    const on = cycle<string>([], ['b'], 'a');
    expect(on).toEqual({ inc: ['a'], exc: ['b'] });
    const not = cycle(on.inc, on.exc, 'a');
    expect(not).toEqual({ inc: [], exc: ['b', 'a'] });
    expect(cycle(not.inc, not.exc, 'a')).toEqual({ inc: [], exc: ['b'] });
  });
});

describe('cycleElement', () => {
  it('moves an element off → in → out → off', () => {
    const on = cycleElement(null, 10);
    expect(on).toEqual(S(null, [10]));
    const not = cycleElement(on, 10);
    expect(not).toEqual(S(null, [], [10]));
    expect(cycleElement(not, 10)).toBeNull();
  });

  it('leaves the other elements where they are', () => {
    expect(cycleElement(S(null, [10], [11]), 12)).toEqual(S(null, [10, 12], [11]));
  });

  it('an element moved in supersedes the whole-set include; an excluded element keeps it', () => {
    expect(cycleElement(S('in'), 5)).toEqual(S(null, [5]));
    expect(cycleElement(cycleElement(S('in'), 5), 5)).toEqual(S(null, [], [5]));
  });

  it('lifts the whole-set exclusion rather than swallowing the tap', () => {
    expect(cycleElement(S('out'), 5)).toEqual(S(null, [5]));
  });
});

describe('cycleSet', () => {
  it('toggles the dedicated "not in" chip off ↔ out, clearing the elements', () => {
    const out = cycleSet(S(null, [1], [2]));
    expect(out).toEqual(S('out'));
    expect(cycleSet(out)).toBeNull();
  });

  it('toggles the whole-set include off ↔ in, superseding element includes, keeping excludes', () => {
    const on = cycleSet(S(null, [1], [2]), 'in');
    expect(on).toEqual(S('in', [], [2]));
    expect(cycleSet(on, 'in')).toEqual(S(null, [], [2]));
    expect(cycleSet(S('in'), 'in')).toBeNull();
  });

  it('switches between the two whole-set sides', () => {
    expect(cycleSet(S('in'), 'out')).toEqual(S('out'));
    expect(cycleSet(S('out'), 'in')).toEqual(S('in'));
  });
});

describe('normalizeScope — contradictions are unrepresentable', () => {
  it('an all-empty scope is off', () => {
    expect(normalizeScope(S(null))).toBeNull();
    expect(normalizeScope(null)).toBeNull();
  });

  it("'out' empties both element lists", () => {
    expect(normalizeScope(S('out', [1], [2]))).toEqual(S('out'));
  });

  it("an element on the in side supersedes 'in'", () => {
    expect(normalizeScope(S('in', [1], [2]))).toEqual(S(null, [1], [2]));
    expect(normalizeScope(S('in', [], [2]))).toEqual(S('in', [], [2]));
  });

  it('an element sits on one list only (out wins), lists are deduplicated', () => {
    expect(normalizeScope(S(null, [1, 1, 2], [2, 3, 3]))).toEqual(S(null, [1], [2, 3]));
    expect(normalizeScope(S(null, [4], [4]))).toEqual(S(null, [], [4]));
  });
});

describe('matchesScope', () => {
  const tagged = [10, 11];

  it('is no constraint when off', () => {
    expect(matchesScope(undefined, null, 'every')).toBe(true);
  });

  it("'in' needs any element, 'out' none", () => {
    expect(matchesScope(tagged, S('in'), 'some')).toBe(true);
    expect(matchesScope(undefined, S('in'), 'some')).toBe(false);
    expect(matchesScope([], S('in'), 'some')).toBe(false);
    expect(matchesScope(tagged, S('out'), 'some')).toBe(false);
    expect(matchesScope(undefined, S('out'), 'some')).toBe(true);
  });

  it('every: needs EVERY included element (tags, AND)', () => {
    expect(matchesScope(tagged, S(null, [10, 11]), 'every')).toBe(true);
    expect(matchesScope([10], S(null, [10, 11]), 'every')).toBe(false);
    expect(matchesScope(undefined, S(null, [10]), 'every')).toBe(false);
  });

  it('some: needs ANY included element (collections, stages, OR)', () => {
    expect(matchesScope([10], S(null, [10, 11]), 'some')).toBe(true);
    expect(matchesScope([12], S(null, [10, 11]), 'some')).toBe(false);
  });

  it('refuses ANY excluded element, and a property in no element passes', () => {
    expect(matchesScope(tagged, S(null, [], [11, 12]), 'every')).toBe(false);
    expect(matchesScope([10], S(null, [], [11, 12]), 'every')).toBe(true);
    expect(matchesScope(undefined, S(null, [], [11]), 'every')).toBe(true);
  });

  it("'in' with an excluded stage: on the board, but not at that stage", () => {
    expect(matchesScope([5], S('in', [], [5]), 'some')).toBe(false);
    expect(matchesScope([6], S('in', [], [5]), 'some')).toBe(true);
    expect(matchesScope(undefined, S('in', [], [5]), 'some')).toBe(false);
  });
});

describe('predicates', () => {
  it('scopeActive is false only when off', () => {
    expect(scopeActive(null)).toBe(false);
    expect(scopeActive(S(null))).toBe(false);
    expect(scopeActive(S('out'))).toBe(true);
    expect(scopeActive(S(null, [], [1]))).toBe(true);
  });

  it('scopeIncludes lights on the IN side only', () => {
    expect(scopeIncludes(S('in'))).toBe(true);
    expect(scopeIncludes(S(null, [1]))).toBe(true);
    expect(scopeIncludes(S('out'))).toBe(false);
    expect(scopeIncludes(S(null, [], [1]))).toBe(false);
    expect(scopeIncludes(null)).toBe(false);
  });
});

const params = (qs: string) => new URLSearchParams(qs);
const wire = (name: CuratedSet, s: SetScope | null): string => {
  const sp = new URLSearchParams();
  setScopeToParams(sp, name, s);
  return sp.toString().replace(/%2C/g, ',');
};

describe('URL codec', () => {
  it('reads the legacy wire byte-identically', () => {
    expect(parseSetScope(params('tags=1,2&tags_excluded=3'), 'tags')).toEqual(S(null, [1, 2], [3]));
    expect(parseSetScope(params('collections=4'), 'collections')).toEqual(S(null, [4]));
    expect(parseSetScope(params('pipeline=any'), 'pipeline')).toEqual(S('in'));
    expect(parseSetScope(params('pipeline=5,6'), 'pipeline')).toEqual(S(null, [5, 6]));
  });

  it('writes the legacy wire byte-identically', () => {
    expect(wire('tags', S(null, [1, 2], [3]))).toBe('tags=1,2&tags_excluded=3');
    expect(wire('collections', S(null, [4]))).toBe('collections=4');
    expect(wire('pipeline', S('in'))).toBe('pipeline=any');
    expect(wire('pipeline', S(null, [5, 6]))).toBe('pipeline=5,6');
  });

  it('round-trips every shape on every set, the new params included', () => {
    const shapes = [S('in'), S('out'), S(null, [1, 2]), S(null, [], [3]), S('in', [], [3]), S(null, [1], [3])];
    for (const name of Object.keys(CURATED_SETS) as CuratedSet[]) {
      for (const s of shapes) {
        expect(parseSetScope(params(wire(name, s)), name)).toEqual(s);
      }
      expect(wire(name, null)).toBe('');
    }
    expect(wire('collections', S('out'))).toBe('collections_excluded=any');
    expect(wire('pipeline', S('in', [], [7]))).toBe('pipeline=any&pipeline_excluded=7');
  });

  it('degrades junk to off on every side of every set', () => {
    for (const name of Object.keys(CURATED_SETS) as CuratedSet[]) {
      expect(parseSetScope(params(`${name}=junk`), name)).toBeNull();
      expect(parseSetScope(params(`${name}_excluded=junk`), name)).toBeNull();
      expect(parseSetScope(params(`${name}=,-1,0,x`), name)).toBeNull();
      expect(parseSetScope(params(`${name}=junk&${name}_excluded=4`), name)).toEqual(S(null, [], [4]));
    }
  });

  it('normalises a contradicting URL and dedupes element ids', () => {
    expect(parseSetScope(params('pipeline=any&pipeline_excluded=any'), 'pipeline')).toEqual(S('out'));
    expect(parseSetScope(params('tags=1,1,2&tags_excluded=2'), 'tags')).toEqual(S(null, [1], [2]));
  });

  it('patches an existing URL in place, deleting an off side', () => {
    const sp = params('tags=1&tags_excluded=2&status=active');
    setScopeToParams(sp, 'tags', S(null, [1]));
    expect(sp.toString()).toBe('tags=1&status=active');
  });
});

describe('scopeArgs', () => {
  it('carries only the active sides, never a null', () => {
    expect(scopeArgs({ tags: null, collections: null, pipeline: null })).toEqual({});
    expect(scopeArgs({
      tags: S(null, [1, 2], [3]),
      collections: S('out'),
      pipeline: S('in', [], [7]),
    })).toEqual({
      tags_in: [1, 2],
      tags_out: [3],
      collections_set: 'out',
      pipeline_set: 'in',
      pipeline_out: [7],
    });
  });
});

describe('scopeWords', () => {
  it('names each side in Czech', () => {
    expect(scopeWords('pipeline', S('in'))).toBe('v pipeline');
    expect(scopeWords('pipeline', S('out'))).toBe('mimo pipeline');
    expect(scopeWords('collections', S('out'))).toBe('v žádné kolekci');
    expect(scopeWords('tags', S('out'))).toBe('bez štítků');
    expect(scopeWords('collections', S(null, [1, 2]))).toBe('v kolekci (2 kolekce)');
    expect(scopeWords('pipeline', S(null, [1, 2, 3, 4, 5]))).toBe('v pipeline (5 fází)');
    expect(scopeWords('tags', S(null, [1], [2, 3]))).toBe('se štítkem (1 štítek), bez 2 štítků');
    expect(scopeWords('pipeline', S('in', [], [9]))).toBe('v pipeline, bez 1 fáze');
    expect(scopeWords('tags', S(null, [], [4]))).toBe('bez 1 štítku');
    expect(scopeWords('collections', S(null, [], [4, 5]))).toBe('bez 2 kolekcí');
    expect(scopeWords('tags', null)).toBeNull();
  });

  it('offers the dedicated chip labels per set', () => {
    expect(CURATED_SETS.collections.notInLabel).toBe('V žádné kolekci');
    expect(CURATED_SETS.pipeline.notInLabel).toBe('Mimo pipeline');
    expect(CURATED_SETS.tags.notInLabel).toBe('Bez štítků');
    expect(CURATED_SETS.pipeline.inAnyLabel).toBe('V pipeline');
    expect(CURATED_SETS.tags.inAnyLabel).toBeNull();
    expect(CURATED_SETS.tags.inRule).toBe('every');
  });
});
