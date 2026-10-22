/* The property page's estimation chapter — the property page is the primary
 * surface for estimations, so everything the old standalone estimation page
 * showed for a linked run renders HERE.
 *
 * Structure: two reference figures side by side — the state's number (MF
 * Cenová mapa reference rent) and ours (the selected run's comparables-based
 * estimate) — then the selected run's full body (yield calculator, re-run,
 * deep-detail popup), then the append-only ledger of every run on any of the
 * property's child listings. ?run=ID selects a run (deep-links from the
 * estimations list and /estimation/:id redirects land here); the latest run
 * is the default. */
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { estimationKeys, submitEstimation, useEstimationRuns } from '@/lib/queries';
import { ApiError } from '@/lib/api';
import { buildRerunPayload, type RerunInput } from '@/lib/rerun';
import { fmtCzk, fmtPct, fmtRelative } from '@/lib/format';
import EstimateCard, { PerMonth } from '@/components/estimation/EstimateCard';
import { MfReferenceCard, YieldFigure } from '@/components/estimation/MfReferenceCard';
import RunHistory from '@/components/estimation/RunHistory';
import { mfShape } from '@/lib/mfReference';
import {
  ConfidencePill,
  RunBody,
  RunStatusChip,
} from '@/components/estimation/RunPanel';
import {
  useNewEstimationModal,
  type NewEstimationPrefill,
} from '@/components/NewEstimationModal';
import type { PropertyPublic } from '@/lib/queries';
import type { EstimationRun } from '@/lib/types';
import { Hairline, SectionLabel } from '@/components/section';
import type { Column } from '@/components/table';

export default function EstimationsBlock({
  listing,
  listingIds,
  prefill,
  readOnly = false,
  bare = false,
  label = 'Estimates',
}: {
  /* The property as its header shows it, MF included: one result per
   * property, the same row Browse, the kanban and the extension read. */
  listing: PropertyPublic;
  /* Every advert of the property as SURROGATE listings.id values (falls back to
   * the canonical advert until the advert list loads) — runs are fetched
   * property-grain. Never sreality_id: that is NULL for every non-sreality
   * listing, and an array holding one null used to serialize to an empty query
   * param, which the API read as "no filter" and answered with the whole table. */
  listingIds: number[];
  prefill?: NewEstimationPrefill;
  /* The phone (#1760 Q10): the runs are read, never started — no "Run an
   * estimation", no re-run; the detail popup still opens. */
  readOnly?: boolean;
  /* No leading hairline: the phone layout separates its sections itself. */
  bare?: boolean;
  label?: string;
}) {
  const runsQ = useEstimationRuns(listingIds);

  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const runParam = params.get('run');

  const runs = runsQ.data?.data ?? [];
  const selected =
    runs.find((r) => runParam != null && String(r.id) === runParam)
    ?? runs[0]
    ?? null;

  const qc = useQueryClient();
  const rerunMut = useMutation<EstimationRun, ApiError, RerunInput>({
    mutationFn: ({ run, overrides }) =>
      submitEstimation(buildRerunPayload(run, overrides)),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: estimationKeys.all });
      selectRun(run.id);
    },
  });

  const selectRun = (id: number) => {
    const sp = new URLSearchParams(location.search);
    sp.set('run', String(id));
    // navigate (not setSearchParams) so a lingering #feedback hash from a
    // deep-link doesn't re-open the detail popup on every selection.
    navigate(`?${sp.toString()}#estimations`, { replace: true });
  };

  // #estimations deep-links (estimations list, /estimation/:id redirects)
  // scroll the section into view once, after the runs arrive.
  const sectionRef = useRef<HTMLElement | null>(null);
  const scrolledRef = useRef(false);
  const wantsScroll =
    location.hash === '#estimations' || location.hash === '#feedback';
  useEffect(() => {
    if (!wantsScroll || scrolledRef.current) return;
    if (runsQ.isLoading) return;
    scrolledRef.current = true;
    const raf = requestAnimationFrame(() => {
      sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return () => cancelAnimationFrame(raf);
  }, [wantsScroll, runsQ.isLoading]);

  // The property's MF result, rendered by shape — the one source; a run's
  // frozen reference rent belongs to that run's own page, not to this card.
  const hasMf = mfShape(listing.mf_reference_rent).kind !== 'none';

  // Nothing to say: no MF result, no runs. The section disappears entirely
  // (e.g. land parcels) rather than rendering an empty shell.
  if (!hasMf && runs.length === 0) return null;

  const openedViaFeedbackHash =
    location.hash === '#feedback'
    && selected != null
    && runParam != null
    && String(selected.id) === runParam;

  return (
    <>
      {!bare && <Hairline />}
      <section id="estimations" ref={sectionRef} className="scroll-mt-6">
        <div className="flex items-baseline justify-between">
          <SectionLabel>{label}</SectionLabel>
          <p className="text-[0.7rem] tracking-wide text-[var(--color-ink-4)] font-mono tabular-nums">
            {runs.length === 0
              ? ''
              : `${runs.length} ${runs.length === 1 ? 'run' : 'runs'}`}
          </p>
        </div>

        {/* Two authorities, side by side: the ministry's reference figure
            and our comparables-based estimate. */}
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <MfReferenceCard
            refRent={listing.mf_reference_rent}
            yieldPct={listing.mf_gross_yield_pct}
          />
          {selected ? (
            <RunSummaryCard run={selected} />
          ) : (
            <NoRunsCard prefill={prefill} loading={runsQ.isLoading} readOnly={readOnly} />
          )}
        </div>

        {selected && (
          <div className="mt-7">
            <RunBody
              key={selected.id}
              run={selected}
              subject={listing}
              embedded
              initialDetailOpen={openedViaFeedbackHash}
              onRerun={
                readOnly
                  ? undefined
                  : (overrides) => rerunMut.mutate({ run: selected, overrides })
              }
              rerunPending={rerunMut.isPending}
              rerunError={rerunMut.error}
            />
          </div>
        )}

        {runs.length > 1 && (
          <RunHistory title="All runs" rows={runs} columns={RUN_COLUMNS}
            selectedId={selected?.id ?? null} onPick={selectRun} when="When" viewing="viewing" />
        )}
      </section>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* The "our estimate" card — mirrors the MF card's compact structure          */
/* -------------------------------------------------------------------------- */

function RunSummaryCard({ run }: { run: EstimationRun }) {
  const kind = run.estimate_kind ?? 'rent';
  const isSale = kind === 'sale';
  const value = isSale ? run.estimated_sale_price_czk : run.estimated_monthly_rent_czk;
  const [p25, p75] = isSale ? [run.sale_p25_czk, run.sale_p75_czk] : [run.rent_p25_czk, run.rent_p75_czk];
  const inFlight = run.status === 'pending' || run.status === 'running';

  return (
    <EstimateCard
      tone="ours"
      eyebrow={`Náš odhad · ${run.mode === 'agent' ? 'agentní analýza' : 'komparativní'}`}
      aside={run.status !== 'success' && <RunStatusChip status={run.status} />}
    >
      <div className="mt-1 flex items-baseline justify-between gap-3">
        <span className="text-lg font-medium tabular-nums">
          {inFlight ? (
            <span className="text-[var(--color-ink-3)]">Estimating…</span>
          ) : value != null ? (
            <>
              {fmtCzk(value)}
              {!isSale && <PerMonth />}
            </>
          ) : (
            <span className="text-[var(--color-ink-3)]">—</span>
          )}
        </span>
        {run.gross_yield_pct != null && (
          <YieldFigure>{fmtPct(run.gross_yield_pct, { digits: 2 })}</YieldFigure>
        )}
      </div>
      {p25 != null && p75 != null && (
        <p className="mt-1.5 text-[0.72rem] text-[var(--color-ink-3)] tabular-nums">
          rozpětí {fmtCzk(p25)} – {fmtCzk(p75)}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <ConfidencePill confidence={run.confidence} />
      </div>
      <p className="mt-2 text-[0.58rem] text-[var(--color-ink-4)]">
        run #{run.id} · {fmtRelative(run.created_at)}
        {run.skill_name ? ` · ${run.skill_name}` : ''}
      </p>
    </EstimateCard>
  );
}

function NoRunsCard({
  prefill,
  loading,
  readOnly,
}: {
  prefill?: NewEstimationPrefill;
  loading: boolean;
  readOnly: boolean;
}) {
  const { open } = useNewEstimationModal();
  return (
    <EstimateCard tone="absent" eyebrow="Náš odhad" className="flex flex-col items-start justify-center gap-2">
      {loading ? (
        <p className="text-sm text-[var(--color-ink-3)]">Loading runs…</p>
      ) : (
        <>
          <p className="text-sm text-[var(--color-ink-3)]">
            No estimation runs for this property yet.
          </p>
          {!readOnly && prefill && (
          <button
            type="button"
            onClick={() => open(prefill)}
            className="text-sm text-[var(--color-copper)] hover:underline underline-offset-2"
          >
            Run an estimation →
          </button>
          )}
        </>
      )}
    </EstimateCard>
  );
}

/* The runs' ledger columns, after its "when". */
const RUN_COLUMNS: ReadonlyArray<Column<EstimationRun>> = [
  { header: 'Mode', className: 'text-[0.8rem] text-[var(--color-ink-3)]', cell: (r) => `${r.mode}${r.estimate_kind === 'sale' ? ' · sale' : ''}` },
  {
    header: 'Estimate', align: 'right', className: 'font-mono tabular-nums text-[var(--color-ink)]',
    cell: (r) => {
      const isSale = r.estimate_kind === 'sale';
      const value = isSale ? r.estimated_sale_price_czk : r.estimated_monthly_rent_czk;
      if (value == null) return <span className="text-[var(--color-ink-4)]">—</span>;
      return <>{fmtCzk(value)}{!isSale && <PerMonth unit="/mo" />}</>;
    },
  },
  { header: 'Yield', align: 'right', className: 'font-mono tabular-nums text-[var(--color-ink-2)]', cell: (r) => fmtPct(r.gross_yield_pct, { digits: 2 }) },
  { header: 'Status', cell: (r) => <RunStatusChip status={r.status} /> },
];
