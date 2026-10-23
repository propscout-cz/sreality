/* The property page's "Okolí dashboard" tab (operator 2026-10-23): a small dashboard
 * of THIS property's neighbourhood. Its charts are the account's ONE dashboard marked
 * for it (migration 663, any name), set up in Browse › Statistiky; its
 * sample is lib/neighbourhood's — the property's type, deal, disposition and
 * ownership, a radius around it, and only the building material and condition
 * left to the user — under the Stats tab's own lens ("Prodáno (trh)" by default,
 * or "Vše"). "Prozkoumat okolí" opens the explore window over the same sample,
 * lens and dashboard: one neighbourhood, two depths. */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { isApiConfigured } from '@/lib/api';
import { categoryMainLabel, categoryTypeLabel, enumLabel } from '@/lib/enums';
import { FILTER_REGISTRY } from '@/lib/filterRegistry.generated';
import type { BuildingMaterial } from '@/lib/filters';
import {
  DEFAULT_SCOPE,
  RADII_M,
  fmtRadius,
  isPropertyCard,
  neighbourhoodFilters,
  PROPERTY_CARD_LABEL,
  type NeighbourhoodMarket,
  type NeighbourhoodScope,
  type RadiusM,
} from '@/lib/neighbourhood';
import type { PropertyPublic } from '@/lib/queries';
import { ROUTES, withQuery } from '@/lib/routes';
import {
  MARKET_CAPTION,
  listStatsDashboards,
  marketSample,
  sanitizeCharts,
  statsDashboardKeys,
} from '@/lib/stats';
import type { ImagePublic } from '@/lib/types';
import { Segmented } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import { useExploreAreaModal } from '@/components/ExploreAreaModal';
import { MultiselectChips } from '@/components/filter-controls';
import { SectionLabel } from '@/components/section';
import Skeleton from '@/components/Skeleton';
import StatsChart from '@/components/stats/StatsChart';

const options = (id: string) =>
  (FILTER_REGISTRY.filters.find((f) => f.id === id)?.enum_values ?? [])
    .filter((o) => o.value !== '__unknown__')
    .map((o) => ({ value: String(o.value), label: o.label_cs }));
const MATERIALS = options('building_material') as { value: BuildingMaterial; label: string }[];
const CONDITIONS = options('condition_match');

/* The radius and the lens are this browser's habit; the building and the
 * condition belong to the property you are looking at. */
const PREF_KEY = 'sreality.marketTile';
const readPrefs = (): Pick<NeighbourhoodScope, 'radiusM' | 'market'> => {
  try {
    const raw = JSON.parse(localStorage.getItem(PREF_KEY) ?? 'null') as Partial<NeighbourhoodScope> | null;
    return {
      radiusM: RADII_M.includes(raw?.radiusM as RadiusM) ? (raw!.radiusM as RadiusM) : DEFAULT_SCOPE.radiusM,
      market: raw?.market === 'all' ? 'all' : DEFAULT_SCOPE.market,
    };
  } catch {
    return { radiusM: DEFAULT_SCOPE.radiusM, market: DEFAULT_SCOPE.market };
  }
};
const writePrefs = (s: NeighbourhoodScope): void => {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({ radiusM: s.radiusM, market: s.market }));
  } catch {
    /* memory only */
  }
};

export default function MarketTile({
  property,
  images,
}: {
  property: PropertyPublic;
  /* The origin panel of the explore window: the photos this page already holds. */
  images: ImagePublic[];
}) {
  const [scope, setScopeState] = useState<NeighbourhoodScope>(() => ({ ...DEFAULT_SCOPE, ...readPrefs() }));
  const setScope = (patch: Partial<NeighbourhoodScope>) =>
    setScopeState((prev) => {
      const next = { ...prev, ...patch };
      writePrefs(next);
      return next;
    });

  /* The Stats bar's own read (same key), so a dashboard saved there is here at once. */
  const listQ = useQuery({
    queryKey: statsDashboardKeys.all,
    queryFn: listStatsDashboards,
    enabled: isApiConfigured(),
  });
  const template = listQ.data?.data.find(isPropertyCard) ?? null;
  const charts = useMemo(() => (template ? sanitizeCharts(template.charts) : []), [template]);

  const hood = useMemo(() => neighbourhoodFilters(property, scope), [property, scope]);
  const sample = useMemo(() => (hood ? marketSample(hood.filters, scope.market) : null), [hood, scope.market]);
  const { open } = useExploreAreaModal();

  const kind = [
    categoryMainLabel(property.category_main),
    property.category_type ? categoryTypeLabel(property.category_type) : null,
    property.disposition,
    property.ownership ? enumLabel('ownership', property.ownership) : null,
  ].filter(Boolean);

  const explore = () => {
    if (!hood || property.lat == null || property.lng == null) return;
    open({
      filters: hood.filters,
      lat: property.lat,
      lng: property.lng,
      tab: 'stats',
      market: scope.market,
      dashboard: template?.id ?? null,
      origin: { listing: property, images },
      label: [property.display_label, property.disposition].filter(Boolean).join(' · ') || undefined,
    });
  };

  let body: React.ReactNode;
  if (!hood || !sample) {
    body = <Quiet>Bez souřadnic nelze hledat okolí.</Quiet>;
  } else if (listQ.isLoading) {
    body = <Skeleton height={192} />;
  } else if (listQ.isError) {
    body = (
      <ErrorBanner
        title="Dashboardy se nepodařilo načíst:"
        message={listQ.error.message}
        onRetry={() => void listQ.refetch()}
      />
    );
  } else if (!template) {
    body = (
      <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 py-5 text-sm text-[var(--color-ink-2)]">
        Grafy pro tuto kartu nastavíte v Browse › Statistiky: tlačítkem „+ {PROPERTY_CARD_LABEL}“ uložíte
        současné grafy, nebo v menu kteréhokoli svého dashboardu zvolíte „Použít jako okolí dashboard“.
        <div className="mt-3">
          <Link to={withQuery(ROUTES.browse.build(), { tab: 'stats' })} className="text-[var(--color-copper)] hover:underline underline-offset-2">
            Otevřít Statistiky →
          </Link>
        </div>
      </div>
    );
  } else if (charts.length === 0) {
    body = <Quiet>Dashboard „{template.name}“ zatím nemá žádný graf.</Quiet>;
  } else {
    body = (
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        {charts.map((c) => (
          <StatsChart key={c.id} chart={c} filters={sample} />
        ))}
      </div>
    );
  }

  return (
    <section
      aria-label={PROPERTY_CARD_LABEL}
      className="min-w-0 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5 pt-4 pb-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <SectionLabel>{template?.name ?? PROPERTY_CARD_LABEL}</SectionLabel>
        {hood && (
          <button
            type="button"
            onClick={explore}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper-3)] px-3 py-1.5 text-[0.8rem] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
            title="Celý Browse nad stejným okolím: mapa, inzeráty a statistiky; filtry lze dál měnit"
          >
            Prozkoumat okolí <span aria-hidden>↗</span>
          </button>
        )}
      </div>

      {hood && (
        <div className="mt-3 space-y-3">
          <p className="text-[0.85rem] text-[var(--color-ink-2)]">
            {kind.join(' · ')} · do {fmtRadius(scope.radiusM)}
            {hood.dropped.length > 0 && (
              <span className="text-[var(--color-ink-3)]">
                {' '}
                — {hood.dropped.join(', ')} neuvedeno, bez filtru
              </span>
            )}
          </p>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <Segmented
              label="Okruh"
              options={RADII_M.map((m) => ({ value: m, label: fmtRadius(m) }))}
              value={scope.radiusM}
              onChange={(radiusM) => setScope({ radiusM })}
            />
            <Segmented
              label="Vzorek"
              options={[
                { value: 'sold' as NeighbourhoodMarket, label: 'Prodáno (trh)' },
                { value: 'all' as NeighbourhoodMarket, label: 'Vše' },
              ]}
              value={scope.market}
              onChange={(market) => setScope({ market })}
            />
          </div>
          <div className="grid gap-x-6 gap-y-2 md:grid-cols-[auto_minmax(0,1fr)] md:items-start">
            <span className="pt-1 text-[0.75rem] text-[var(--color-ink-3)]">Budova</span>
            <MultiselectChips
              value={scope.buildingMaterial}
              options={MATERIALS}
              onChange={(buildingMaterial) => setScope({ buildingMaterial })}
            />
            <span className="pt-1 text-[0.75rem] text-[var(--color-ink-3)]">Stav</span>
            <MultiselectChips
              value={scope.conditionMatch}
              options={CONDITIONS}
              onChange={(conditionMatch) => setScope({ conditionMatch })}
            />
          </div>
          <p className="text-[0.75rem] text-[var(--color-ink-3)]">{MARKET_CAPTION[scope.market]}</p>
        </div>
      )}

      <div className="mt-4">{body}</div>
    </section>
  );
}

const Quiet = ({ children }: { children: React.ReactNode }) => (
  <p className="text-sm text-[var(--color-ink-3)]">{children}</p>
);
