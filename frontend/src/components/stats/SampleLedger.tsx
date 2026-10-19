import { cohortChips, fmtN, ledgerCounts, type StatsPivot } from '@/lib/stats';

/* THE sample ledger — the mark every chart carries above its view, in the edit
 * preview too. One line of counts (what the filter selected, what has every
 * value the chart needs, what was set aside for a missing one) over a thin
 * three-segment bar: behind a number · counted in a cell under the minimum ·
 * excluded. A chart never shows a statistic without saying how many properties
 * stand behind it and how many did not make it in. */
export default function SampleLedger({ pivot }: { pivot: StatsPivot | null }) {
  const counts = pivot ? ledgerCounts(pivot) : null;
  if (!pivot || !counts) return null;
  const chips = cohortChips(pivot);
  const { filtered, sample, excluded, withStats, underMin } = counts;

  const segments: Array<{ id: string; n: number; color: string; label: string }> =
    withStats == null || underMin == null
      ? [{ id: 'sample', n: sample, color: 'var(--color-ink-3)', label: `ve vzorku ${fmtN(sample)}` }]
      : [
          { id: 'stats', n: withStats, color: 'var(--color-ink-2)', label: `ve statistikách ${fmtN(withStats)}` },
          {
            id: 'under',
            n: underMin,
            color: 'var(--color-ink-4)',
            label: `${fmtN(underMin)} v buňkách pod ${pivot.min_n}`,
          },
        ];
  segments.push({
    id: 'excluded',
    n: excluded,
    color: 'var(--color-rule-strong)',
    label: `vyřazeno ${fmtN(excluded)}`,
  });

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-[0.75rem] leading-snug text-[var(--color-ink-3)]">
          Ve filtru <Count n={filtered} /> · vzorek <Count n={sample} /> · vyřazeno{' '}
          <Count n={excluded} />
          {excluded > 0 ? ' (chybí údaje)' : ''}
          {underMin != null && underMin > 0 ? (
            <>
              {' '}· <Count n={underMin} /> v buňkách pod {pivot.min_n}
            </>
          ) : null}
        </p>
        {chips.length > 0 && (
          <ul aria-label="Vzorek" className="ml-auto flex flex-wrap gap-1">
            {chips.map((c) => (
              <li
                key={c}
                className="rounded-[var(--radius-xs)] border border-[var(--color-rule)] px-1.5 py-px text-[0.62rem] tracking-[0.08em] uppercase text-[var(--color-ink-3)]"
              >
                {c}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div
        role="img"
        aria-label={segments.map((s) => s.label).join(', ')}
        className="flex h-[3px] w-full gap-[2px] overflow-hidden rounded-full"
      >
        {filtered > 0 &&
          segments
            .filter((s) => s.n > 0)
            .map((s) => (
              <span
                key={s.id}
                data-segment={s.id}
                className="h-full rounded-full"
                style={{ flexGrow: s.n, flexBasis: 0, background: s.color }}
              />
            ))}
      </div>
    </div>
  );
}

function Count({ n }: { n: number }) {
  return <span className="font-mono tabular-nums text-[var(--color-ink)]">{fmtN(n)}</span>;
}

/* The dashed "too few" mark: a cell under the minimum shows its n and this,
 * never a number. The same dashed vocabulary as an empty shelf slot — absence
 * is drawn, not left blank. */
export function TooFew({ minN }: { minN: number }) {
  return (
    <span className="inline-flex items-center rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)] px-1.5 py-px text-[0.65rem] leading-tight text-[var(--color-ink-3)] whitespace-nowrap">
      <span aria-hidden="true">pod {minN}</span>
      <span className="sr-only">méně než {minN} nemovitostí, bez statistik</span>
    </span>
  );
}
