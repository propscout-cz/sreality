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
 * it works on any card background. What sits in the fade counts as hidden. */
const FADE_PX = 48;
const FADE = `linear-gradient(to bottom, #000 calc(100% - ${FADE_PX}px), transparent)`;

/* Focus is hidden when its row (a list item, else the element itself) ends below the
 * cut, the fade included: an editor opened near the cut puts its Save there. */
function belowCut(node: HTMLElement, el: Element, fades: boolean): boolean {
  const row = el.closest('li');
  const unit = row && node.contains(row) ? row : el;
  const edge = node.getBoundingClientRect().bottom - (fades ? FADE_PX : 0);
  return unit.getBoundingClientRect().bottom > edge + 1;
}

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
  /* Each surface draws its toggle in its own voice; only the behaviour is shared. */
  toggleClassName: string;
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
  toggleClassName,
  children,
}: ExpandableProps) {
  const id = useId();
  const [node, setNode] = useState<HTMLElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  /* Measured only while cut: open, nothing overflows, and a reading then would drop the
   * toggle the user just pressed (and their focus with it). The layout effect lands the
   * first reading before paint, so the toggle never pops in a frame late. A change inside
   * that pushes the focused row below the cut (an editor's buttons, an error line) opens it. */
  const fades = maxHeight != null;
  useLayoutEffect(() => {
    if (!node || expanded) return;
    const measure = () => {
      const cut = node.scrollHeight - node.clientHeight > 1;
      setOverflows(cut);
      const focused = document.activeElement;
      if (cut && focused && focused !== node && node.contains(focused) && belowCut(node, focused, fades)) setExpanded(true);
    };
    measure();
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    resize?.observe(node);
    const mutation = new MutationObserver(measure);
    mutation.observe(node, { childList: true, characterData: true, subtree: true });
    return () => {
      resize?.disconnect();
      mutation.disconnect();
    };
  }, [node, expanded, fades]);

  /* Closing a long read leaves the toggle where the reader is: brought into view below the
   * sticky header (its scroll margin) when the content above it shrank away. */
  const toggle = () => {
    if (expanded) requestAnimationFrame(() => toggleRef.current?.scrollIntoView?.({ block: 'nearest' }));
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
          if (!expanded && overflows && node && belowCut(node, e.target, fades)) setExpanded(true);
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
          style={{ scrollMarginTop: '5rem' }}
        >
          {words[expanded ? 1 : 0]}
        </button>
      )}
    </div>
  );
}
