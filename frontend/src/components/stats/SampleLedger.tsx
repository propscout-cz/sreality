import { memo, useMemo } from 'react';
import { fmtCount } from '@/lib/format';
import { cohortChips, ledgerCounts, type StatsPivot } from '@/lib/stats';

/* THE sample ledger — the mark every chart carries above its view, in the edit
 * preview too. One line of counts (what the filter selected, what has every
 * value the chart needs, what was set aside for a missing one) over a thin
 * three-segment bar: behind a number · counted in a cell under the minimum ·
 * excluded. A chart never shows a statistic without saying how many properties
 * stand behind it and how many did not make it in. */
function SampleLedger({ pivot }: { pivot: StatsPivot }) {
  const counts = useMemo(() => ledgerCounts(pivot), [pivot]);
  const chips = useMemo(() => cohortChips(pivot), [pivot]);
  if (!counts) return null;
  const { filtered, sample, excluded, split } = counts;

  const segments: Array<{ id: string; n: number; color: string; label: string }> =
    split == null
      ? [{ id: 'sample', n: sample, color: 'var(--color-ink-3)', label: `ve vzorku ${fmtCount(sample)}` }]
      : [
          {
            id: 'stats',
            n: split.withStats,
            color: 'var(--color-ink-2)',
            label: `ve statistikách ${fmtCount(split.withStats)}`,
          },
          {
            id: 'under',
            n: split.underMin,
            color: 'var(--color-ink-4)',
            label: `${fmtCount(split.underMin)} v buňkách pod ${pivot.min_n}`,
          },
        ];
  segments.push({
    id: 'excluded',
    n: excluded,
    color: 'var(--color-rule-strong)',
    label: `vyřazeno ${fmtCount(excluded)}`,
  });

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-[0.75rem] leading-snug text-[var(--color-ink-3)]">
          Ve filtru <Count n={filtered} /> · vzorek <Count n={sample} /> · vyřazeno{' '}
          <Count n={excluded} />
          {excluded > 0 ? ' (chybí údaje)' : ''}
          {split && split.underMin > 0 ? (
            <>
              {' '}· <Count n={split.underMin} /> v buňkách pod {pivot.min_n}
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

export default memo(SampleLedger);

function Count({ n }: { n: number }) {
  return <span className="font-mono tabular-nums text-[var(--color-ink)]">{fmtCount(n)}</span>;
}
