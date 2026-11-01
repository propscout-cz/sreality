/* The store package: the real build without its pinned `key`, checked against the hand-written
 * allowed list (allowed.ts). Node built-ins only. The release (and, on every pull request, CI)
 * refuses a package this finds anything wrong with. */

import { cpSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { DEPLOYED_HOST_MARKERS, DESCRIPTION_MAX_CHARS, type AllowedList } from './allowed.ts';

/* A package by relative path: a text file's contents, '' for a binary one (the icons). */
export type PackageFiles = Record<string, string>;

const TEXT_EXTENSIONS = new Set(['.js', '.mjs', '.json', '.html', '.css', '.txt']);

export function readPackage(dir: string): PackageFiles {
  const files: PackageFiles = {};
  const walk = (at: string): void => {
    for (const entry of readdirSync(at, { withFileTypes: true })) {
      const path = join(at, entry.name);
      if (entry.isDirectory()) { walk(path); continue; }
      const name = relative(dir, path).split(sep).join('/');
      files[name] = TEXT_EXTENSIONS.has(extname(entry.name)) ? readFileSync(path, 'utf8') : '';
    }
  };
  walk(dir);
  return files;
}

/* Copies the build to `outDir` without the manifest's `key`. The repository keeps the key, so
 * unpacked and pull-request builds keep their fixed ID until the store's ID replaces it. */
export function makeStorePackage(distDir: string, outDir: string): void {
  rmSync(outDir, { recursive: true, force: true });
  cpSync(distDir, outDir, { recursive: true });
  const path = join(outDir, 'manifest.json');
  const { key: _pinned, ...manifest } = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  writeFileSync(path, JSON.stringify(manifest, null, 2) + '\n');
}

/* A URL's hostname, tolerating a missing scheme; null when empty or unreadable. */
export function hostOf(raw: string | undefined): string | null {
  const trimmed = (raw ?? '').trim();
  if (trimmed === '') return null;
  try {
    return new URL(/^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`).hostname;
  } catch {
    return null;
  }
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : [];
}

function differences(what: string, actual: readonly string[], allowed: readonly string[]): string[] {
  const have = new Set(actual);
  const want = new Set(allowed);
  const diff = [
    ...[...have].filter((x) => !want.has(x)).map((x) => `+${x}`),
    ...[...want].filter((x) => !have.has(x)).map((x) => `-${x}`),
  ];
  return diff.length === 0 ? [] : [`${what} differ from release/allowed.ts: ${diff.join(', ')}`];
}

/* Every way the package breaks the store rules; empty when it may ship. `deployedHosts` are the
 * build's own API, auth and app hosts, checked besides DEPLOYED_HOST_MARKERS. */
export function checkPackage(
  files: PackageFiles,
  allowed: AllowedList,
  deployedHosts: readonly string[],
): string[] {
  const raw = files['manifest.json'];
  if (raw == null) return ['manifest.json is missing'];
  let manifest: Record<string, unknown>;
  try {
    manifest = JSON.parse(raw) as Record<string, unknown>;
  } catch (err) {
    return [`manifest.json does not parse: ${(err as Error).message}`];
  }

  const contentScripts = Array.isArray(manifest.content_scripts) ? manifest.content_scripts : [];
  const sites = contentScripts.flatMap((cs: unknown) => strings((cs as { matches?: unknown })?.matches));
  const violations = [
    ...differences('permissions', strings(manifest.permissions), allowed.permissions),
    ...differences('host permissions', strings(manifest.host_permissions), allowed.hostPermissions),
    ...differences('content-script sites', sites, allowed.contentScriptMatches),
  ];
  for (const key of ['optional_permissions', 'optional_host_permissions']) {
    const extra = strings(manifest[key]);
    if (extra.length > 0) violations.push(`${key} must be empty, has ${extra.join(', ')}`);
  }
  if ('key' in manifest) {
    violations.push('manifest has a "key": the store assigns the ID, so the package must not pin one');
  }

  const text = JSON.stringify(manifest).toLowerCase();
  const hosts = new Set([...deployedHosts, ...DEPLOYED_HOST_MARKERS].map((h) => h.toLowerCase()).filter(Boolean));
  for (const host of hosts) {
    if (text.includes(host)) violations.push(`manifest names a deployed hostname: ${host}`);
  }

  const description = typeof manifest.description === 'string' ? manifest.description : '';
  const length = [...description].length;
  if (length > DESCRIPTION_MAX_CHARS) {
    violations.push(`description is ${length} characters; the store allows ${DESCRIPTION_MAX_CHARS}`);
  }

  for (const [path, body] of Object.entries(files)) {
    if (path.endsWith('.map')) violations.push(`${path}: source maps stay out of the store package`);
    else if (/\/[/*][#@] sourceMappingURL=/.test(body)) {
      violations.push(`${path} references a source map (sourceMappingURL)`);
    }
  }
  return violations;
}
