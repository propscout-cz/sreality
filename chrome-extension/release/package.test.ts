/* `npm test` (node --test; Node >= 22.18 strips the types). */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { checkPackage, makeStorePackage, type PackageFiles } from './package.ts';
import type { AllowedList } from './allowed.ts';

// An independent literal, not the committed list: the checks are tested, not today's sites.
const ALLOWED: AllowedList = {
  permissions: ['storage', 'identity'],
  hostPermissions: [],
  contentScriptMatches: ['https://www.sreality.cz/*', 'https://reality.idnes.cz/*'],
};
const API_HOST = 'api.example-deploy.test';

function manifest(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    manifest_version: 3,
    name: 'PropScout.cz — výnos & pipeline',
    version: '0.11.7',
    description: 'Panel u nabídek z realitních portálů.',
    permissions: ['identity', 'storage'],
    host_permissions: [],
    background: { service_worker: 'background.js', type: 'module' },
    content_scripts: [{
      matches: ['https://reality.idnes.cz/*', 'https://www.sreality.cz/*'],
      js: ['content.js'],
      run_at: 'document_idle',
    }],
    icons: { 128: 'icon-128.png' },
    ...patch,
  };
}

function pkg(m: Record<string, unknown>, extra: PackageFiles = {}): PackageFiles {
  return {
    'manifest.json': JSON.stringify(m),
    'content.js': '(function () { console.log("panel"); })();\n',
    'background.js': 'chrome.runtime.onMessage.addListener(() => true);\n',
    'icon-128.png': '',
    ...extra,
  };
}

const check = (files: PackageFiles): string[] => checkPackage(files, ALLOWED, [API_HOST]);

test('a package that asks for exactly the allowed list passes', () => {
  assert.deepEqual(check(pkg(manifest())), []);
});

test('a new permission fails, naming it', () => {
  const v = check(pkg(manifest({ permissions: ['identity', 'storage', 'tabs'] })));
  assert.equal(v.length, 1);
  assert.match(v[0], /permissions/);
  assert.match(v[0], /\+tabs/);
});

test('a dropped permission fails too: the list is compared for equality', () => {
  const v = check(pkg(manifest({ permissions: ['storage'] })));
  assert.equal(v.length, 1);
  assert.match(v[0], /-identity/);
});

test('a new content-script site fails', () => {
  const m = manifest();
  (m.content_scripts as { matches: string[] }[])[0].matches.push('https://www.bazos.cz/*');
  const v = check(pkg(m));
  assert.equal(v.length, 1);
  assert.match(v[0], /content-script sites/);
  assert.match(v[0], /\+https:\/\/www\.bazos\.cz\/\*/);
});

test('a site in a second content_scripts entry counts as well', () => {
  const m = manifest({
    content_scripts: [
      { matches: ['https://reality.idnes.cz/*', 'https://www.sreality.cz/*'], js: ['content.js'] },
      { matches: ['https://evil.example/*'], js: ['content.js'] },
    ],
  });
  assert.match(check(pkg(m)).join('\n'), /\+https:\/\/evil\.example\/\*/);
});

test('any host permission fails, required or optional', () => {
  assert.match(check(pkg(manifest({ host_permissions: ['https://*/*'] }))).join('\n'), /host permissions/);
  assert.match(
    check(pkg(manifest({ optional_host_permissions: ['https://x.test/*'] }))).join('\n'),
    /optional_host_permissions/,
  );
  assert.match(check(pkg(manifest({ optional_permissions: ['tabs'] }))).join('\n'), /optional_permissions/);
});

test('a pinned key fails: the store assigns the ID', () => {
  assert.match(check(pkg(manifest({ key: 'MIIBIjAN' }))).join('\n'), /"key"/);
});

test('a source map fails, as a file or as a reference in a script', () => {
  assert.match(check(pkg(manifest(), { 'content.js.map': '{}' })).join('\n'), /content\.js\.map/);
  const v = check(pkg(manifest(), { 'background.js': 'x();\n//# sourceMappingURL=background.js.map\n' }));
  assert.match(v.join('\n'), /background\.js.*sourceMappingURL/);
});

test('a deployed hostname anywhere in the manifest fails', () => {
  const fromBuild = check(pkg(manifest({ externally_connectable: { matches: [`https://${API_HOST}/*`] } })));
  assert.match(fromBuild.join('\n'), /api\.example-deploy\.test/);
  const known = check(pkg(manifest({ homepage_url: 'https://app.propscout.cz/' })));
  assert.match(known.join('\n'), /app\.propscout\.cz/);
  const auth = check(pkg(manifest({ description: 'Talks to abcd.supabase.co' })));
  assert.match(auth.join('\n'), /supabase\.co/);
});

test('the brand name is not a deployed hostname', () => {
  assert.deepEqual(check(pkg(manifest({ name: 'PropScout.cz' }))), []);
});

test('a description over 132 characters fails; 132 passes', () => {
  assert.deepEqual(check(pkg(manifest({ description: 'ž'.repeat(132) }))), []);
  assert.match(check(pkg(manifest({ description: 'ž'.repeat(133) }))).join('\n'), /133 characters/);
});

test('a missing or unreadable manifest fails', () => {
  assert.match(check({ 'content.js': '' }).join('\n'), /manifest\.json/);
  assert.match(check({ 'manifest.json': '{nope' }).join('\n'), /manifest\.json/);
});

test('the store package drops the key and keeps every file and every other field', () => {
  const dist = mkdtempSync(join(tmpdir(), 'ext-dist-'));
  const out = join(mkdtempSync(join(tmpdir(), 'ext-store-')), 'store');
  const withKey = manifest({ key: 'MIIBIjAN' });
  writeFileSync(join(dist, 'manifest.json'), JSON.stringify(withKey, null, 2));
  writeFileSync(join(dist, 'content.js'), 'one();\n');
  mkdirSync(join(dist, 'assets'));
  writeFileSync(join(dist, 'assets', 'panel.css'), '.a{}');
  writeFileSync(join(dist, 'icon-128.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]));

  makeStorePackage(dist, out);

  const written = JSON.parse(readFileSync(join(out, 'manifest.json'), 'utf8')) as Record<string, unknown>;
  const { key: _dropped, ...expected } = withKey;
  assert.deepEqual(written, expected);
  assert.equal(readFileSync(join(out, 'content.js'), 'utf8'), 'one();\n');
  assert.equal(readFileSync(join(out, 'assets', 'panel.css'), 'utf8'), '.a{}');
  assert.deepEqual([...readFileSync(join(out, 'icon-128.png'))], [0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]);
  // The build output itself keeps its key: unpacked dev installs keep their fixed ID.
  assert.equal(JSON.parse(readFileSync(join(dist, 'manifest.json'), 'utf8')).key, 'MIIBIjAN');
  assert.ok(existsSync(join(out, 'icon-128.png')));
});
