/* The one page tab strip (operator ruling 2026-10-27, option A; the binding pattern
 * is .interface-design/system.md § Tab strip): Browse and its Explore windows, the
 * property page, a collection's members, Labeling's statuses.
 *
 * The STRIP owns the 1px rule, so it runs the container's whole content column
 * with the trailing slot (Browse's toggles) above it. The tablist scrolls sideways
 * and keeps 4px of room on every side for the global focus ring (a scroller clips
 * an outline at its padding box); its negative margins give that room back, so the
 * first label sits on the content edge and the buttons' bottom row lies ON the rule
 * (-mb-1.25 = the 4px + the rule's 1px), where the 2px copper line covers it.
 * That room hangs 4px past the strip, below the rule too, so the tablist takes no
 * pointer: the ROW (tabs and the gaps between them, exactly the strip's height)
 * takes the clicks and the sideways swipes, and content scrolling under a pinned
 * strip stays clickable right up to the rule. */
import { useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface Tab<T extends string> {
  key: T;
  label: string;
  /* Drawn as the count pill whenever present, 0 included: the caller decides when. */
  count?: string | number;
}

interface Props<T extends string> {
  tabs: ReadonlyArray<Tab<T>>;
  active: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  /* Tab ids `${idPrefix}tab-<key>`, panels `${idPrefix}panel-<key>`. */
  idPrefix?: string;
  trailing?: ReactNode;
  /* Pins under the Shell's 3.5rem bar; z-20 clears the card actions (z-10). */
  sticky?: boolean;
}

const STRIP = 'flex flex-wrap-reverse items-center gap-x-4 gap-y-2 border-b border-[var(--color-rule)]';
const LIST =
  'pointer-events-none -mx-1 -mt-1 -mb-1.25 flex min-w-0 flex-auto overflow-x-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden';
const ROW = 'pointer-events-auto flex shrink-0 gap-6';
const TAB = 'relative inline-flex h-11 shrink-0 items-center gap-2 whitespace-nowrap text-sm transition-colors';
const ON = 'text-[var(--color-ink)]';
const IDLE = 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]';
const COUNT =
  'rounded-full bg-[var(--color-inset)] px-1.5 py-0.5 font-mono text-[0.68rem] tabular-nums text-[var(--color-ink-2)]';

export default function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  ariaLabel,
  idPrefix,
  trailing,
  sticky = false,
}: Props<T>) {
  const buttons = useRef(new Map<T, HTMLButtonElement>());
  const at = Math.max(0, tabs.findIndex((t) => t.key === active));

  /* The WAI-ARIA tabs keys: arrows wrap, Home/End jump, focus follows; Tab leaves. */
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const n = tabs.length;
    const to =
      e.key === 'ArrowRight' ? (at + 1) % n
      : e.key === 'ArrowLeft' ? (at - 1 + n) % n
      : e.key === 'Home' ? 0
      : e.key === 'End' ? n - 1
      : -1;
    if (to < 0) return;
    e.preventDefault();
    const next = tabs[to].key;
    if (next !== active) onChange(next);
    buttons.current.get(next)?.focus();
  };

  return (
    <div className={sticky ? `${STRIP} sticky top-14 z-20 bg-[var(--color-paper)]` : STRIP}>
      <div role="tablist" aria-label={ariaLabel} onKeyDown={onKeyDown} className={LIST}>
        <div className={ROW}>
          {tabs.map((t, i) => {
            const on = t.key === active;
            return (
              <button
                key={t.key}
                ref={(el) => {
                  if (el) buttons.current.set(t.key, el);
                  else buttons.current.delete(t.key);
                }}
                type="button"
                role="tab"
                id={idPrefix != null ? `${idPrefix}tab-${t.key}` : undefined}
                aria-controls={idPrefix != null ? `${idPrefix}panel-${t.key}` : undefined}
                aria-selected={on}
                tabIndex={i === at ? 0 : -1}
                onClick={() => onChange(t.key)}
                className={`${TAB} ${on ? ON : IDLE}`}
              >
                {t.label}
                {t.count != null && <span className={COUNT}>{t.count}</span>}
                {on && (
                  <span
                    aria-hidden="true"
                    data-tab-indicator=""
                    className="absolute inset-x-0 bottom-0 h-0.5 bg-[var(--color-copper)]"
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
      {trailing != null && <div className="ml-auto flex shrink-0 items-center gap-2">{trailing}</div>}
    </div>
  );
}
