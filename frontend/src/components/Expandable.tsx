/* THE "show more": content cut to a few lines (text) or a height (a block), and one
 * toggle that opens it. It replaces four hand-rolled copies (the desktop and phone
 * descriptions, the Health message, the dedup advert text), one of which guessed
 * from a character count, and it is what collapses the property page's notes.
 *
 * The toggle shows only when the cut actually hides something, MEASURED in the
 * browser: on mount, on every size change of the content box (a narrower window
 * re-wraps the text; a hidden dialog's member gets a size once shown) and on every
 * change inside it (a note added, a text replaced). Nothing is ever removed from
 * the DOM, so find-in-page and a screen reader still reach the whole text.
 *
 * Keyboard focus that lands in the hidden part (tabbing into a cut-off note's
 * controls, an editor growing past the cut) opens it, so focus is never on
 * something the user cannot see (WCAG 2.4.11). Closing it again keeps the toggle
 * in view instead of leaving the reader a screen below what they were reading. */

import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

/* The clamps in use, as literal classes so Tailwind emits them. */
const LINE_CLAMP = { 2: 'line-clamp-2', 4: 'line-clamp-4', 6: 'line-clamp-6' } as const;

/* A block's cut fades out rather than ending mid-line. A mask, not an overlay, so
 * it works on any card background. */
const FADE = 'linear-gradient(to bottom, #000 calc(100% - 3rem), transparent)';

const DEFAULT_TOGGLE =
  'mt-2 inline-flex scroll-mt-20 items-center text-[0.8rem] text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors';

type Cut = { lines: keyof typeof LINE_CLAMP; maxHeight?: never } | { maxHeight: string; lines?: never };

export type ExpandableProps = Cut & {
  /* The toggle's visible word, closed and open. */
  words?: readonly [more: string, less: string];
  /* Its accessible name, closed and open, when the word alone does not say what it
   * opens; each must contain its word (WCAG 2.5.3), so a voice command still finds it. */
  names?: readonly [more: string, less: string];
  as?: 'div' | 'p';
  /* The wrapper (a flex row puts the toggle beside the content). */
  className?: string;
  contentClassName?: string;
  toggleClassName?: string;
  children: ReactNode;
};

export default function Expandable({
  lines,
  maxHeight,
  words = ['Zobrazit více', 'Zobrazit méně'],
  names,
  as: Content = 'div',
  className,
  contentClassName = '',
  toggleClassName = DEFAULT_TOGGLE,
  children,
}: ExpandableProps) {
  const id = useId();
  const [node, setNode] = useState<HTMLElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  /* Layout effect: the first reading lands before paint, so the toggle never pops in a frame late. */
  useLayoutEffect(() => {
    if (!node) return;
    const measure = () => setOverflows(node.scrollHeight - node.clientHeight > 1);
    measure();
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    resize?.observe(node);
    const mutation = new MutationObserver(measure);
    mutation.observe(node, { childList: true, characterData: true, subtree: true });
    return () => {
      resize?.disconnect();
      mutation.disconnect();
    };
  }, [node, expanded]);

  const toggle = () => {
    if (expanded) {
      requestAnimationFrame(() => {
        const button = toggleRef.current;
        if (button && button.getBoundingClientRect().top < 0) button.scrollIntoView({ block: 'nearest' });
      });
    }
    setExpanded(!expanded);
  };

  const collapsed = !expanded;
  const cut = collapsed && lines != null ? LINE_CLAMP[lines] : '';
  const style =
    collapsed && maxHeight != null
      ? {
          maxHeight,
          overflow: 'hidden',
          ...(overflows ? { maskImage: FADE, WebkitMaskImage: FADE } : null),
        }
      : undefined;

  return (
    <div className={className}>
      <Content
        id={id}
        ref={setNode}
        className={`${contentClassName} ${cut}`.trim()}
        style={style}
        onFocus={(e) => {
          if (expanded || !overflows || !node) return;
          if (e.target.getBoundingClientRect().bottom > node.getBoundingClientRect().bottom + 1) setExpanded(true);
        }}
      >
        {children}
      </Content>
      {(expanded || overflows) && (
        <button
          ref={toggleRef}
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          aria-label={names?.[expanded ? 1 : 0]}
          onClick={toggle}
          className={toggleClassName}
        >
          {words[expanded ? 1 : 0]}
        </button>
      )}
    </div>
  );
}
