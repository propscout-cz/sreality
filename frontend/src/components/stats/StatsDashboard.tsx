import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { arrayMove } from '@dnd-kit/sortable';
import type { ListingFilters } from '@/lib/filters';
import { pushToast } from '@/lib/toast';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import ErrorBanner from '@/components/ErrorBanner';
import Skeleton from '@/components/Skeleton';
import {
  MAX_CHARTS,
  fetchDashboard,
  newChart,
  saveDashboard,
  statsDashboardKey,
  type ChartSpec,
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
 * dashboards' bar leads the tab where Browse hands over its view state (`view`;
 * the explore modals do not). */
export default function StatsDashboard({
  filters,
  view,
  onIncludeDismissed,
}: {
  filters: ListingFilters;
  view?: DashboardView;
  /* Flips the sidebar to Show dismissed, from the skew warning. */
  onIncludeDismissed?: () => void;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<ChartSpec | null>(null);
  /* Where focus lands after a board-level change: the moved card's arrow, the
   * card that took a removed one's place, a new chart's Upravit once saved. */
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
    focusCard(id, delta < 0 ? 'earlier' : 'later');
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

  return (
    <div className="space-y-4">
      {view ? <DashboardBar view={view} working={charts} onLoadCharts={(next) => save.mutateAsync(next)} /> : null}
      <DismissedSkewNotice filters={filters} onInclude={onIncludeDismissed} />
      <p className="text-[0.75rem] text-[var(--color-ink-3)]">
        Každý graf počítá nemovitosti z filtru; sestava grafů je uložená k vašemu účtu.
      </p>
      <div className="grid grid-cols-1 items-start gap-5 2xl:grid-cols-2">
        {listed.map((c, i) => (
          <StatsChart
            key={c.id}
            chart={c}
            filters={filters}
            saving={save.isPending}
            focus={focus?.id === c.id ? focus.req : null}
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
            filters={filters}
            startEditing
            saving={save.isPending}
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
