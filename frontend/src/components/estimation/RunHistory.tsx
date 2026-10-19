import type { ReactNode } from 'react';

import { fmtAbsolute, fmtDateSlash, fmtTime24 } from '@/lib/format';

/* The append-only ledger under a property's figures, newest first; a click
 * selects that row's card. Our runs and the Valuo valuations each name their
 * columns after the "when" every ledger starts with. */
export type HistoryColumn<T> = { header: string; right?: boolean; className?: string; cell: (row: T) => ReactNode };

const cell = (right?: boolean, extra?: string) => ['px-3 py-2', right && 'text-right', extra].filter(Boolean).join(' ');

export default function RunHistory<T extends { id: number; created_at: string }>({
  title, rows, columns, selectedId, onPick, when = 'When', viewing = 'viewing',
}: {
  title: string;
  rows: readonly T[];
  columns: ReadonlyArray<HistoryColumn<T>>;
  selectedId: number | null;
  onPick: (id: number) => void;
  when?: string;
  viewing?: string;
}) {
  return (
    <div className="mt-7">
      <div className="flex items-baseline justify-between">
        <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)] font-medium">{title}</p>
        <p className="text-[0.7rem] tracking-wide text-[var(--color-ink-4)] font-mono tabular-nums">{rows.length}</p>
      </div>
      <div className="mt-3 border border-[var(--color-rule)] rounded-[var(--radius-md)] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)] bg-[var(--color-paper-2)]">
              <th className="px-3 py-2 font-medium">{when}</th>
              {columns.map((c) => <th key={c.header} className={cell(c.right, 'font-medium')}>{c.header}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isSelected = r.id === selectedId;
              return (
                <tr
                  key={r.id}
                  onClick={() => onPick(r.id)}
                  className={[
                    'cursor-pointer border-t border-[var(--color-rule-soft)] transition-colors',
                    isSelected ? 'bg-[var(--color-copper-soft)]/60' : 'hover:bg-[var(--color-copper-soft)]/30',
                  ].join(' ')}
                >
                  <td className="px-3 py-2 tabular-nums text-[var(--color-ink-2)]" title={fmtAbsolute(r.created_at)}>
                    {fmtDateSlash(r.created_at)}{' '}
                    <span className="text-[0.7rem] text-[var(--color-ink-4)]">{fmtTime24(r.created_at)}</span>
                    {isSelected && (
                      <span className="ml-2 text-[0.6rem] tracking-[0.14em] uppercase text-[var(--color-copper)]">
                        {viewing}
                      </span>
                    )}
                  </td>
                  {columns.map((c) => <td key={c.header} className={cell(c.right, c.className)}>{c.cell(r)}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
