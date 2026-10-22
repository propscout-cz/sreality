import type { ReactNode } from 'react';

/* The cells of the app's hand-rolled tables. Each table used to carry its own
 * copy, so a class tweak landed in one and missed the other seven.
 *
 * Three sizes because the tables sit at three depths: full-page (estimations,
 * watchdogs) on the page gutter, the collection table inside a panel, the run
 * panel's comparables table one level deeper again. */
const TH_SIZE = {
  md: 'px-4 py-2.5 text-[0.7rem]',
  sm: 'px-3 py-2.5 text-[0.7rem]',
  xs: 'px-3 py-2 text-[0.65rem]',
} as const;

export function Th({
  align = 'left',
  size = 'md',
  children,
}: {
  align?: 'left' | 'right';
  size?: keyof typeof TH_SIZE;
  children: ReactNode;
}) {
  return (
    <th
      scope="col"
      className={[
        TH_SIZE[size],
        'tracking-[0.14em] uppercase font-medium text-[var(--color-ink-3)]',
        align === 'right' ? 'text-right' : 'text-left',
      ].join(' ')}
    >
      {children}
    </th>
  );
}

/* One column of a table described as data (the property page's ledgers and comparables): its header, the
 * alignment its header and cells share, its cells' classes. */
export type Column<T> = { header: string; align?: 'left' | 'right'; className?: string; cell: (row: T, i: number) => ReactNode };
export const cellClass = (c: Pick<Column<unknown>, 'align' | 'className'>, base = 'px-3 py-2') =>
  [base, c.align === 'right' && 'text-right', c.className].filter(Boolean).join(' ');
