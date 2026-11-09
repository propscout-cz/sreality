// @vitest-environment node
/* MapLibre stays off the first visit only while nothing outside a map module
 * imports maplibre-gl or lib/basemap at the top level. This runs the real
 * eslint.config.js over each shape, so a rule edit that stops catching one
 * fails here instead of in the bundle. */
import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const eslint = new ESLint({ cwd: process.cwd() });

async function restrictedImports(code: string, filePath: string): Promise<number> {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((m) => m.ruleId === 'no-restricted-imports').length;
}

describe('map imports stay behind lazyChunk', { timeout: 30_000 }, () => {
  it.each([
    ["import maplibregl from 'maplibre-gl';", 'src/components/phone/LocationBody.tsx'],
    ["import { createMap } from '@/lib/basemap';", 'src/components/phone/LocationBody.tsx'],
    ["import { createMap } from './basemap';", 'src/lib/foo.ts'],
  ])('refuses %s in %s', async (code, filePath) => {
    expect(await restrictedImports(code, filePath)).toBe(1);
  });

  it.each([
    ["import type { Map } from 'maplibre-gl';", 'src/lib/foo.ts'],
    [
      "import { lazyChunk } from '@/lib/lazyChunk';\nconst M = lazyChunk(() => import('./PhoneMap'));",
      'src/components/phone/LocationBody.tsx',
    ],
    ["import { createMap } from '@/lib/basemap';", 'src/components/phone/PhoneMap.tsx'],
  ])('allows %s in %s', async (code, filePath) => {
    expect(await restrictedImports(code, filePath)).toBe(0);
  });
});
