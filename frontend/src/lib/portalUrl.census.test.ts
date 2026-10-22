/* The no-reconstruction rail (docs/design/portal-listing-url.md).
 *
 * A listing's portal URL is `source_url` on the row, stored by the portal's own
 * parser. The SPA used to rebuild sreality's from display labels — wrong for 14
 * of 48 sub-category codes, and a green test pinned the 404. This census fails
 * the build if a sreality detail URL is ever assembled in app code again. */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

import * as portals from './portals';

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith('.generated.ts')) {
      out.push(p);
    }
  }
  return out;
}

describe('portal URL census', () => {
  it('no app code assembles a sreality detail URL', () => {
    const root = join(process.cwd(), 'src');
    const offenders: string[] = [];
    for (const file of sources(root)) {
      if (readFileSync(file, 'utf8').includes('sreality.cz/detail')) offenders.push(relative(root, file));
    }
    expect(offenders).toEqual([]);
  });

  it('walks the app source (so a silent path miss cannot pass)', () => {
    const root = join(process.cwd(), 'src');
    expect(sources(root).map((f) => relative(root, f))).toContain('lib/portals.ts');
  });

  it('lib/portals exports labels only — no URL builder survives', () => {
    expect(Object.keys(portals).filter((k) => /url/i.test(k))).toEqual([]);
  });
});
