/* Dragging the edge tab up and down its window, and where it was left (one place for
 * every tab and portal). A lookup or a store change can repaint the tab under a live
 * press, so the press hangs on its stable parent, `box`, and a fast first move can land
 * off the tab, so the moves are read on the window. The pointer is captured only past
 * the slop: captured from the press, a plain click would land on `box`, not the tab. */

export const TOP_KEY = 'edgeTabTop';
const DEFAULT_TOP = 96;
const SLOP_PX = 4;

export const storedTop = (value: unknown): number => (typeof value === 'number' ? value : DEFAULT_TOP);

export async function readTop(): Promise<number> {
  try {
    return storedTop((await chrome.storage.local.get([TOP_KEY]))[TOP_KEY]);
  } catch {
    return DEFAULT_TOP;  // an orphaned script
  }
}

export function writeTop(top: number): void {
  try {
    void chrome.storage.local.set({ [TOP_KEY]: top }).catch(() => {});
  } catch {
    /* an orphaned script: the tab still moved on this page */
  }
}

export function dragTab(box: HTMLElement, move: (top: number) => void, drop: () => void): void {
  let press: { id: number; y: number; top: number } | null = null;
  let dragged = false;

  const release = (): void => {
    press = null;
    window.removeEventListener('pointermove', onMove, true);
    window.removeEventListener('pointerup', onEnd, true);
    window.removeEventListener('pointercancel', onEnd, true);
  };
  function onMove(e: PointerEvent): void {
    if (press == null || e.pointerId !== press.id) return;
    if (e.buttons === 0) {  // released outside the window, inside the slop
      release();
      return;
    }
    const dy = e.clientY - press.y;
    if (!dragged) {
      if (Math.abs(dy) < SLOP_PX) return;
      dragged = true;
      box.setPointerCapture(e.pointerId);
      box.classList.add('dragging');
    }
    move(press.top + dy);
  }
  function onEnd(e: PointerEvent): void {
    if (press == null || e.pointerId !== press.id) return;
    release();
    if (!dragged) return;
    box.classList.remove('dragging');
    drop();
    // The click that ends a drag fires right after this; later clicks are the user's.
    setTimeout(() => { dragged = false; }, 0);
  }

  box.addEventListener('pointerdown', (e) => {
    const tab = box.firstElementChild;
    if (e.button !== 0 || tab == null) return;
    press = { id: e.pointerId, y: e.clientY, top: tab.getBoundingClientRect().top };
    window.addEventListener('pointermove', onMove, true);
    window.addEventListener('pointerup', onEnd, true);
    window.addEventListener('pointercancel', onEnd, true);
  });
  box.addEventListener('click', (e) => { if (dragged) e.stopPropagation(); }, true);
}
