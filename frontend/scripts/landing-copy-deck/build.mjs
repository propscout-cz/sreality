#!/usr/bin/env node
/* Build the landing-page copy deck (README.md) from the built landing pages.
 *
 *   npm run build                                    # from frontend/: the SPA + the landing pages into dist/
 *   node scripts/landing-copy-deck/build.mjs [outDir] # default outDir: frontend/.copy-deck/
 *
 * Writes outDir/index.html (the three pages, each in a `.cd-page`, plus a "hidden texts" tab), the landing stylesheet
 * and its fonts, deck.js and deck.css, then prints the publish list ([{as, path}]) for the artifact server's
 * publish_files. Stdlib only; reads dist/landing/*.html and the strings the scripts set at run time. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONT = path.resolve(HERE, '..', '..');
const DIST = path.join(FRONT, 'dist');
const OUT = path.resolve(process.argv[2] || path.join(FRONT, '.copy-deck'));
const PAGES = [['index', 'index.html'], ['dekujeme', 'dekujeme.html'], ['soukromi', 'soukromi.html']];

const read = (p) => fs.readFileSync(p, 'utf8');
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const first = (re, s, what) => {
  const m = re.exec(s);
  if (!m) throw new Error(`copy deck: ${what} not found — update build.mjs to the page's new shape`);
  return m[1];
};

const html = Object.fromEntries(PAGES.map(([name, file]) => [name, read(path.join(DIST, 'landing', file))]));

// Each page's icon sprite moves to the top of the document: a gradient or pattern inside a hidden tab draws nothing.
const SPRITE = /<svg width="0" height="0"[\s\S]*?<\/svg>\s*/;
const sprites = PAGES.map(([name]) => (SPRITE.exec(html[name]) || [''])[0]).join('\n');
for (const [name] of PAGES) html[name] = html[name].replace(SPRITE, '');

// The page bodies, made inert and local: no scripts, forms post nowhere, assets by relative path.
function body(src) {
  return first(/<body[^>]*>([\s\S]*)<\/body>/i, src, 'body')
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/\saction="[^"]*"/g, ' action="#"')
    .replace(/(["(])\/assets\//g, '$1assets/');
}

// Strings no HTML file holds: the browser tab and share texts, and what the scripts write in some states.
const src = (f) => read(path.join(FRONT, 'src', 'landing', f));
const meta = (name) => first(new RegExp(`<meta (?:name|property)="${name}" content="([^"]*)"`), html.index, name);
const hidden = [
  ['Browser tab · landing page', first(/<title>([^<]*)<\/title>/, html.index, 'title')],
  ['Browser tab · thank-you page', first(/<title>([^<]*)<\/title>/, html.dekujeme, 'title')],
  ['Browser tab · privacy page', first(/<title>([^<]*)<\/title>/, html.soukromi, 'title')],
  ['Search result / link preview · description', meta('description')],
  ['Facebook share card · title', meta('og:title')],
  ['Facebook share card · description', meta('og:description')],
];
const errors = first(/export const ERRORS[^{]*\{([\s\S]*?)\};/, src('shared.ts'), 'ERRORS');
for (const m of errors.matchAll(/(\w+):\s*'([^']*)'/g)) hidden.push([`Form error · ${m[1]}`, esc(m[2])]);
hidden.push(['Button while sending', esc(first(/label\.textContent = '([^']*)'/, src('main.ts'), 'busy label').trim())]);
const thanks = src('thanks.ts');
hidden.push(['Thank-you page · text shared with a friend', esc(first(/SHARE_TEXT = '([^']*)'/, thanks, 'SHARE_TEXT'))]);
hidden.push(['Thank-you page · after copying the link', esc(first(/shareLabel\.textContent = '([^']*)'/, thanks, 'copied label'))]);

const hiddenPage = `
<main class="page"><div class="wrap">
  <p class="eyebrow" data-cd-skip>Not on the page itself</p>
  <h1 data-cd-skip>Hidden texts</h1>
  <p class="lede" data-cd-skip>These appear in a browser tab, a link preview, an error message or the moment after a click. Edit them here like any other text.</p>
  <div class="cd-hidden-list">
${hidden.map(([label, text]) => `    <section class="card" data-label="${esc(label)}"><p class="cd-hid-label" data-cd-skip>${esc(label)}</p><p class="cd-hid-text">${text}</p></section>`).join('\n')}
  </div>
</div></main>`;

const css = first(/href="\/assets\/(landing-[^"]+\.css)"/, html.index, 'landing stylesheet');
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUT, 'assets'), { recursive: true });
const files = [];
const put = (as, data) => {
  const dest = path.join(OUT, as);
  if (Buffer.isBuffer(data)) fs.writeFileSync(dest, data); else fs.writeFileSync(dest, data, 'utf8');
  files.push({ as, path: dest });
};
const sheet = read(path.join(DIST, 'assets', css));
put(`assets/${css}`, sheet.replace(/\/assets\//g, ''));
for (const font of new Set(sheet.match(/landing-[\w.-]+\.woff2/g) || [])) put(`assets/${font}`, fs.readFileSync(path.join(DIST, 'assets', font)));
put('deck.js', read(path.join(HERE, 'deck.js')));
put('deck.css', read(path.join(HERE, 'deck.css')));
put('index.html', `<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PropScout landing copy</title>
<link rel="stylesheet" href="assets/${css}">
<link rel="stylesheet" href="deck.css">
<script src="deck.js" defer></script>
</head>
<body>
${sprites}
${PAGES.map(([name]) => `<div class="cd-page" data-page="${name}"${name === 'index' ? '' : ' hidden'}>\n${body(html[name])}\n</div>`).join('\n')}
<div class="cd-page" data-page="hidden" hidden>${hiddenPage}
</div>
</body>
</html>
`);
process.stdout.write(JSON.stringify(files, null, 1) + '\n');
