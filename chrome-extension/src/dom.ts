/* The two DOM helpers every surface shares. No `document` at import, so
 * dom.test.ts runs keepFocus under node with a stand-in root. */

/* One element with its class and, optionally, its text. A button is
 * type="button", so it never submits a form of the portal page around it. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, className: string, text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text != null) node.textContent = text;
  if (tag === 'button') (node as HTMLButtonElement).type = 'button';
  return node;
}

interface FocusRoot {
  readonly activeElement: Element | null;
  querySelector<E extends Element = Element>(selectors: string): E | null;
}

type Caret = HTMLInputElement | HTMLTextAreaElement;

function hasCaret(node: Element | null): node is Caret {
  // selectionStart is null on an input type without a caret (a checkbox).
  return node != null && 'setSelectionRange' in node && (node as Caret).selectionStart != null;
}

/* Rebuild a shadow root's content without taking focus from the page: the focused
 * element (by data-key) and its caret come back ONLY when focus was inside `root`
 * before — a background render never pulls focus out of the portal page. Read
 * before the rebuild because Chrome blurs a focused element as it is removed.
 * `fallback` takes focus when the keyed element is gone from the rebuild. */
export function keepFocus(
  root: FocusRoot, rebuild: () => void, fallback?: () => HTMLElement | null,
): void {
  const active = root.activeElement as HTMLElement | null;
  const key = active?.dataset.key;
  const caret = hasCaret(active) ? { start: active.selectionStart, end: active.selectionEnd } : null;
  rebuild();
  if (key == null) return;
  const target = root.querySelector<HTMLElement>(`[data-key="${key}"]`) ?? fallback?.() ?? null;
  target?.focus({ preventScroll: true });  // the callers restore their own scroll
  if (target == null || root.activeElement !== target || !hasCaret(target)) return;
  const n = target.value.length;
  target.setSelectionRange(Math.min(caret?.start ?? n, n), Math.min(caret?.end ?? n, n));
}
