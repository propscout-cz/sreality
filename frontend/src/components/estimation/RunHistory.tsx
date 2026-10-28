import { PanelGroup } from '@/components/Panel';
import { Th, cellClass, type Column } from '@/components/table';
import { fmtAbsolute, fmtDateSlash, fmtTime24 } from '@/lib/format';

/* The append-only ledger under a property's figures, newest first; a click
 * selects that row's card. Our runs and the Valuo valuations each name their
 * columns after the "when" every ledger starts with, in their own language.
 * A group inside its section card (C7, L3): a title and count over a rule-soft
 * line, never a box; its heads are L4. */
export default function RunHistory<T extends { id: number; created_at: string }>({
  title, rows, columns, selectedId, onPick, when, viewing,
}: {
  title: string;
  rows: readonly T[];
  columns: ReadonlyArray<Column<T>>;
  selectedId: number | null;
  onPick: (id: number) => void;
  when: string;
  viewing: string;
}) {
  return (
    <PanelGroup title={title} count={rows.length}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <Th size="xs">{when}</Th>
              {columns.map((c) => <Th key={c.header} size="xs" align={c.align}>{c.header}</Th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
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
                  <td className="px-3 py-2 font-mono tabular-nums text-[var(--color-ink-2)]" title={fmtAbsolute(r.created_at)}>
                    {/* The keyboard's way in: its click bubbles to the row's. */}
                    <button type="button" aria-current={isSelected || undefined} className="text-left cursor-pointer">
                      {fmtDateSlash(r.created_at)}{' '}
                      <span className="text-[0.7rem] text-[var(--color-ink-3)]">{fmtTime24(r.created_at)}</span>
                      {isSelected && (
                        <span className="ml-2 font-sans text-[0.6rem] tracking-[0.14em] uppercase text-[var(--color-copper)]">
                          {viewing}
                        </span>
                      )}
                    </button>
                  </td>
                  {columns.map((c) => <td key={c.header} className={cellClass(c)}>{c.cell(r, i)}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </PanelGroup>
  );
}
