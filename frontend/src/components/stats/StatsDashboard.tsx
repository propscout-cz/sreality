import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { arrayMove } from '@dnd-kit/sortable';
import type { ListingFilters } from '@/lib/filters';
import { isApiConfigured } from '@/lib/api';
import { pushToast } from '@/lib/toast';
import { cachePatch } from '@/lib/revalidate';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';
import ErrorBanner from '@/components/ErrorBanner';
import Skeleton from '@/components/Skeleton';
import SettingsRow from '@/components/SettingsRow';
import { Segmented, Switch } from '@/components/controls';
import { STATS_REGISTRY } from '@/lib/statsRegistry.generated';
import {
  MARKET_LABEL,
  MAX_CHARTS,
  fetchDashboard,
  marketCaption,
  marketSample,
  newChart,
  saveDashboard,
  statsDashboardKey,
  type ChartSpec,
  type StatsMarket,
} from '@/lib/stats';
import DismissedSkewNotice from './DismissedSkewNotice';
import StatsChart, { type CardFocus } from './StatsChart';
import DashboardBar, { type DashboardView } from './DashboardBar';

/* Browse › Stats: the account's WORKING board of charts over the current filter.
 * The filter only picks the sample; the board (which charts, in which order) is
 * ONE document per account, read through GET /stats/dashboard and written whole
 * through PUT /stats/dashboard by the one optimistic write (lib/useOptimisticWrite)
 * — the chart list's edits and the dashboard bar's loads alike. No saved row ⇒ the
 * four default charts, and the first save persists the whole list. The named
 * dashboards' bar and the sample lens lead the tab on every surface that shows it
 * (the Browse page and the explore modals alike), as two labelled rows of ONE
 * settings panel above the charts (rulings S01–S04, 2026-10-27). */
export default function StatsDashboard({
  filters,
  view,
  market,
  small,
  onIncludeDismissed,
}: {
  filters: ListingFilters;
  view?: DashboardView;
  /* The sample lens; optional only so the chart list renders on its own in tests. */
  market?: { value: StatsMarket; set: (m: StatsMarket) => void };
  /* "Malé vzorky": cells under the minimum show min, max, průměr, medián (highlighted). */
  small?: { value: boolean; set: (on: boolean) => void };
  /* Flips the sidebar to Show dismissed, from the skew warning. */
  onIncludeDismissed?: () => void;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<ChartSpec | null>(null);
  /* Where focus lands after a board-level change: the moved card's ⋯ trigger,
   * the card that took a removed one's place, a new chart's Upravit once saved. */
  const [focus, setFocus] = useState<{ id: string; req: CardFocus } | null>(null);
  const seq = useRef(0);
  const focusCard = (id: string, target: CardFocus['target']) =>
    setFocus({ id, req: { target, seq: ++seq.current } });
  const addRef = useRef<HTMLButtonElement>(null);
  /* The add tile takes focus once it is back (a discarded new chart, the last card removed). */
  const focusAdd = useRef(false);
  useEffect(() => {
    if (focusAdd.current && addRef.current) {
      focusAdd.current = false;
      addRef.current.focus();
    }
  });

  /* What every chart counts: the Browse filter, or a market lens over it. */
  const lens = market?.value ?? 'filter';
  const sample = useMemo(() => marketSample(filters, lens), [lens, filters]);

  const board = useQuery<ChartSpec[], Error>({
    queryKey: statsDashboardKey,
    queryFn: ({ signal }) => fetchDashboard({ signal }),
    staleTime: 5 * 60_000,
  });

  /* The board moves on the click; the PUT answers with the board as stored, which
   * is adopted on success, so only a failure (rolled back from onSettled) re-reads. */
  const save = useOptimisticWrite<ChartSpec[], ChartSpec[]>({
    mutationKey: ['write', 'stats', 'dashboard'],
    mutationFn: saveDashboard,
    patch: (charts) => [cachePatch<ChartSpec[]>({ queryKey: statsDashboardKey, exact: true }, () => charts)],
    revalidate: (_charts, failed) => (failed ? [statsDashboardKey] : []),
    onSuccess: (saved) => qc.setQueryData(statsDashboardKey, saved),
    onError: (err) => pushToast('err', `Grafy se nepodařilo uložit: ${err.message}`),
  });

  if (board.isLoading) {
    return (
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-2" aria-busy="true">
        <Skeleton height={192} />
        <Skeleton height={192} />
      </div>
    );
  }
  /* A failed read must not fall back to the defaults: a save from there would
   * overwrite the board the read could not see. */
  if (board.error || !board.data) {
    return (
      <ErrorBanner
        title="Grafy se nepodařilo načíst:"
        message={board.error?.message ?? 'neznámá chyba'}
        onRetry={() => void board.refetch()}
      />
    );
  }

  const charts = board.data;
  /* A new chart's save is optimistic, but its card stays the pending one (in
   * edit mode, holding the draft) until the save lands — so a refused save
   * loses nothing — and it is not listed twice meanwhile. */
  const listed = pending ? charts.filter((c) => c.id !== pending.id) : charts;
  const atCap = charts.length >= MAX_CHARTS;
  const move = (id: string, delta: -1 | 1) => {
    const from = charts.findIndex((c) => c.id === id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= charts.length) return;
    save.mutate(arrayMove(charts, from, to));
    focusCard(id, 'menu');
  };
  const remove = (id: string) => {
    const at = listed.findIndex((c) => c.id === id);
    save.mutate(charts.filter((c) => c.id !== id));
    /* Focus goes to what now stands in its place: the next card, else the add tile. */
    const heir = listed[at + 1];
    if (heir) focusCard(heir.id, 'heading');
    else focusAdd.current = true;
  };
  const discardNew = () => {
    setPending(null);
    focusAdd.current = true;
  };
  /* A chart with every cell under the minimum turns the switch on (ruling S09). */
  const showSmallOn = small ? () => small.set(true) : undefined;

  const caption = marketCaption(lens);

  return (
    <div className="space-y-4">
      <section className="@container rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5">
        <div className="divide-y divide-[var(--color-rule-soft)]">
          {/* DashboardBar draws nothing without the API: no row, no orphan label. */}
          {view && isApiConfigured() ? (
            <SettingsRow label="Dashboard">
              <DashboardBar view={view} working={charts} onLoadCharts={(next) => save.mutateAsync(next)} />
            </SettingsRow>
          ) : null}
          <SettingsRow label="Vzorek">
            {/* The lens and its sentence keep a real width, so the switch drops to its
             * own line before they are squeezed into a strip. */}
            <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
              <div className="min-w-0 flex-[1_1_18rem]">
                {market ? (
                  <Segmented
                    label="Vzorek"
                    options={(['filter', 'sold', 'all'] as const).map((m) => ({ value: m, label: MARKET_LABEL[m] }))}
                    value={market.value}
                    onChange={market.set}
                  />
                ) : null}
                <p className={`${market ? 'mt-1.5 ' : ''}text-[0.75rem] text-[var(--color-ink-3)]`}>
                  <strong className="font-semibold text-[var(--color-ink-2)]">{caption.name}:</strong> {caption.text}
                </p>
              </div>
              {small ? (
                <label
                  className="ml-auto inline-flex min-h-[1.875rem] shrink-0 items-center gap-2 text-[0.75rem] text-[var(--color-ink-2)]"
                  title={`Buňky pod ${STATS_REGISTRY.min_n} nemovitostí ukážou min, max, průměr a medián`}
                >
                  <Switch
                    on={small.value}
                    onChange={small.set}
                    ariaLabel={`Malé vzorky (pod ${STATS_REGISTRY.min_n}): min, max, průměr, medián`}
                  />
                  <span>
                    Malé vzorky{' '}
                    <span className="text-[var(--color-ink-3)]">
                      (pod <span className="font-mono tabular-nums">{STATS_REGISTRY.min_n}</span>)
                    </span>
                  </span>
                </label>
              ) : null}
            </div>
          </SettingsRow>
        </div>
      </section>
      <DismissedSkewNotice filters={sample} onInclude={onIncludeDismissed} />
      <div className="grid grid-cols-1 items-start gap-5 2xl:grid-cols-2">
        {listed.map((c, i) => (
          <StatsChart
            key={c.id}
            chart={c}
            filters={sample}
            showSmall={small?.value ?? false}
            saving={save.isPending}
            focus={focus?.id === c.id ? focus.req : null}
            onShowSmall={showSmallOn}
            onSave={(next) => save.mutateAsync(charts.map((x) => (x.id === c.id ? next : x)))}
            onRemove={() => remove(c.id)}
            onMove={{
              earlier: i > 0 ? () => move(c.id, -1) : undefined,
              later: i < listed.length - 1 ? () => move(c.id, 1) : undefined,
            }}
          />
        ))}
        {pending ? (
          <StatsChart
            key={pending.id}
            chart={pending}
            filters={sample}
            showSmall={small?.value ?? false}
            startEditing
            saving={save.isPending}
            onShowSmall={showSmallOn}
            onSave={async (next) => {
              await save.mutateAsync([...listed, next]);
              setPending(null);
              focusCard(next.id, 'edit');
            }}
            onRemove={discardNew}
            onCancelNew={discardNew}
          />
        ) : atCap ? (
          <p className="flex min-h-[8rem] items-center justify-center rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 text-center text-[0.8rem] text-[var(--color-ink-2)]">
            Sestava má nejvýš {MAX_CHARTS} grafů — další přidáte, až některý odeberete.
          </p>
        ) : (
          <button
            ref={addRef}
            type="button"
            onClick={() => setPending(newChart())}
            aria-label="Přidat graf"
            className="flex min-h-[8rem] items-center justify-center rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] text-[0.85rem] text-[var(--color-ink-3)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
          >
            + Přidat graf
          </button>
        )}
      </div>
    </div>
  );
}
