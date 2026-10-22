import type { Status } from '@/lib/healthChecks';

/* The one status mark: a dot in the status colour and, always, the status word (GLOSSARY.md
 * § Health). Colour alone fails here: sage and brick are 1.1:1 apart for a deuteranope, so
 * the word is the cue, visible beside the dot or `sr-only` where the layout already shows it.
 * Colour is only ever the dot: the word stays ink (ochre text fails AA on light paper).
 * States (off, idle, building, merging) are not statuses and never take this mark. */

export const STATUS_WORD: Readonly<Record<Status, string>> = {
  ok: 'OK',
  warn: 'Watch',
  fail: 'Problem',
  unknown: 'Not measured',
};

const MARK: Readonly<Record<Status, string>> = {
  ok: 'bg-[var(--color-sage)]',
  warn: 'bg-[var(--color-ochre)]',
  fail: 'bg-[var(--color-brick)]',
  // Absence, drawn as an empty ring in ink-3 (3.9:1, a legible mark); never a filled grey dot.
  unknown: 'border-[1.5px] border-[var(--color-ink-3)] bg-transparent',
};

/* sm in chips, md in rows, lg in the portal matrix. */
const SIZE = { sm: 'h-1.5 w-1.5', md: 'h-2 w-2', lg: 'h-2.5 w-2.5' } as const;

export default function StatusDot({
  status,
  size = 'md',
  word = 'visible',
}: {
  status: Status;
  size?: keyof typeof SIZE;
  word?: 'visible' | 'sr-only';
}) {
  // Only a measured verdict gets a colour; anything else is Not measured, never OK.
  const s: Status = status === 'ok' || status === 'warn' || status === 'fail' ? status : 'unknown';
  const text = STATUS_WORD[s];
  return (
    <span className="inline-flex items-center gap-1.5" title={word === 'sr-only' ? text : undefined}>
      <span aria-hidden="true" className={`inline-block shrink-0 rounded-full ${SIZE[size]} ${MARK[s]}`} />
      <span className={word === 'sr-only' ? 'sr-only' : 'whitespace-nowrap text-[var(--color-ink-2)]'}>{text}</span>
    </span>
  );
}
