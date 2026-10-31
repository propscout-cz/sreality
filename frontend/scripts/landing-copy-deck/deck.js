/* The landing-page copy deck: the built propscout.cz pages with every text editable in place.
 *
 * A "unit" is the outermost element holding text of its own (a heading, a paragraph, a list item, a button). Its key
 * is a hash of its PUBLISHED text, so an edit sticks to the words it replaces: after the real page changes and the deck
 * is rebuilt, edits whose text is gone (usually because they were applied) are listed apart instead of landing on the
 * wrong element. Edits live in the artifact's db, collection `edits`, one document per key; Claude reads them there to
 * apply them to frontend/landing/ (README.md beside this file). Without db (signed out, or a local preview) they live
 * in this browser's localStorage. */
(function () {
  'use strict';

  var TABS = [
    { hash: 'uvod', page: 'index', label: 'Landing page' },
    { hash: 'dekujeme', page: 'dekujeme', label: 'Thank-you' },
    { hash: 'soukromi', page: 'soukromi', label: 'Privacy' },
    { hash: 'skryte', page: 'hidden', label: 'Hidden texts' },
  ];
  var VARIANTS = [['default', 'Default'], ['flip', 'Flip'], ['najem', 'Rent']];
  var LOCAL_KEY = 'ps-copy-deck-edits';
  var root = document.documentElement;

  var ORIG_HTML = new Map(); // key -> published innerHTML
  var ORIG_TEXT = new Map(); // key -> published text
  var UNITS = new Map(); // key -> [elements]
  var docs = new Map(); // key -> edit document
  var state = { tab: 'uvod', variant: 'default', edit: true, hidden: false, panel: false, filter: 'pending' };
  var store = { db: null, uid: null, user: null, mode: 'connecting', chains: new Map() };
  var editing = null;

  function norm(t) { return String(t || '').replace(/\s+/g, ' ').trim(); }
  function fnv(s) {
    var h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return 'k' + ('0000000' + h.toString(16)).slice(-8);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function sameHtml(a, b) { return norm(a) === norm(b); }

  /* Stored html is shared data: drop anything that could run or load before it touches the page. */
  function clean(html) {
    var t = document.createElement('template');
    t.innerHTML = html;
    t.content.querySelectorAll('script,style,iframe,object,embed,link,meta,base,form,img,video,audio').forEach(function (n) { n.remove(); });
    t.content.querySelectorAll('*').forEach(function (n) {
      Array.prototype.slice.call(n.attributes).forEach(function (a) {
        var name = a.name.toLowerCase();
        if (name.indexOf('on') === 0 || name === 'contenteditable' || name === 'style' || name.indexOf('data-cd') === 0) n.removeAttribute(a.name);
        else if ((name === 'href' || name === 'xlink:href' || name === 'src') && /^\s*(javascript|data):/i.test(a.value)) n.removeAttribute(a.name);
      });
    });
    return t.innerHTML;
  }

  /* ---------- Units ---------- */
  function ownText(el) {
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3 && n.nodeValue.trim()) return true;
    }
    return false;
  }
  var SKIP = /^(script|style|svg|noscript|template|input|textarea|select|option)$/i;
  function collect(el, out) {
    for (var i = 0; i < el.children.length; i++) {
      var c = el.children[i];
      if (SKIP.test(c.tagName) || c.hasAttribute('data-cd-skip')) continue;
      if (ownText(c)) out.push(c);
      else collect(c, out);
    }
    return out;
  }
  function pageOf(el) { var p = el.closest('.cd-page'); return p ? p.getAttribute('data-page') : ''; }
  function contextOf(el) {
    var page = pageOf(el);
    if (page === 'hidden') { var c = el.closest('[data-label]'); return c ? c.getAttribute('data-label') : 'Hidden text'; }
    var art = el.closest('.feature');
    var feature = art && art.querySelector('.kicker') ? norm(art.querySelector('.kicker').textContent).replace(/^\d+\s*/, '') : '';
    if (el.closest('figure, .phone, .push')) return 'Mockup' + (feature ? ' · ' + feature : el.closest('.hero') ? ' · hero' : '');
    if (el.closest('.consent')) return 'Consent line (legal)';
    if (page === 'dekujeme') return el.closest('.foot') ? 'Footer' : 'Thank-you page';
    if (page === 'soukromi') return el.closest('.foot') ? 'Footer' : 'Privacy page (legal)';
    var map = [['.hero', 'Hero'], ['.proof', 'Numbers'], ['.band', 'Problem'], ['.audience', 'Who it is for'], ['.offer', 'Offer'],
      ['.faq', 'FAQ'], ['.foot', 'Footer'], ['.top', 'Header'], ['.cookie-bar', 'Cookie bar'], ['.sticky-cta', 'Sticky button']];
    if (feature) return 'Feature · ' + feature;
    if (el.closest('.features')) return 'Features intro';
    for (var i = 0; i < map.length; i++) if (el.closest(map[i][0])) return map[i][1];
    return 'Page';
  }
  function variantOf(el) { var v = el.closest('[data-variant]'); return v ? v.getAttribute('data-variant') : ''; }

  function index() {
    document.querySelectorAll('.cd-page').forEach(function (page) {
      collect(page, []).forEach(function (el) {
        var text = norm(el.textContent);
        if (!text) return;
        var key = fnv(text);
        el.setAttribute('data-cd-key', key);
        if (!ORIG_HTML.has(key)) { ORIG_HTML.set(key, el.innerHTML); ORIG_TEXT.set(key, text); }
        if (!UNITS.has(key)) UNITS.set(key, []);
        UNITS.get(key).push(el);
      });
    });
  }

  /* ---------- Store ---------- */
  function setStatus(mode, detail) {
    store.mode = mode;
    var s = document.getElementById('cd-status');
    if (!s) return;
    var words = {
      connecting: 'Connecting…', saved: 'All changes saved', saving: 'Saving…', slow: 'Can’t reach the saved changes yet',
      local: 'Saved in this browser only', error: 'Saving failed' + (detail ? ' (' + detail + ')' : ''),
    };
    s.textContent = words[mode] || mode;
    s.setAttribute('data-mode', mode);
  }
  function useLocal() {
    store.db = null;
    try { docs = new Map(Object.entries(JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}'))); } catch (e) { docs = new Map(); }
    setStatus('local');
    render();
  }
  function saveLocal() {
    var o = {};
    docs.forEach(function (v, k) { o[k] = v; });
    try { localStorage.setItem(LOCAL_KEY, JSON.stringify(o)); } catch (e) { /* storage full or blocked */ }
  }
  function initStore() {
    var c = window.claude;
    if (!c || typeof c.use !== 'function') { useLocal(); return; }
    c.use('db').then(function (db) {
      if (!db) { useLocal(); return; }
      store.db = db;
      c.use('user').then(function (u) {
        store.user = u;
        if (u && u.id) u.id().then(function (id) { store.uid = id; }, function () {});
      }, function () {});
      var timer = setTimeout(function () { setStatus('slow'); }, 10000);
      db.collection('edits').onSnapshot(function (snap) {
        if (!snap.metadata.fromCache) clearTimeout(timer);
        var next = new Map();
        snap.docs.forEach(function (d) { if (d.exists) next.set(d.id, d.data()); });
        docs = next;
        setStatus(snap.metadata.hasPendingWrites ? 'saving' : 'saved');
        render();
      }, function (err) {
        clearTimeout(timer);
        setStatus('error', err && err.code);
      });
    }, useLocal);
  }
  /* One write at a time per document: each key's writes run in order. */
  function write(key, fn) {
    var prev = store.chains.get(key) || Promise.resolve();
    var next = prev.then(fn).catch(function (err) { setStatus('error', err && err.code); });
    store.chains.set(key, next);
    return next;
  }
  function putEdit(key, doc) {
    docs.set(key, doc);
    render();
    if (!store.db) { saveLocal(); return; }
    setStatus('saving');
    write(key, function () { return store.db.collection('edits').doc(key).set(doc); });
  }
  function dropEdit(key) {
    docs.delete(key);
    render();
    if (!store.db) { saveLocal(); return; }
    setStatus('saving');
    write(key, function () { return store.db.collection('edits').doc(key).delete(); });
  }

  /* ---------- Editing ---------- */
  function startEdit(el, x, y) {
    if (editing === el) return;
    if (editing) editing.blur();
    editing = el;
    el.setAttribute('data-cd-before', el.innerHTML);
    el.querySelectorAll('svg').forEach(function (s) { s.setAttribute('contenteditable', 'false'); });
    el.setAttribute('contenteditable', 'true');
    el.setAttribute('spellcheck', 'true');
    el.focus();
    var range = null;
    if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(x, y);
    else if (document.caretPositionFromPoint) {
      var p = document.caretPositionFromPoint(x, y);
      if (p) { range = document.createRange(); range.setStart(p.offsetNode, p.offset); }
    }
    if (range && el.contains(range.startContainer)) {
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }
  function endEdit(el, keep) {
    var before = el.getAttribute('data-cd-before');
    el.removeAttribute('contenteditable');
    el.removeAttribute('spellcheck');
    el.removeAttribute('data-cd-before');
    el.querySelectorAll('[contenteditable]').forEach(function (s) { s.removeAttribute('contenteditable'); });
    if (editing === el) editing = null;
    if (!keep) { el.innerHTML = before; return; }
    var key = el.getAttribute('data-cd-key');
    var html = clean(el.innerHTML);
    var text = norm(el.textContent);
    var orig = ORIG_HTML.get(key);
    var current = docs.get(key);
    if (sameHtml(html, orig) || !text) {
      if (!text) el.innerHTML = current ? clean(current.html) : orig;
      else if (current) dropEdit(key);
      render();
      return;
    }
    if (current && sameHtml(html, current.html)) return;
    putEdit(key, {
      key: key,
      original: ORIG_TEXT.get(key),
      originalHtml: orig,
      html: html,
      text: text,
      context: contextOf(el),
      pages: Array.from(new Set((UNITS.get(key) || []).map(pageOf))),
      variant: variantOf(el),
      legal: Boolean(el.closest('.consent') || pageOf(el) === 'soukromi'),
      count: (UNITS.get(key) || []).length,
      status: 'pending',
      note: current && current.note ? current.note : '',
      by: store.uid || null,
      updatedAt: new Date().toISOString(),
    });
  }

  document.addEventListener('submit', function (e) { e.preventDefault(); }, true);
  document.addEventListener('click', function (e) {
    if (e.target.closest('.cd-ui')) return;
    var link = e.target.closest('a[data-page]');
    if (!state.edit) {
      if (link) { e.preventDefault(); var p = link.getAttribute('data-page'); go(p === 'index' ? 'uvod' : p); }
      else if (e.target.closest('a, button')) e.preventDefault();
      return;
    }
    if (e.target.closest('a, button, summary, label')) e.preventDefault();
    var unit = e.target.closest('[data-cd-key]');
    if (unit) startEdit(unit, e.clientX, e.clientY);
  }, true);
  document.addEventListener('keydown', function (e) {
    if (!editing) return;
    if (e.key === 'Escape') { e.preventDefault(); var el = editing; endEdit(el, false); el.blur(); }
    else if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); editing.blur(); }
    else if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); document.execCommand('insertLineBreak'); }
  });
  document.addEventListener('paste', function (e) {
    if (!editing) return;
    e.preventDefault();
    document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain'));
  });
  document.addEventListener('focusout', function (e) {
    if (editing && e.target === editing) endEdit(editing, true);
  });

  /* ---------- Rendering ---------- */
  function applyEdits() {
    UNITS.forEach(function (els, key) {
      var d = docs.get(key);
      var html = d ? clean(d.html) : ORIG_HTML.get(key);
      els.forEach(function (el) {
        if (el === editing) return;
        if (!sameHtml(el.innerHTML, html)) el.innerHTML = html;
        el.classList.toggle('cd-edited', Boolean(d && d.status !== 'applied'));
        el.classList.toggle('cd-applied', Boolean(d && d.status === 'applied'));
      });
    });
  }
  function go(hash) {
    var tab = TABS.filter(function (t) { return t.hash === hash; })[0] || TABS[0];
    state.tab = tab.hash;
    if (location.hash.slice(1) !== tab.hash) history.replaceState(null, '', '#' + tab.hash);
    render();
    window.scrollTo(0, 0);
  }
  function show(key) {
    var els = UNITS.get(key);
    if (!els || !els.length) return;
    var el = els[0];
    var tab = TABS.filter(function (t) { return t.page === pageOf(el); })[0];
    var v = variantOf(el);
    if (v) state.variant = v;
    if (el.closest('[hidden], .done-note, .cookie-bar, .sticky-cta')) state.hidden = true;
    if (tab && tab.hash !== state.tab) go(tab.hash); else render();
    if (window.innerWidth < 900) { state.panel = false; render(); }
    setTimeout(function () {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.remove('cd-flash');
      void el.offsetWidth;
      el.classList.add('cd-flash');
    }, 60);
  }
  function render() {
    var tab = TABS.filter(function (t) { return t.hash === state.tab; })[0];
    document.querySelectorAll('.cd-page').forEach(function (p) { p.hidden = p.getAttribute('data-page') !== tab.page; });
    if (state.variant === 'default') root.removeAttribute('data-v'); else root.setAttribute('data-v', state.variant);
    root.classList.toggle('cd-edit', state.edit);
    root.classList.toggle('cd-show-hidden', state.hidden);
    root.classList.toggle('cd-panel-open', state.panel);
    document.querySelectorAll('.cd-page details').forEach(function (d) { if (state.edit) d.open = true; });
    applyEdits();
    renderBar(tab);
    renderPanel();
  }

  /* ---------- Toolbar + panel ---------- */
  function bar() {
    var b = document.createElement('div');
    b.className = 'cd-ui cd-bar';
    b.innerHTML =
      '<div class="cd-brand"><span class="cd-mark">PS</span><span>Landing copy</span></div>' +
      '<nav class="cd-tabs" aria-label="Pages">' + TABS.map(function (t) {
        return '<button type="button" data-tab="' + t.hash + '">' + t.label + '</button>';
      }).join('') + '</nav>' +
      '<div class="cd-seg" data-variants role="group" aria-label="Headline variant"><span class="cd-seg-label">Ad variant</span>' +
      VARIANTS.map(function (v) { return '<button type="button" data-variant-pick="' + v[0] + '">' + v[1] + '</button>'; }).join('') + '</div>' +
      '<label class="cd-check"><input type="checkbox" id="cd-edit-toggle" checked> Edit text</label>' +
      '<label class="cd-check"><input type="checkbox" id="cd-hidden-toggle"> Show hidden states</label>' +
      '<span class="cd-status" id="cd-status" data-mode="connecting">Connecting…</span>' +
      '<button type="button" class="cd-changes" id="cd-changes">Changes <b id="cd-count">0</b></button>';
    document.body.insertBefore(b, document.body.firstChild);
    b.addEventListener('click', function (e) {
      var t = e.target.closest('[data-tab]');
      if (t) { go(t.getAttribute('data-tab')); return; }
      var v = e.target.closest('[data-variant-pick]');
      if (v) { state.variant = v.getAttribute('data-variant-pick'); render(); return; }
      if (e.target.closest('#cd-changes')) { state.panel = !state.panel; render(); }
    });
    b.querySelector('#cd-edit-toggle').addEventListener('change', function (e) { state.edit = e.target.checked; if (editing) editing.blur(); render(); });
    b.querySelector('#cd-hidden-toggle').addEventListener('change', function (e) { state.hidden = e.target.checked; render(); });
  }
  function renderBar(tab) {
    document.querySelectorAll('.cd-bar [data-tab]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-tab') === tab.hash)); });
    document.querySelectorAll('.cd-bar [data-variant-pick]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-variant-pick') === state.variant)); });
    var seg = document.querySelector('.cd-bar [data-variants]');
    if (seg) seg.hidden = tab.page !== 'index';
    var hid = document.getElementById('cd-hidden-toggle');
    if (hid) hid.checked = state.hidden;
    var pending = 0;
    docs.forEach(function (d) { if (d.status !== 'applied') pending++; });
    var c = document.getElementById('cd-count');
    if (c) c.textContent = String(pending);
  }

  var panelEl = null;
  function panel() {
    panelEl = document.createElement('aside');
    panelEl.className = 'cd-ui cd-panel';
    panelEl.setAttribute('aria-label', 'Copy changes');
    document.body.appendChild(panelEl);
    panelEl.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      var key = b.getAttribute('data-key');
      var act = b.getAttribute('data-act');
      if (act === 'close') { state.panel = false; render(); }
      else if (act === 'filter') { state.filter = b.getAttribute('data-filter'); renderPanel(); }
      else if (act === 'show') show(key);
      else if (act === 'undo') dropEdit(key);
      else if (act === 'copy') copyAll(b);
    });
    panelEl.addEventListener('change', function (e) {
      var t = e.target.closest('textarea[data-key]');
      if (!t) return;
      var key = t.getAttribute('data-key');
      var d = docs.get(key);
      if (!d || d.note === t.value) return;
      var next = Object.assign({}, d, { note: t.value, updatedAt: new Date().toISOString() });
      putEdit(key, next);
    });
  }
  function bucket(key, d) {
    if (d.status === 'applied') return 'applied';
    return UNITS.has(key) ? 'pending' : 'gone';
  }
  function renderPanel() {
    if (!panelEl) return;
    if (panelEl.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA') return;
    var rows = [];
    docs.forEach(function (d, key) { rows.push([key, d, bucket(key, d)]); });
    rows.sort(function (a, b) { return String(b[1].updatedAt).localeCompare(String(a[1].updatedAt)); });
    var counts = { pending: 0, gone: 0, applied: 0 };
    rows.forEach(function (r) { counts[r[2]]++; });
    var shown = rows.filter(function (r) { return r[2] === state.filter; });
    var filters = [['pending', 'To apply'], ['gone', 'Text changed'], ['applied', 'Applied']];
    var html = '<header class="cd-panel-head"><h2>Changes</h2><button type="button" data-act="copy" class="cd-btn">Copy as text</button>' +
      '<button type="button" data-act="close" class="cd-x" aria-label="Close">×</button></header>' +
      '<div class="cd-filters">' + filters.map(function (f) {
        return '<button type="button" data-act="filter" data-filter="' + f[0] + '" aria-pressed="' + (state.filter === f[0]) + '">' + f[1] + ' <b>' + counts[f[0]] + '</b></button>';
      }).join('') + '</div>';
    if (!shown.length) {
      html += '<p class="cd-empty">' + (state.filter === 'pending'
        ? 'No changes yet. Click any text on the page, type, then press Enter or click away. It saves by itself.'
        : state.filter === 'gone' ? 'Nothing here. An edit lands here when the page text it changed is no longer on the page.' : 'Nothing applied yet.') + '</p>';
    }
    shown.forEach(function (r) {
      var key = r[0], d = r[1];
      html += '<article class="cd-item">' +
        '<div class="cd-item-meta"><span class="cd-chip">' + esc(d.context || 'Page') + '</span>' +
        (d.variant && d.variant !== 'default' ? '<span class="cd-chip">' + esc(d.variant) + '</span>' : '') +
        (d.count > 1 ? '<span class="cd-chip">' + d.count + ' places</span>' : '') +
        (d.legal ? '<span class="cd-chip cd-legal">legal text</span>' : '') + '</div>' +
        '<p class="cd-was"><span>Was</span>' + esc(d.original || '') + '</p>' +
        '<p class="cd-now"><span>Now</span>' + esc(d.text || '') + '</p>' +
        '<label class="cd-note">Note for Claude<textarea data-key="' + key + '" rows="2" placeholder="Optional: why, or where else this applies">' + esc(d.note || '') + '</textarea></label>' +
        '<div class="cd-item-actions">' + (r[2] === 'pending' ? '<button type="button" class="cd-btn" data-act="show" data-key="' + key + '">Show on page</button>' : '') +
        '<button type="button" class="cd-btn cd-quiet" data-act="undo" data-key="' + key + '">' + (r[2] === 'pending' ? 'Undo' : 'Remove') + '</button></div>' +
        '</article>';
    });
    panelEl.innerHTML = html;
  }
  function copyAll(btn) {
    var lines = [];
    docs.forEach(function (d, key) {
      if (bucket(key, d) !== 'pending') return;
      lines.push('[' + (d.context || 'Page') + ']\nWAS: ' + d.original + '\nNOW: ' + d.text + (d.note ? '\nNOTE: ' + d.note : ''));
    });
    var text = lines.join('\n\n') || 'No pending changes.';
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = 'Copy as text'; }, 1500); },
        function () { btn.textContent = 'Copy blocked'; });
    }
  }

  function start() {
    index();
    bar();
    panel();
    var h = location.hash.slice(1);
    state.tab = TABS.some(function (t) { return t.hash === h; }) ? h : 'uvod';
    window.addEventListener('hashchange', function () { var x = location.hash.slice(1); if (x !== state.tab) go(x); });
    render();
    setStatus('connecting');
    initStore();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
