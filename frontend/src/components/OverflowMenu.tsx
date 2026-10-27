/* The ⋯ menu for a row's or a card's secondary actions (the redesign's P14 chart
 * card, P24 watchdog row, P31 collection header): an APG menu button whose list
 * hangs off it in the shared <AnchoredPopover>, drawn like SavedSetBar's chip menu.
 *
 * The popover owns focus in and out, Escape and outside presses; this file adds the
 * menu roles, the arrow keys and Tab. A destructive item's confirm stays with its
 * caller: choosing only closes the menu and calls the item. */

import { useCallback, useId, useRef, useState, type KeyboardEvent } from 'react';
import { flushSync } from 'react-dom';
import AnchoredPopover from '@/components/AnchoredPopover';

export interface OverflowMenuItem {
  label: string;
  onSelect: () => void;
  /* Destructive: reads in brick. */
  danger?: boolean;
  /* Stays focusable (APG) but cannot be chosen. */
  disabled?: boolean;
}

export interface OverflowMenuProps {
  /* The trigger's accessible name and tooltip, e.g. "Další akce". */
  label: string;
  items: OverflowMenuItem[];
}

export default function OverflowMenu({ label, items }: OverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerId = useId();
  const menuId = useId();
  /* Stable: AnchoredPopover re-subscribes its listeners whenever onClose changes. */
  const close = useCallback(() => setOpen(false), []);

  const entries = () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

  const onTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowDown') return;
    e.preventDefault();
    if (open) entries()[0]?.focus();
    else setOpen(true);
  };

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Tab') {
      /* APG: Tab leaves the menu from any item, not only past its last one. Focus
       * goes to the trigger first, so the browser's own Tab steps on from there. */
      triggerRef.current?.focus({ preventScroll: true });
      close();
      return;
    }
    const list = entries();
    const at = list.indexOf(document.activeElement as HTMLElement);
    const next =
      e.key === 'ArrowDown' ? (at + 1) % list.length
      : e.key === 'ArrowUp' ? (at - 1 + list.length) % list.length
      : e.key === 'Home' ? 0
      : e.key === 'End' ? list.length - 1
      : null;
    if (next == null) return;
    e.preventDefault();
    list[next]?.focus();
  };

  const choose = (item: OverflowMenuItem) => {
    if (item.disabled) return;
    /* Commit the close (and the popover's focus hand-back) before the item runs, so
     * a caller's confirm, or the focus it moves, lands on a closed menu. */
    flushSync(close);
    item.onSelect();
  };

  return (
    <>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={label}
        title={label}
        className={[
          'inline-flex items-center rounded-[var(--radius-sm)] border px-1.5 py-1 text-[0.8rem] transition-colors',
          open
            ? 'border-[var(--color-rule-strong)] text-[var(--color-ink)]'
            : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)]',
        ].join(' ')}
      >
        ⋯
      </button>
      {open && (
        <AnchoredPopover
          anchorRef={triggerRef}
          onClose={close}
          /* SavedSetBar's menu surface over the popover's own paper-3 chrome. */
          className="min-w-[10rem] py-1 rounded-[var(--radius-sm)]! border-[var(--color-rule)]! bg-[var(--color-paper)]!"
        >
          <div ref={menuRef} id={menuId} role="menu" aria-labelledby={triggerId} onKeyDown={onMenuKeyDown}>
            {items.map((item, i) => (
              <button
                key={i}
                type="button"
                role="menuitem"
                tabIndex={-1}
                aria-disabled={item.disabled || undefined}
                onClick={() => choose(item)}
                className={[
                  'block w-full px-3 py-1.5 text-left text-[0.8rem]',
                  item.disabled
                    ? `cursor-not-allowed opacity-50 ${item.danger ? 'text-[var(--color-brick)]' : 'text-[var(--color-ink-2)]'}`
                    : item.danger
                      ? 'text-[var(--color-brick)] hover:bg-[var(--color-brick-soft)]'
                      : 'text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]',
                ].join(' ')}
              >
                {item.label}
              </button>
            ))}
          </div>
        </AnchoredPopover>
      )}
    </>
  );
}
