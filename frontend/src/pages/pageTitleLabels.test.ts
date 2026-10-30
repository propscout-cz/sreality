/* Ruling P26 (2026-10-27): a page title is its h1 alone, with no small-caps
 * label above it, because the nav already says where you are. The only labels
 * left above an h1 say what HAPPENED (an error, a 404, a missing record), not
 * where you are, so they are pinned here site by site.
 *
 * Blind spots, so a green run is read for what it is: only an `<h1` counts (the
 * phone pages title in an h2 under PhoneShell's h1); the label must be a `<p>`,
 * `<div>` or `<span>` with a plain-string `className="…"` holding `uppercase`,
 * with no markup inside it and nothing but whitespace between it and the h1. */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (name.endsWith('.tsx') && !name.endsWith('.test.tsx')) out.push(p);
  }
  return out;
}

const LABEL_ABOVE_H1 = /<(p|div|span)\s+className="[^"]*\buppercase\b[^"]*"[^>]*>([^<]*)<\/\1>\s*<h1\b/g;

/* Each of these pages drops its label in its own redesign agenda's commit, and
 * that commit's tests pin it, so this census stays green whichever agendas land. */
const OWNED_ELSEWHERE = [
  'components/BrowseExperience.tsx',
  'pages/BrokerReview.tsx',
  'pages/CollectionDetail.tsx',
  'pages/Collections.tsx',
  'pages/Notifications.tsx',
  'pages/Pipeline.tsx',
  'pages/WatchdogManage.tsx',
];

function labelSites(): string[] {
  const root = join(process.cwd(), 'src');
  const sites: string[] = [];
  for (const file of sources(root)) {
    const rel = relative(root, file);
    if (OWNED_ELSEWHERE.includes(rel) || rel.startsWith('pages/phone/')) continue;
    for (const m of readFileSync(file, 'utf8').matchAll(LABEL_ABOVE_H1)) {
      sites.push(`${rel}: ${m[2].replace(/\s+/g, ' ').trim()}`);
    }
  }
  return sites.sort();
}

describe('small-caps labels above page titles (P26)', () => {
  it('leaves only the status labels, each site named (so a silent regex miss cannot pass)', () => {
    expect(labelSites()).toEqual([
      "components/ErrorBoundary.tsx: {userFacing ? 'Heads up' : 'Something broke'}",
      'pages/EstimationDetail.tsx: Not found',
      'pages/PropertyDetail.tsx: Not found',
      'routes.tsx: 404',
    ]);
  });
});
