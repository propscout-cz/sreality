/* Browse › Stats' named dashboards — the dashboard instance of THE saved-set bar
 * (components/SavedSetBar; docs/design/saved-dashboards.md). A dashboard is a
 * named chart set, optionally with the Browse filter it was saved over (the
 * preset blob). Loading one COPIES it into the working board — StatsDashboard's
 * one board write, `onLoadCharts` — then writes the URL (browseState.
 * loadDashboard); it changes only on "Uložit změny". A dashboard shared WITH the
 * account is read-only: Načíst, Uložit jako vlastní, Přestat sledovat. */

import { useEffect, useId, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { isApiConfigured } from '@/lib/api';
import type { BrowseViewState } from '@/lib/browseState';
import {
  filtersEqualForPreset,
  filtersForPreset,
  readPresetSpec,
  type PresetSpec,
} from '@/lib/filters';
import { sortToParam } from '@/lib/queries';
import {
  chartsEqual,
  createStatsDashboard,
  deleteStatsDashboard,
  listStatsDashboards,
  reorderStatsDashboards,
  sanitizeCharts,
  shareStatsDashboard,
  statsDashboardKeys,
  unshareStatsDashboard,
  updateStatsDashboard,
  type ChartSpec,
  type DashboardList,
} from '@/lib/stats';
import type { SavedDashboard } from '@/lib/types';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import Dialog, { DialogClose } from '@/components/Dialog';
import SavedSetBar, { type SavedSetCopy, type SavedSetMenuItem } from '@/components/SavedSetBar';
import ShareBlock from '@/components/ShareBlock';

export type DashboardView = Pick<
  BrowseViewState,
  'filters' | 'sort' | 'activeDashboardId' | 'setActiveDashboardId' | 'loadDashboard'
>;

type DashboardPatch = Parameters<typeof updateStatsDashboard>[1];
type NewDashboard = Parameters<typeof createStatsDashboard>[0];

const COPY: SavedSetCopy = {
  kind: 'Dashboard',
  empty: 'Zatím žádné uložené dashboardy.',
  save: '+ Uložit jako dashboard',
  saveHint: 'Uložit současné grafy jako nový dashboard',
  update: 'Uložit změny',
  updateHint: 'Uložit současné grafy (a filtr, nese-li ho) zpět do tohoto dashboardu',
  dialog: { save: 'Uložit grafy jako dashboard', update: 'Uložit změny dashboardu', edit: 'Přejmenovat dashboard' },
  submit: { save: 'Uložit dashboard', update: 'Uložit změny', edit: 'Uložit' },
  nameLabel: 'Název dashboardu',
  namePlaceholder: 'např. Praha 2+kk — ceny',
  color: 'Barva',
  cancel: 'Zrušit',
  saving: 'Ukládám…',
  edit: 'Přejmenovat',
  delete: 'Smazat',
  confirmDelete: 'Opravdu smazat',
  deleting: 'Mažu…',
  load: (name) => `Načíst dashboard: ${name}`,
  reorder: (name) => `Přesunout dashboard: ${name}`,
  dragHint: 'Přetažením změníte pořadí',
  options: (name) => `Možnosti dashboardu: ${name}`,
  optionsHint: 'Možnosti dashboardu',
  edited: 'Upraveno od načtení',
  editedHint: 'Upraveno od načtení — změny uložíte tlačítkem Uložit změny',
  failed: 'Něco se nepovedlo.',
};

const listKey = statsDashboardKeys.all;
const isOwn = (d: SavedDashboard): boolean => d.owner_email == null;

/* The dashboard this browser tab last loaded (a click, an arrival, a save). The
 * URL's `dashboard` asks for a load only when it names ANOTHER one: a tab switch,
 * a Back or a reload repeats the URL and must not copy the stored charts over the
 * working board's edits. sessionStorage outlives a reload; memory backs it up. */
const LOADED_KEY = 'stats.loadedDashboard';
let loadedInMemory: string | null = null;
const lastLoaded = (): string | null => {
  try {
    return sessionStorage.getItem(LOADED_KEY);
  } catch {
    return loadedInMemory;
  }
};
const markLoaded = (id: string): void => {
  loadedInMemory = id;
  try {
    sessionStorage.setItem(LOADED_KEY, id);
  } catch {
    /* memory only */
  }
};

/* A row with the PATCH's defined fields over it (`filter_spec: null` clears). */
const patched = (d: SavedDashboard, patch: DashboardPatch): SavedDashboard => {
  const out: Record<string, unknown> = { ...d };
  for (const [k, v] of Object.entries(patch)) if (v !== undefined) out[k] = v;
  return out as unknown as SavedDashboard;
};

export default function DashboardBar({
  view,
  working,
  onLoadCharts,
}: {
  view: DashboardView;
  /* The working board as read (sanitized). */
  working: ChartSpec[];
  /* The board's one write (StatsDashboard's optimistic PUT /stats/dashboard). */
  onLoadCharts: (charts: ChartSpec[]) => void;
}) {
  const enabled = isApiConfigured();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: listKey, queryFn: listStatsDashboards, enabled });
  const items = list.data?.data ?? [];
  const [sharingId, setSharingId] = useState<string | null>(null);

  const { filters, sort, activeDashboardId } = view;
  const active = items.find((d) => d.id === activeDashboardId) ?? null;
  const dirty =
    active != null &&
    (!chartsEqual(working, sanitizeCharts(active.charts)) ||
      (active.filter_spec != null &&
        !filtersEqualForPreset(filters, readPresetSpec(active.filter_spec).filters)));

  /* A dashboard's filter is the preset blob, captured as a preset save captures
   * it — never the map viewport. */
  const currentSpec = (): PresetSpec => ({
    filters: filtersForPreset(filters, false),
    sort: sortToParam(sort),
  });

  const setRow = (id: string, next: (d: SavedDashboard) => SavedDashboard) =>
    qc.setQueryData<DashboardList>(listKey, (prev) =>
      prev && { ...prev, data: prev.data.map((d) => (d.id === id ? next(d) : d)) },
    );
  const dropRow = (id: string) =>
    cachePatch<DashboardList>({ queryKey: listKey, exact: true }, (prev) =>
      prev && { data: prev.data.filter((d) => d.id !== id), total: Math.max(0, prev.total - 1) },
    );

  /* ONE load: the stored charts into the working board, then the URL. */
  const load = (d: SavedDashboard) => {
    const charts = sanitizeCharts(d.charts);
    if (!chartsEqual(working, charts)) onLoadCharts(charts);
    markLoaded(d.id);
    view.loadDashboard(d);
  };
  const activate = (id: string) => {
    markLoaded(id);
    view.setActiveDashboardId(id);
  };

  /* Arrival: the URL names a dashboard this tab has not loaded — load it, as a
   * click would. One the caller can no longer read clears the param. Judged on a
   * settled list only, so a re-read in flight never clears a dashboard just made. */
  const settled = list.isSuccess && !list.isFetching;
  useEffect(() => {
    if (activeDashboardId == null || !settled) return;
    const d = items.find((x) => x.id === activeDashboardId);
    if (d == null) view.setActiveDashboardId(null);
    else if (lastLoaded() !== d.id) load(d);
  });

  /* The POST answers the row: it joins the list at once, so the arrival rule
   * above never sees an active id the cache does not hold yet. */
  const create = useOptimisticWrite<NewDashboard, SavedDashboard>({
    mutationKey: ['write', 'stats-dashboards', 'create'],
    mutationFn: createStatsDashboard,
    revalidate: [listKey],
    onSuccess: (created) =>
      qc.setQueryData<DashboardList>(listKey, (prev) =>
        prev && !prev.data.some((d) => d.id === created.id)
          ? { data: [...prev.data, created], total: prev.total + 1 }
          : prev,
      ),
  });

  const update = useOptimisticWrite<{ id: string; patch: DashboardPatch }, SavedDashboard>({
    mutationKey: ['write', 'stats-dashboards', 'update'],
    mutationFn: ({ id, patch }) => updateStatsDashboard(id, patch),
    patch: ({ id, patch }) => [
      cachePatch<DashboardList>({ queryKey: listKey, exact: true }, (prev) =>
        prev && { ...prev, data: prev.data.map((d) => (d.id === id ? patched(d, patch) : d)) },
      ),
    ],
    // The PUT answers the row as stored, so only a failure needs a re-read.
    revalidate: (_vars, failed) => (failed ? [listKey] : []),
    onSuccess: (saved) => setRow(saved.id, (d) => ({ ...d, ...saved })),
  });

  const remove = useOptimisticWrite<string, unknown>({
    mutationKey: ['write', 'stats-dashboards', 'delete'],
    mutationFn: deleteStatsDashboard,
    patch: (id) => [dropRow(id)],
    revalidate: [listKey],
  });

  /* A recipient leaves with its own grant — the only row in its `shared_with`. */
  const leave = useOptimisticWrite<SavedDashboard, unknown>({
    mutationKey: ['write', 'stats-dashboards', 'leave'],
    mutationFn: (d) => unshareStatsDashboard(d.id, d.shared_with[0].account_id),
    patch: (d) => [dropRow(d.id)],
    revalidate: [listKey],
  });

  const reorder = useOptimisticWrite<SavedDashboard[], DashboardList>({
    mutationKey: ['write', 'stats-dashboards', 'reorder'],
    mutationFn: (own) => reorderStatsDashboards(own.map((d) => d.id)),
    patch: (own) => [
      cachePatch<DashboardList>({ queryKey: listKey, exact: true }, (prev) =>
        prev && { ...prev, data: [...own, ...prev.data.filter((d) => !isOwn(d))] },
      ),
    ],
    revalidate: (_own, failed) => (failed ? [listKey] : []),
    onSuccess: (res) => qc.setQueryData(listKey, res),
  });

  /* A shared dashboard as the caller's own: its stored charts and filter, its name. */
  const copyAsOwn = (d: SavedDashboard) =>
    create
      .mutateAsync({
        name: d.name,
        charts: sanitizeCharts(d.charts),
        ...(d.filter_spec != null ? { filter_spec: d.filter_spec } : {}),
        color: d.color,
      })
      .then((created) => {
        if (d.id === activeDashboardId) activate(created.id);
      });

  const chipMenu = (d: SavedDashboard): SavedSetMenuItem[] => {
    if (isOwn(d)) return [{ label: 'Sdílet…', onSelect: () => setSharingId(d.id) }];
    const out: SavedSetMenuItem[] = [
      { label: 'Načíst', onSelect: () => load(d) },
      { label: 'Uložit jako vlastní', onSelect: () => copyAsOwn(d) },
    ];
    if (d.shared_with.length > 0) {
      out.push({
        label: 'Přestat sledovat',
        danger: true,
        confirm: { label: 'Opravdu přestat sledovat', busy: 'Odcházím…' },
        onSelect: () => leave.mutateAsync(d),
      });
    }
    return out;
  };

  if (!enabled) return null;
  const sharing = sharingId != null ? items.find((d) => d.id === sharingId) ?? null : null;

  return (
    <>
      <SavedSetBar
        items={items}
        loading={list.isLoading}
        activeId={activeDashboardId}
        dirty={dirty}
        copy={COPY}
        isOwn={isOwn}
        suffix={(d) => d.owner_email}
        chipMenu={chipMenu}
        onLoad={load}
        onSaveNew={(name, withFilter, color) =>
          create
            .mutateAsync({
              name,
              charts: working,
              ...(withFilter ? { filter_spec: currentSpec() } : {}),
              color,
            })
            .then((created) => activate(created.id))
        }
        onUpdate={(d, name, withFilter, color) =>
          update.mutateAsync({
            id: d.id,
            patch: {
              name,
              color,
              charts: working,
              // Unticking the box drops a stored filter; an untouched box keeps it current.
              ...(withFilter
                ? { filter_spec: currentSpec() }
                : d.filter_spec != null
                  ? { filter_spec: null }
                  : {}),
            },
          })
        }
        onRename={(d, name, color) => update.mutateAsync({ id: d.id, patch: { name, color } })}
        onDelete={(d) => remove.mutateAsync(d.id)}
        onReorder={(own) => reorder.mutate(own)}
        extra={{
          label: 'Uložit i nastavení filtru',
          initial: (d) => d != null && d.filter_spec != null,
        }}
      />
      {sharing ? (
        <ShareDialog
          dashboard={sharing}
          onClose={() => setSharingId(null)}
          onShare={(email) =>
            shareStatsDashboard(sharing.id, email).then((saved) =>
              setRow(saved.id, (d) => ({ ...d, ...saved })),
            )
          }
          onRevoke={(accountId) =>
            unshareStatsDashboard(sharing.id, accountId).then(() => {
              setRow(sharing.id, (d) => ({
                ...d,
                shared_with: d.shared_with.filter((r) => r.account_id !== accountId),
              }));
              void qc.invalidateQueries({ queryKey: listKey });
            })
          }
        />
      ) : null}
    </>
  );
}

/* "Sdílet…": the one ShareBlock (read-only shares, no modes) in a small dialog. */
function ShareDialog({
  dashboard,
  onClose,
  onShare,
  onRevoke,
}: {
  dashboard: SavedDashboard;
  onClose: () => void;
  onShare: (email: string) => Promise<unknown>;
  onRevoke: (accountId: string) => Promise<unknown>;
}) {
  const titleId = useId();
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} className="w-full max-w-lg p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
            {COPY.kind}
          </p>
          <h2
            id={titleId}
            className="mt-1 text-xl leading-tight break-words"
            style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
          >
            Sdílet „{dashboard.name}“
          </h2>
        </div>
        <DialogClose onClick={onClose} label="Zavřít" />
      </div>
      <div className="mt-4">
        <ShareBlock
          title="Sdílení"
          lang="cs"
          description="Komu dashboard nasdílíte, uvidí ho mezi svými dashboardy a může si ho načíst nebo uložit jako vlastní. Měnit ho nemůže; vaše uložené změny uvidí."
          recipients={dashboard.shared_with}
          onShare={onShare}
          onRevoke={onRevoke}
        />
      </div>
    </Dialog>
  );
}
