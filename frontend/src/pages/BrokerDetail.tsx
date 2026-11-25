import { useId, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { brokerBoardPath } from '@/lib/browseState';
import { useNewTabProps } from '@/lib/linkGestures';
import { useQuery } from '@tanstack/react-query';
import {
  BROKER_PRICE_SCOPES,
  BROKER_PROPERTIES_PAGE,
  categoryShareLabel,
  fetchBrokerDossier,
  fetchBrokerProperties,
  inventoryCount,
  useBrokerPriceSlice,
  type BrokerCategoryShare,
  type BrokerInventoryFilter,
  type BrokerPriceFigures,
  type BrokerPriceScope,
  type BrokerProperty,
  type BrokerRegionShare,
  type BrokerMembership,
} from '../lib/brokers';
import { fmtCount, fmtCzk, fmtArea, fmtRelative } from '../lib/format';
import { portalBadge } from '../lib/portals';
import { SegmentedBar } from '../components/controls';
import BrokerContactCard from '@/components/BrokerContactCard';
import PriceDelta from '@/components/PriceDelta';
import { brokerContact } from '@/lib/brokerContact';
import Panel, { PanelEmpty } from '@/components/Panel';
import LoadMore from '@/components/LoadMore';
import { useInfiniteList } from '@/lib/useInfiniteList';
import { useExploreBrokerModal } from '@/components/ExploreBrokerModal';
import { propertyPath } from '@/lib/listingUrl';
import { categoryMainLabel, categoryTypeLabel, listingKindLabel } from '@/lib/enums';
import { usePageTitle } from '@/lib/pageTitle';
import { usePhoneShell } from '@/lib/phoneShell';
import { PhoneSuspense } from '@/components/phone/RollParts';
import { lazyChunk } from '@/lib/lazyChunk';

const PhoneBrokerDetail = lazyChunk(() => import('./phone/PhoneBrokerDetail'));


// The broker's own inventory filter chips, defaulting to Vše/Vše so nothing is
// hidden on load.
const CATEGORY_OPTIONS: ReadonlyArray<{ value: string | null; label: string }> = [
  { value: null, label: 'Vše' },
  { value: 'byt', label: 'Byty' },
  { value: 'dum', label: 'Domy' },
  { value: 'pozemek', label: 'Pozemky' },
  { value: 'komercni', label: 'Komerční' },
];
const OFFER_OPTIONS: ReadonlyArray<{ value: string | null; label: string }> = [
  { value: null, label: 'Vše' },
  { value: 'prodej', label: 'Prodej' },
  { value: 'pronajem', label: 'Pronájem' },
];

/* Under the phone shell: the dossier without the map, footprint as numbers
 * (#1760 Q12); the explore-on-map modal is desktop only. */
export default function BrokerDetail() {
  return usePhoneShell() ? (
    <PhoneSuspense>
      <PhoneBrokerDetail />
    </PhoneSuspense>
  ) : <BrokerDetailDesktop />;
}

function BrokerDetailDesktop() {
  const { id } = useParams<{ id: string }>();
  const brokerId = Number(id);

  // Lifted out of Inventory so the "Explore this broker's listings" modal
  // seeds from the SAME Typ/Nabídka selection the table reads —
  // one cohort definition for both, not two that happen to agree.
  const [categoryMain, setCategoryMain] = useState<string | null>(null);
  const [categoryType, setCategoryType] = useState<string | null>(null);
  const { open: openExploreBroker } = useExploreBrokerModal();

  // One call: identity + firms + regional footprint (+ contacts for an admin).
  // Region names arrive joined, so the old geo-options query and the
  // regionNames-dependent `enabled` gate on the shares query are both gone.
  const dossierQ = useQuery({
    queryKey: ['broker-dossier', brokerId],
    queryFn: () => fetchBrokerDossier(brokerId),
    enabled: Number.isFinite(brokerId),
  });

  const dossier = dossierQ.data;
  const b = dossier?.broker;
  usePageTitle(b?.display_name ?? null);

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto text-[var(--color-ink)]">
      <Link
        to={brokerBoardPath()}
        className="text-xs tracking-[0.12em] uppercase text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
      >
        ← Žebříček makléřů
      </Link>

      {dossierQ.isLoading ? (
        <p className="mt-8 text-sm text-[var(--color-ink-3)]">Načítám…</p>
      ) : dossierQ.isError ? (
        /* A failed read is NOT "no such broker" — the old page rendered both as
           "Makléř nenalezen." and hid every outage behind an empty-state. */
        <p className="mt-8 text-sm text-[var(--color-brick)]">
          Makléře se nepodařilo načíst: {(dossierQ.error as Error).message}
        </p>
      ) : !b || !dossier ? (
        <p className="mt-8 text-sm text-[var(--color-ink-3)]">Makléř nenalezen.</p>
      ) : (
        <>
          <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h1 className="text-[1.7rem] leading-tight font-[family-name:var(--font-display)]">
                {b.display_name ?? 'Neznámý makléř'}
              </h1>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-[var(--color-ink-3)]">
                <span>{b.firm_name ?? b.firm_domain ?? 'nezávislý / neznámá kancelář'}</span>
                {b.firm_is_franchise && (
                  <span className="text-[0.6rem] tracking-[0.1em] uppercase px-1.5 py-0.5 rounded-[var(--radius-xs)] border border-[var(--color-rule)] text-[var(--color-ink-4)]">
                    franšíza
                  </span>
                )}
                {b.distinct_source_count > 1 && (
                  <span className="text-[0.6rem] tracking-[0.1em] uppercase px-1.5 py-0.5 rounded-[var(--radius-xs)] bg-[var(--color-copper-soft)] text-[var(--color-copper-2)]">
                    {b.distinct_source_count} portály
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() =>
                  openExploreBroker({
                    brokerId,
                    brokerName: b.display_name ?? 'Neznámý makléř',
                    categoryMain,
                    categoryType,
                  })
                }
                className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-[0.8rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
                title="See this broker's listings on the map — same Typ/Nabídka selection as below"
              >
                <MapPinGlyph />
                <span>Explore this broker's listings</span>
              </button>
            </div>
            <BrokerContactCard
              broker={b}
              saveAs={brokerContact({
                brokerId: b.broker_id,
                name: b.display_name,
                firm: b.firm_name ?? b.firm_domain,
                phone: b.primary_phone,
                email: b.primary_email,
              })}
            />
          </header>

          {/* Stats strip */}
          <div className="mt-6 grid grid-cols-[1fr_1fr_1fr_minmax(0,1.7fr)] border border-[var(--color-rule)] rounded-[var(--radius-md)] divide-x divide-[var(--color-rule-soft)] bg-[var(--color-paper-2)]">
            <Stat
              label="Nemovitosti"
              value={b.active_properties}
              total={b.properties}
              hint="aktivní / celkem"
            />
            <Stat label="Kanceláře" value={dossier.memberships.length} hint="historicky" />
            <Stat label="Regiony" value={dossier.region_shares.length} hint="kde inzeruje" />
            <PriceChangeStat figures={dossier.price_change} />
          </div>

          {/* Section cards, 16 px apart (system.md § Section cards). */}
          <div className="mt-4 space-y-4">
            <div className="grid gap-4 md:grid-cols-3 items-start">
              <Footprint shares={dossier.region_shares} />
              <Types rows={dossier.categories} />
              <Firms rows={dossier.memberships} />
            </div>

            <Inventory
              brokerId={brokerId}
              categories={dossier.categories}
              filter={{ categoryMain, categoryType }}
              onCategoryMainChange={setCategoryMain}
              onCategoryTypeChange={setCategoryType}
            />
          </div>
        </>
      )}
    </div>
  );
}


/* A broker's properties are a list worked through: each opens in a new tab. The
 * dot marks a property the broker no longer advertises. */
function LocalityCell({ p }: { p: BrokerProperty }) {
  const newTab = useNewTabProps();
  return (
    <Link to={propertyPath(p.property_id)} {...newTab} className="flex items-center gap-2 hover:text-[var(--color-copper-2)]">
      {!p.is_active && (
        <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-brick)] shrink-0" title="neaktivní" />
      )}
      <span className="truncate font-[family-name:var(--font-sans)]">{p.display_label ?? '—'}</span>
    </Link>
  );
}

/* The portals the broker advertises the property on; one whose ad ended recedes. */
function PortalsCell({ p }: { p: BrokerProperty }) {
  return (
    <>
      {p.all_sources.map((source, i) => {
        const live = p.active_sources.includes(source);
        return (
          <span key={source} title={live ? undefined : 'inzerát ukončen'}
            className={live ? 'text-[var(--color-ink-3)]' : 'text-[var(--color-ink-4)]'}>
            {i > 0 && ' · '}
            {portalBadge(source)}
          </span>
        );
      })}
    </>
  );
}

function Stat({
  label,
  value,
  total,
  hint,
}: {
  label: string;
  value: number;
  total?: number;
  hint?: string;
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-[0.6rem] tracking-[0.14em] uppercase text-[var(--color-ink-3)]">
        {label}
      </p>
      <p className="mt-1 font-[family-name:var(--font-mono)] tabular-nums text-[1.35rem] leading-none text-[var(--color-ink)]">
        {fmtCount(value)}
        {total != null && total > value && (
          <span className="text-[var(--color-ink-4)] text-sm"> / {fmtCount(total)}</span>
        )}
      </p>
      {hint && <p className="mt-1 text-[0.62rem] text-[var(--color-ink-4)]">{hint}</p>}
    </div>
  );
}

/* How much the broker changes prices (migration 770): the share of their priced
 * properties whose price they changed at least once, and the median change among
 * those. Over the whole book like its neighbours, not the
 * inventory's Typ / Nabídka selection; the switch is the status Nemovitosti counts. */
function PriceChangeStat({ figures }: { figures: Record<BrokerPriceScope, BrokerPriceFigures> }) {
  const labelId = useId();
  const { scope, setScope, share, median, hint } = useBrokerPriceSlice(figures);
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <p id={labelId} className="text-[0.6rem] tracking-[0.14em] uppercase text-[var(--color-ink-3)]">
          Změna ceny
        </p>
        <SegmentedBar aria={{ 'aria-labelledby': labelId }} options={BROKER_PRICE_SCOPES} value={scope}
          onChange={setScope} />
      </div>
      <p className="mt-1 font-[family-name:var(--font-mono)] tabular-nums text-[1.35rem] leading-none text-[var(--color-ink)]">
        {share}
        {median && <span className="text-[var(--color-ink-3)] text-sm"> · {median}</span>}
      </p>
      <p className="mt-1 text-[0.62rem] text-[var(--color-ink-3)]">{hint}</p>
    </div>
  );
}

function Footprint({ shares }: { shares: BrokerRegionShare[] }) {
  const max = shares.reduce((m, s) => Math.max(m, s.active_properties), 0) || 1;
  return (
    <Panel title="Kde inzeruje" count={shares.length}>
      {shares.length === 0 ? (
        <PanelEmpty>Bez aktivního regionu.</PanelEmpty>
      ) : (
        <ul className="space-y-2">
          {shares.slice(0, 8).map((s) => (
            <li key={s.geo_id} className="flex items-center gap-3">
              <span className="w-40 shrink-0 truncate text-sm text-[var(--color-ink-2)]">
                {s.name ?? '—'}
              </span>
              <span className="flex-1 h-1.5 rounded-full bg-[var(--color-inset)] overflow-hidden">
                <span
                  className="block h-full bg-[var(--color-copper)]"
                  style={{ width: `${Math.max(4, (s.active_properties / max) * 100)}%` }}
                />
              </span>
              <span className="w-10 shrink-0 text-right text-xs font-[family-name:var(--font-mono)] tabular-nums text-[var(--color-ink)]">
                {fmtCount(s.active_properties)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/* What the broker sells and lets, per type and deal, busiest first as served:
 * the properties with an ad of theirs still running / all of the book's. */
function Types({ rows }: { rows: BrokerCategoryShare[] }) {
  return (
    <Panel title="Typy" count={rows.length}>
      {rows.length === 0 ? (
        <PanelEmpty>Žádná nemovitost.</PanelEmpty>
      ) : (
        <ul className="space-y-2">
          {rows.map((c) => (
            <li key={`${c.category_main ?? ''}|${c.category_type ?? ''}`} className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-sm text-[var(--color-ink-2)]">{categoryShareLabel(c)}</span>
              <span className="shrink-0 text-xs font-[family-name:var(--font-mono)] tabular-nums text-[var(--color-ink)]"
                title="aktivní / celkem nemovitostí">
                {fmtCount(c.active_properties)}
                {c.properties > c.active_properties && (
                  <span className="text-[var(--color-ink-4)]"> / {fmtCount(c.properties)}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Firms({ rows }: { rows: BrokerMembership[] }) {
  return (
    <Panel title="Kanceláře" count={rows.length}>
      {rows.length === 0 ? (
        <PanelEmpty>Žádná kancelář (nezávislý).</PanelEmpty>
      ) : (
        <ul className="space-y-2">
          {rows.map((m) => (
            <li key={m.firm_id} className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-sm text-[var(--color-ink-2)]">
                {m.firm_name ?? m.firm_domain ?? '—'}
              </span>
              {m.is_current && (
                <span className="shrink-0 text-[0.55rem] tracking-[0.1em] uppercase px-1.5 py-0.5 rounded-[var(--radius-xs)] bg-[var(--color-sage-soft)] text-[var(--color-sage)]">
                  aktuální
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/* The broker's book, one row per property, a page at a time. Typ / Nabídka narrow
 * the read itself; the count in the title bar is the dossier's, exact over the
 * book's properties Browse serves, the ones this table lists ("z" all of them
 * when a filter narrows it), so it does not wait for the pages. */
function Inventory({
  brokerId,
  categories,
  filter,
  onCategoryMainChange,
  onCategoryTypeChange,
}: {
  brokerId: number;
  categories: BrokerCategoryShare[];
  filter: BrokerInventoryFilter;
  onCategoryMainChange: (v: string | null) => void;
  onCategoryTypeChange: (v: string | null) => void;
}) {
  const captionId = useId();
  const { categoryMain, categoryType } = filter;
  const list = useInfiniteList<BrokerProperty>({
    queryKey: ['broker-properties', brokerId, categoryMain, categoryType],
    queryFn: (cursor, signal) => fetchBrokerProperties(brokerId, filter, cursor as string | null, signal),
    pageSize: BROKER_PROPERTIES_PAGE,
    getRowId: (p) => p.property_id,
  });
  const total = inventoryCount(categories, { categoryMain: null, categoryType: null });
  const selected = inventoryCount(categories, filter);
  const isFiltered = categoryMain !== null || categoryType !== null;
  const count = isFiltered && total > 0 ? `${fmtCount(selected)} z ${fmtCount(total)}` : fmtCount(total);

  return (
    <Panel title="Inventář" count={count}>
      {total > 0 && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {([
            ['Typ', CATEGORY_OPTIONS, categoryMain, onCategoryMainChange],
            ['Nabídka', OFFER_OPTIONS, categoryType, onCategoryTypeChange],
          ] as const).map(([label, options, value, onChange]) => (
            <div key={label} className="flex items-center gap-2">
              {/* L4 field label (SectionLabel's type, on a span that names the bar). */}
              <span id={`${captionId}-${label}`} className="text-[11px] leading-[14px] font-semibold tracking-[0.08em] uppercase text-[var(--color-ink-3)]">
                {label}
              </span>
              <SegmentedBar hug aria={{ 'aria-labelledby': `${captionId}-${label}` }} options={options} value={value}
                onChange={onChange} />
            </div>
          ))}
        </div>
      )}

      {list.isLoading ? (
        <p className="text-sm text-[var(--color-ink-3)]">Načítám nemovitosti…</p>
      ) : list.isError && list.rows.length === 0 ? (
        /* The stats strip above already asserts a property count from the
           dossier, which succeeds independently — so a failed inventory read
           rendered as empty would make the page contradict itself. */
        <p className="text-sm text-[var(--color-brick)]">
          Nemovitosti se nepodařilo načíst: {list.error?.message}
        </p>
      ) : list.rows.length === 0 ? (
        <PanelEmpty>{isFiltered ? 'Žádné nemovitosti pro zvolený filtr.' : 'Žádné nemovitosti.'}</PanelEmpty>
      ) : (
        <>
          {/* A table that scrolls keeps its own box (C9); the previous selection's rows recede while the new read loads. */}
          <div aria-busy={list.isPlaceholderData}
            className={`overflow-x-auto border border-[var(--color-rule)] rounded-[var(--radius-md)] transition-opacity ${list.isPlaceholderData ? 'opacity-50' : ''}`}>
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-[var(--color-rule)] bg-[var(--color-paper-2)] text-left">
                  {['Lokalita', 'Typ', 'Plocha', 'Cena', 'Změna ceny', 'Portály', 'Viděno'].map((h) => (
                    <th
                      key={h}
                      className="px-3 py-2 font-normal whitespace-nowrap text-[var(--color-ink-3)] text-xs"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="font-[family-name:var(--font-mono)] text-[0.78rem]">
                {list.rows.map((p) => (
                  <tr
                    key={p.property_id}
                    className="border-b border-[var(--color-rule-soft)] last:border-0 hover:bg-[var(--color-paper-2)]"
                  >
                    <td className="px-3 py-1.5 max-w-[16rem]">
                      <LocalityCell p={p} />
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap text-[var(--color-ink-2)] font-[family-name:var(--font-sans)]">
                      {[
                        // The specific kind (subtype for commercial/houses, else
                        // disposition); the umbrella category word only when no
                        // subtype is shown, to avoid "Ubytování · Komerční" doubling.
                        listingKindLabel(p),
                        p.subtype ? null : categoryMainLabel(p.category_main),
                        p.category_type ? categoryTypeLabel(p.category_type).toLowerCase() : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap tabular-nums text-right text-[var(--color-ink-3)]">
                      {fmtArea(p.area_m2)}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap tabular-nums text-right">
                      {fmtCzk(p.price_czk)}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      <PriceDelta pct={p.price_change_pct} changes={p.price_change_count} muted={!p.is_active} />
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap font-[family-name:var(--font-sans)]">
                      <PortalsCell p={p} />
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap text-[var(--color-ink-4)] font-[family-name:var(--font-sans)]">
                      {fmtRelative(p.last_seen_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.isFetchNextPageError && (
            <p className="text-sm text-[var(--color-brick)]">
              Další nemovitosti se nepodařilo načíst: {list.error?.message}
            </p>
          )}
          <LoadMore list={list} />
        </>
      )}
    </Panel>
  );
}

function MapPinGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M8 1.5c2.5 0 4.5 2 4.5 4.5 0 3-4.5 8-4.5 8S3.5 9 3.5 6C3.5 3.5 5.5 1.5 8 1.5z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="6" r="1.6" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}
