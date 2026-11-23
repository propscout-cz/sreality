import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchBrokerDossier, searchBrokersByName, type BrokerPublic } from '@/lib/brokers';
import { fmtCount } from '@/lib/format';
import { useDebouncedTerm } from '@/lib/useDebouncedTerm';

/* The broker scope (ListingFilters.brokerId, MS19: the properties where one of
 * this broker's ads matches the portal and status filters) picked by name in the
 * filter sidebar and the phone's Filtry sheet. A pick NARROWS the current filters;
 * the explore-broker modal stays the way to open a broker's whole book. The
 * picked broker's name is cached under its id at pick time, so a folded band or
 * the phone does not refetch it; a `?broker=` link reads it once from the dossier. */

interface BrokerLabel {
  name: string;
  firm: string | null;
}

const labelOf = (b: BrokerPublic): BrokerLabel => ({
  name: b.display_name ?? 'Neznámý makléř',
  firm: b.firm_name ?? b.firm_domain ?? null,
});

export const brokerLabelKey = (id: number) => ['broker-label', id] as const;

export default function BrokerFilterControl({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const qc = useQueryClient();
  const [q, debounced, setQ] = useDebouncedTerm();
  const [open, setOpen] = useState(false);

  const labelQ = useQuery({
    queryKey: brokerLabelKey(value ?? 0),
    queryFn: async () => {
      const d = await fetchBrokerDossier(value as number);
      return d ? labelOf(d.broker) : null;
    },
    enabled: value != null,
    staleTime: Infinity,
  });
  const resultsQ = useQuery({
    queryKey: ['broker-name-search', debounced],
    queryFn: () => searchBrokersByName(debounced),
    enabled: value == null && debounced.length >= 2,
    staleTime: 60_000,
  });

  if (value != null) {
    const label = labelQ.data;
    const name = label?.name ?? (labelQ.isLoading ? 'Loading…' : `Broker #${value}`);
    return (
      <div className="flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--color-copper)] bg-[var(--color-copper-soft)] px-2.5 py-1.5">
        <span className="min-w-0">
          <span className="block truncate text-sm text-[var(--color-ink)]">{name}</span>
          {label?.firm ? (
            <span className="block truncate text-xs text-[var(--color-ink-3)]">{label.firm}</span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={() => onChange(null)}
          aria-label={`Remove broker ${name}`}
          className="shrink-0 px-1 text-[var(--color-ink-3)] hover:text-[var(--color-brick)] transition-colors"
        >
          ×
        </button>
      </div>
    );
  }

  const results = resultsQ.data ?? [];
  const pick = (b: BrokerPublic) => {
    qc.setQueryData(brokerLabelKey(b.broker_id), labelOf(b));
    setQ('');
    setOpen(false);
    onChange(b.broker_id);
  };
  return (
    <div className="relative">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        aria-label="Broker"
        placeholder="Search a broker by name…"
        className="w-full text-sm border border-[var(--color-rule)] rounded-[var(--radius-sm)] bg-[var(--color-inset)] px-3 py-2 text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]"
      />
      {open && debounced.length >= 2 && (
        <div className="absolute z-20 mt-1 w-full max-h-72 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-3)] shadow-sm">
          {resultsQ.isLoading ? (
            <p className="px-3 py-2 text-sm text-[var(--color-ink-3)]">Searching…</p>
          ) : resultsQ.isError ? (
            /* A failed search is not an empty one. */
            <p className="px-3 py-2 text-sm text-[var(--color-brick)]">
              Search failed: {(resultsQ.error as Error).message}
            </p>
          ) : results.length === 0 ? (
            <p className="px-3 py-2 text-sm text-[var(--color-ink-4)]">No broker found.</p>
          ) : (
            results.map((b) => (
              <button
                key={b.broker_id}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(b)}
                className="w-full text-left px-3 py-2 flex items-center justify-between gap-3 border-b border-[var(--color-rule-soft)] last:border-0 hover:bg-[var(--color-copper-soft)]"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm text-[var(--color-ink)]">{labelOf(b).name}</span>
                  <span className="block truncate text-xs text-[var(--color-ink-3)]">
                    {labelOf(b).firm ?? 'nezávislý'}
                  </span>
                </span>
                {/* The live properties the search orders by. */}
                <span className="shrink-0 text-xs font-[family-name:var(--font-mono)] tabular-nums text-[var(--color-ink-3)]">
                  {fmtCount(b.active_properties)}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
