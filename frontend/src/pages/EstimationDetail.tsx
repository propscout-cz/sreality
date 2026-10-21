/* The standalone estimation page is a FALLBACK surface, not a primary one.
 *
 * A run of an ad we hold — on any portal — lives on that ad's page, so this
 * route immediately redirects there (runSurfaceUrl), and old /estimation/:id
 * links keep working. What renders here is only a run that never had an ad:
 * the historic pasted-URL and spec runs (an estimation is of an ad since
 * 2026-10-20). Its subject is synthesized from the stored spec, and the run
 * body is the same shared RunBody the listing page embeds. */
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import { useMutation, useQuery } from '@tanstack/react-query';
import { estimationKeys, fetchEstimation, submitEstimation } from '@/lib/queries';
import { fmtAbsolute, fmtRelative } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';
import { ApiError } from '@/lib/api';
import { hasListingSurface, runSurfaceUrl } from '@/lib/runLinks';
import { buildRerunPayload, type RerunInput } from '@/lib/rerun';
import { ListingOverview } from '@/components/listing-detail/ListingOverview';
import { ConfidencePill, RunBody, SourceBadge } from '@/components/estimation/RunPanel';
import type { EstimationRun, ListingPublic } from '@/lib/types';
import { Hairline } from '@/components/section';

export default function EstimationDetail() {
  const { id: idParam } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const id = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null;

  const runQ = useQuery<EstimationRun, Error>({
    queryKey: id != null ? estimationKeys.detail(id) : ['estimations', 'detail', null],
    queryFn: () => fetchEstimation(id as number),
    enabled: id != null,
    staleTime: 60_000,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === 'pending' || status === 'running' ? 2000 : false;
    },
  });

  const rerunMut = useMutation<EstimationRun, ApiError, RerunInput>({
    mutationFn: ({ run, overrides }) =>
      submitEstimation(buildRerunPayload(run, overrides)),
    onSuccess: (run) => navigate(runSurfaceUrl(run)),
  });

  // Above the early returns (hook-order). Falls back to the route's
  // "Estimation" handle while loading / on invalid id.
  usePageTitle(runQ.data ? `Estimation · run #${runQ.data.id}` : null);

  if (id == null) {
    return <NotFoundState reason="invalid" id={idParam ?? null} />;
  }

  if (runQ.isLoading) {
    return (
      <Page>
        <Crumb />
        <div className="mt-8 text-sm text-[var(--color-ink-3)]">Loading…</div>
      </Page>
    );
  }

  if (runQ.error) {
    const err = runQ.error;
    if (err instanceof ApiError && err.status === 404) {
      return <NotFoundState reason="missing" id={String(id)} />;
    }
    return (
      <Page>
        <Crumb />
        <div className="mt-8 text-sm text-[var(--color-brick)]">
          Failed to load: {err.message}
        </div>
      </Page>
    );
  }

  const run = runQ.data!;

  // A run of an ad lives on the ad's page — redirect, preserving a #feedback
  // deep-link so the section opens the detail popup there.
  if (hasListingSurface(run)) {
    const hash = location.hash === '#feedback' ? '#feedback' : '#estimations';
    return <Navigate to={runSurfaceUrl(run, hash)} replace />;
  }

  const listing = subjectAsListing(run);

  return (
    <Page>
      <Crumb />
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
        <div className="flex items-center gap-3 min-w-0">
          <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
            Estimation · run #{run.id}
          </p>
          <span
            className="text-[0.72rem] text-[var(--color-ink-4)]"
            title={fmtAbsolute(run.created_at)}
          >
            {fmtRelative(run.created_at)}
          </span>
          <span className="text-[0.72rem] text-[var(--color-ink-4)]">
            not in our database
          </span>
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          <ConfidencePill confidence={run.confidence} />
          <SourceBadge source={run.source} />
        </div>
      </div>
      {listing ? (
        <ListingOverview listing={listing} showStatus={false} />
      ) : (
        <p className="mt-5 text-sm text-[var(--color-ink-3)]">
          No subject listing details available.
        </p>
      )}
      <Hairline />
      <RunBody
        key={run.id}
        run={run}
        subject={null}
        onRerun={(overrides) => rerunMut.mutate({ run, overrides })}
        rerunPending={rerunMut.isPending}
        rerunError={rerunMut.error}
      />
    </Page>
  );
}

/* The adless subject as a ListingPublic: a synthetic row built from the stored
 * spec, so it renders through the shared overview. (A run of an ad never
 * reaches this — it redirects to the ad's page above.) */
function subjectAsListing(run: EstimationRun): ListingPublic | null {
  const spec = run.input_spec;
  if (!spec) return null;
  return {
    sreality_id: run.input_sreality_id ?? 0,
    is_active: true,
    last_seen_at: run.created_at,
    area_m2: spec.area_m2,
    disposition: spec.disposition,
    /* No ad, so no listing_location row to label it from. */
    display_label: null,
    lat: spec.lat,
    lng: spec.lng,
    floor: spec.floor,
  } as unknown as ListingPublic;
}

/* -------------------------------------------------------------------------- */
/* Layout primitives                                                          */
/* -------------------------------------------------------------------------- */

function Page({ children }: { children: React.ReactNode }) {
  return <div className="px-6 py-8 max-w-5xl mx-auto">{children}</div>;
}

function Crumb() {
  return (
    <Link
      to={ROUTES.estimations.build()}
      className="inline-flex items-center gap-1.5 text-[0.75rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
    >
      <BackArrow />
      <span>Back to estimations</span>
    </Link>
  );
}

function BackArrow() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <polyline
        points="5.5,1.5 1.5,5 5.5,8.5"
        stroke="currentColor"
        strokeWidth="1.25"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line
        x1="1.5" y1="5" x2="9" y2="5"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* 404 / not-found                                                            */
/* -------------------------------------------------------------------------- */

function NotFoundState({ reason, id }: { reason: 'invalid' | 'missing'; id: string | null }) {
  const headline = reason === 'invalid'
    ? "That doesn't look like a run id."
    : `No estimation #${id ?? '?'} in our database.`;
  return (
    <Page>
      <Crumb />
      <div className="mt-12 text-center">
        <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
          Not found
        </p>
        <h1
          className="mt-2 text-2xl"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          {headline}
        </h1>
        <Link
          to={ROUTES.estimations.build()}
          className="mt-4 inline-block text-sm text-[var(--color-copper)] hover:underline underline-offset-2"
        >
          Browse all estimations →
        </Link>
      </div>
    </Page>
  );
}
