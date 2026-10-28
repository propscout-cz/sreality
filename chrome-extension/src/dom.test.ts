/* `npm test` (node --test). Invariant 8 of store.ts: a render never steals focus
 * from the portal page — keepFocus against a stand-in shadow root. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { keepFocus } from './dom.ts';

class FakeEl {
  focused = 0;
  selection: [number, number] | null = null;
  selectionStart: number | null = null;
  selectionEnd: number | null = null;
  root: FakeRoot;
  dataset: Record<string, string>;
  value: string;
  constructor(root: FakeRoot, dataset: Record<string, string>, value = '') {
    this.root = root;
    this.dataset = dataset;
    this.value = value;
  }
  focus(): void {
    this.focused++;
    this.root.activeElement = this;
  }
  setSelectionRange(start: number, end: number): void {
    this.selection = [start, end];
  }
}

class FakeRoot {
  activeElement: FakeEl | null = null;
  children: FakeEl[] = [];
  querySelector(selector: string): FakeEl | null {
    const key = /\[data-key="([^"]+)"\]/.exec(selector)?.[1];
    return this.children.find((c) => c.dataset.key === key) ?? null;
  }
}

type Root = Parameters<typeof keepFocus>[0];
const asRoot = (r: FakeRoot): Root => r as unknown as Root;

test('focus on the portal page stays there: a rebuild focuses nothing', () => {
  const root = new FakeRoot();
  const rebuilt = new FakeEl(root, { key: 'note' });
  keepFocus(asRoot(root), () => { root.children = [rebuilt]; });
  assert.equal(rebuilt.focused, 0);
  assert.equal(root.activeElement, null);
});

test('focus inside comes back to the rebuilt element with the same key, caret kept', () => {
  const root = new FakeRoot();
  const typing = new FakeEl(root, { key: 'note' }, 'Volat v pondělí');
  typing.selectionStart = 5;
  typing.selectionEnd = 5;
  root.children = [typing];
  root.activeElement = typing;
  const rebuilt = new FakeEl(root, { key: 'note' }, 'Volat v pondělí');
  rebuilt.selectionStart = 0;
  keepFocus(asRoot(root), () => { root.children = [rebuilt]; root.activeElement = null; });
  assert.equal(rebuilt.focused, 1);
  assert.deepEqual(rebuilt.selection, [5, 5]);
});

test('a focused element with no key, or one gone from the rebuild, is not chased', () => {
  const root = new FakeRoot();
  root.activeElement = new FakeEl(root, {});
  const other = new FakeEl(root, { key: 'coll-1' });
  keepFocus(asRoot(root), () => { root.children = [other]; });
  assert.equal(other.focused, 0);

  root.activeElement = new FakeEl(root, { key: 'coll-9' });
  const fallback = new FakeEl(root, {});
  keepFocus(asRoot(root), () => { root.children = [other]; }, () => fallback as unknown as HTMLElement);
  assert.equal(other.focused, 0);
  assert.equal(fallback.focused, 1);
});
