/* The phone shell's one modal surface: a sheet from the bottom edge, where the
 * thumb already is (#1762: "the OS-native select for a stage → the SPA's stage
 * menu as a bottom sheet"). The stage menu, the collection menu and the account
 * menu all open in it, so they share one grab handle, one "Hotovo", one scrim
 * and one way to close (tap outside, Escape, Hotovo).
 *
 * Portalled to <body> so a card's `overflow-hidden` photo frame or the sticky
 * header cannot clip it. Bespoke chrome over the app's one modal contract
 * (`useDialog`), the way RunPanel and ImageLightbox are. */

import { useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { useDialog } from '@/lib/useDialog';

/* The scrim is ink in light mode and black after dusk; `light-dark()` follows the
 * html `color-scheme` the theme switch already sets. */
const SCRIM = 'light-dark(rgba(26, 28, 34, 0.32), rgba(0, 0, 0, 0.55))';
const SCROLLS = 'overflow-y-auto overscroll-contain';
const SAFE_BOTTOM = 'max(16px, env(safe-area-inset-bottom))';

export interface BottomSheetProps {
  /* The dialog's accessible name and the micro heading beside "Hotovo". */
  label: string;
  onClose: () => void;
  /* A context line under the heading (what the sheet acts on). */
  context?: ReactNode;
  /* The close word: "Hotovo" for a menu that acts as you tap, "Zavřít" for a
   * sheet that holds a draft and lets it go. */
  closeLabel?: string;
  /* Makes the sheet a TALL one (the feed's Filtry): the body scrolls between
   * the heading and this footer, both pinned. */
  footer?: ReactNode;
  children: ReactNode;
}

export default function BottomSheet({
  label,
  onClose,
  context,
  closeLabel = 'Hotovo',
  footer,
  children,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  /* The app's modal contract (lib/useDialog): the layered Escape, the focus trap
   * and restore, the ref-counted scroll lock, the z-index by nesting. */
  const { isTopLayer, zIndex } = useDialog({ onClose, panelRef: sheetRef, zRef: scrimRef });

  return createPortal(
    <div
      ref={scrimRef}
      className="fixed inset-0 flex flex-col justify-end"
      style={{ background: SCRIM, zIndex }}
      onClick={(e) => {
        if (e.target === e.currentTarget && isTopLayer()) onClose();
      }}
    >
      <div
        ref={sheetRef}
        // eslint-disable-next-line no-restricted-syntax -- bespoke chrome over lib/useDialog (see the header); the layering, trap, Escape and scroll lock all come from the hook.
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={`${footer ? 'flex h-[92dvh] flex-col' : `max-h-[85dvh] ${SCROLLS}`} rounded-t-[var(--radius-lg)] border-t border-[var(--color-rule-strong)] bg-[var(--color-paper-3)] pt-2 text-[var(--color-ink)] outline-none`}
        style={footer ? undefined : { paddingBottom: SAFE_BOTTOM }}
      >
        <div aria-hidden className="mx-auto mb-1 h-1 w-9 rounded-[2px] bg-[var(--color-ink-4)]" />
        <div className="flex h-10 items-center justify-between gap-2 pl-4 pr-2">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">
            {label}
          </span>
          <button
            type="button"
            onClick={onClose}
            data-dialog-close
            className="h-10 px-2 text-[15px] font-medium text-[var(--color-copper)]"
          >
            {closeLabel}
          </button>
        </div>
        {context}
        {footer ? (
          <>
            <div className={`min-h-0 flex-1 ${SCROLLS}`}>{children}</div>
            <div className="border-t border-[var(--color-rule)] pt-3" style={{ paddingBottom: SAFE_BOTTOM }}>
              {footer}
            </div>
          </>
        ) : (
          children
        )}
      </div>
    </div>,
    document.body,
  );
}

/* The line under the heading: what the sheet is about, the figure in mono. */
export function SheetContext({ text, figure }: { text: string; figure?: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--color-rule-soft)] px-4 pb-3 text-[13px] text-[var(--color-ink-2)]">
      <span className="min-w-0 truncate">{text}</span>
      {figure && (
        <span className="whitespace-nowrap font-mono text-[12px] tabular-nums text-[var(--color-ink)]">
          {figure}
        </span>
      )}
    </div>
  );
}
