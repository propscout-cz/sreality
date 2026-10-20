/* Browse › Stats' named dashboards — the dashboard instance of THE saved-set bar
 * (components/SavedSetBar; docs/design/saved-dashboards.md). A dashboard is a
 * named chart set, optionally with the Browse filter it was saved over (the
 * preset blob). Loading one COPIES it into the working board — StatsDashboard's
 * one board write, `onLoadCharts` — then writes the URL (browseState.
 * loadDashboard); it changes only on "Uložit změny". A dashboard shared WITH the
 * account is read-only: Načíst, Uložit jako vlastní, Přestat sledovat. */

import { useEffect, useId, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import type { BrowseViewState } from '@/lib/browseState';
import { filtersEqualForPreset, filtersForPreset, readPresetSpec, type PresetSpec } from '@/lib/filters';
import { sortToParam } from '@/lib/queries';
import {
  chartsEqual, createStatsDashboard, deleteStatsDashboard, listStatsDashboards, reorderStatsDashboards, sanitizeCharts,
  shareStatsDashboard, statsDashboardKeys, unshareStatsDashboard, updateStatsDashboard, type ChartSpec, type DashboardList,
} from '@/lib/stats';
import type { SavedDashboard } from '@/lib/types';
import { pushToast } from '@/lib/toast';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import Dialog, { DialogClose } from '@/components/Dialog';
import SavedSetBar, { useSavedSet, type SavedSetCopy, type SavedSetMenuItem } from '@/components/SavedSetBar';
import ShareBlock from '@/components/ShareBlock';

export type DashboardView = Pick<
  BrowseViewState,
  'filters' | 'sort' | 'activeDashboardId' | 'setActiveDashboardId' | 'loadDashboard'
>;

type DashboardPatch = Parameters<typeof updateStatsDashboard>[1];

const COPY: SavedSetCopy = {
  kind: 'Dashboard',
  empty: 'Zatím žádné uložené dashboardy.',
  save: '+ Uložit jako dashboard',
  saveHint: 'Uložit současné grafy jako nový dashboard',
  updateHint: 'Uložit současné grafy (a filtr, nese-li ho) zpět do tohoto dashboardu',
  dialog: { save: 'Uložit grafy jako dashboard', update: 'Uložit změny dashboardu', edit: 'Přejmenovat dashboard' },
  submit: { save: 'Uložit dashboard', update: 'Uložit změny', edit: 'Uložit' },
  nameLabel: 'Název dashboardu',
  namePlaceholder: 'např. Praha 2+kk — ceny',
  color: 'Barva',
  cancel: 'Zrušit',
  saving: 'Ukládám…',
  edit: 'Přejmenovat',
  delete: { label: 'Smazat…', confirm: { label: 'Opravdu smazat', busy: 'Mažu…' } },
  load: 'Načíst dashboard',
  reorder: 'Přesunout dashboard',
  dragHint: 'Přetažením změníte pořadí',
  options: 'Možnosti dashboardu',
  edited: 'Upraveno od načtení',
  editedHint: 'Upraveno od načtení — změny uložíte tlačítkem Uložit změny',
  shared: { by: 'sdílí', editedHint: 'Upraveno od načtení — změny uložíte přes Uložit jako vlastní' },
  failed: 'Něco se nepovedlo.',
};

const listKey = statsDashboardKeys.all;
const DISPLAY_TITLE = { fontFamily: 'var(--font-display)', fontWeight: 600 };
const isOwn = (d: SavedDashboard): boolean => d.owner_email == null;
/* A failed dashboard write, said in the Stats tab's words (StatsDashboard's
 * "Grafy se nepodařilo uložit: …"); declaring it silences the global toast. */
const failed = (what: string) => (err: Error) => pushToast('err', `${what}: ${err.message}`);

/* The dashboard this browser tab last loaded (a click, an arrival, a save). The
 * URL's `dashboard` asks for a load only when it names ANOTHER one. A tab switch,
 * a reload, or a Back to an entry of the same dashboard repeats the URL and
 * leaves the working board, its edits included, as it is; a Back to the entry of
 * a different dashboard (a click with a filter pushes one) loads that one again.
 * Memory is read first, so a storage that refuses writes cannot loop a load;
 * sessionStorage carries the marker over a reload. */
const LOADED_KEY = 'stats.loadedDashboard';
let loadedInMemory: string | null = null;
const lastLoaded = (): string | null => {
  if (loadedInMemory != null) return loadedInMemory;
  try {
    return sessionStorage.getItem(LOADED_KEY);
  } catch {
    return null;
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
export function __resetLoadedDashboardForTests(): void {
  loadedInMemory = null;
}

/* The cached list with one row rewritten. */
const withRow =
  (id: string, next: (d: SavedDashboard) => SavedDashboard) => (prev: DashboardList | undefined) =>
    prev && { ...prev, data: prev.data.map((d) => (d.id === id ? next(d) : d)) };

export default function DashboardBar({ view, working, onLoadCharts }: {
  view: DashboardView;
  /* The working board as read (sanitized). */
  working: ChartSpec[];
  /* The board's one write (StatsDashboard's optimistic PUT /stats/dashboard). */
  onLoadCharts: (charts: ChartSpec[]) => Promise<unknown>;
}) {
  const qc = useQueryClient();
  /* Re-read on every mount: a link that names a dashboard shared a minute ago
   * must find it in the list, not in a cache from before the share. */
  const { enabled, query, bar } = useSavedSet(listKey, listStatsDashboards, reorderStatsDashboards, {
    isOwn,
    refetchOnMount: 'always',
    onReorderError: failed('Pořadí dashboardů se nepodařilo uložit'),
  });
  const { items } = bar;
  const [sharingId, setSharingId] = useState<string | null>(null);
  /* A load's board write in flight: the board still holds the old charts. */
  const [loadingBoard, setLoadingBoard] = useState(false);
  const shareTitleId = useId();

  const { filters, sort, activeDashboardId } = view;
  const active = items.find((d) => d.id === activeDashboardId);
  const dirty = useMemo(
    () =>
      active != null &&
      !loadingBoard &&
      (!chartsEqual(working, sanitizeCharts(active.charts)) ||
        (active.filter_spec != null &&
          !filtersEqualForPreset(filters, readPresetSpec(active.filter_spec).filters))),
    [active, loadingBoard, working, filters],
  );

  /* A dashboard's filter is the preset blob, captured as a preset save captures
   * it — never the map viewport. */
  const currentSpec = (): PresetSpec => ({ filters: filtersForPreset(filters, false), sort: sortToParam(sort) });
  const adopt = (saved: SavedDashboard) => qc.setQueryData(listKey, withRow(saved.id, (d) => ({ ...d, ...saved })));

  /* ONE load: the stored charts into the working board, then the URL. */
  const load = (d: SavedDashboard, opts?: { replace?: boolean }) => {
    const charts = sanitizeCharts(d.charts);
    if (!chartsEqual(working, charts)) {
      setLoadingBoard(true);
      const done = () => setLoadingBoard(false);
      onLoadCharts(charts).then(done, done);
    }
    markLoaded(d.id);
    view.loadDashboard(d, opts);
  };
  const activate = (id: string) => {
    markLoaded(id);
    view.setActiveDashboardId(id);
  };

  /* The POST answers the row: it joins the list at once, so the arrival rule
   * above never sees an active id the cache does not hold yet. */
  const create = useOptimisticWrite({
    mutationKey: ['write', 'stats-dashboards', 'create'],
    mutationFn: createStatsDashboard,
    revalidate: [listKey],
    onError: failed('Dashboard se nepodařilo uložit'),
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
      cachePatch({ queryKey: listKey, exact: true }, withRow(id, (d) => ({ ...d, ...patch }))),
    ],
    // The PUT answers the row as stored, so only a failure needs a re-read.
    revalidate: (_vars, refused) => (refused ? [listKey] : []),
    onSuccess: adopt,
    onError: failed('Dashboard se nepodařilo uložit'),
  });

  /* Off the caller's list: the owner deletes it; a recipient leaves with its own
   * grant — the only row in its `shared_with`. */
  const drop = useOptimisticWrite({
    mutationKey: ['write', 'stats-dashboards', 'delete'],
    mutationFn: (d: SavedDashboard) =>
      isOwn(d) ? deleteStatsDashboard(d.id) : unshareStatsDashboard(d.id, d.shared_with[0].account_id),
    patch: (d) => [
      cachePatch<DashboardList>({ queryKey: listKey, exact: true }, (prev) =>
        prev && { data: prev.data.filter((x) => x.id !== d.id), total: Math.max(0, prev.total - 1) },
      ),
    ],
    revalidate: [listKey],
    onError: (err, d) =>
      failed(isOwn(d) ? 'Dashboard se nepodařilo smazat' : 'Sdílený dashboard se nepodařilo odebrat')(err),
  });

  /* Arrival: the URL names a dashboard this tab has not loaded — load it as a
   * click would, but REPLACING the entry (the link's own entry holds no filter,
   * and Back must leave Browse, not land there). One the caller can no longer read
   * clears the param. Judged only on the list as re-read since this mount; a
   * dashboard being deleted or left keeps the param until its write settles, so
   * a refused DELETE leaves it active. */
  const settled = query.isSuccess && query.isFetchedAfterMount && !query.isFetching;
  useEffect(() => {
    if (activeDashboardId == null || !settled) return;
    if (active == null) {
      if (!drop.isPending) view.setActiveDashboardId(null);
    } else if (lastLoaded() !== active.id) load(active, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load and view are re-made each render; these decide.
  }, [activeDashboardId, settled, active, drop.isPending]);

  /* A shared dashboard as the caller's own: its stored charts and filter, its name. */
  const copyAsOwn = (d: SavedDashboard) =>
    create
      .mutateAsync({ name: d.name, charts: sanitizeCharts(d.charts), filter_spec: d.filter_spec ?? undefined, color: d.color })
      .then((created) => {
        if (d.id === activeDashboardId) activate(created.id);
      });

  const chipMenu = (d: SavedDashboard): SavedSetMenuItem[] => {
    if (isOwn(d)) return [{ label: 'Sdílet…', onSelect: () => setSharingId(d.id) }];
    const leave = { label: 'Přestat sledovat', confirm: { label: 'Opravdu přestat sledovat', busy: 'Odcházím…' } };
    return [
      { label: 'Načíst', onSelect: () => load(d) },
      { label: 'Uložit jako vlastní', onSelect: () => copyAsOwn(d) },
      ...(d.shared_with.length > 0 ? [{ ...leave, danger: true, onSelect: () => drop.mutateAsync(d) }] : []),
    ];
  };

  if (!enabled) return null;
  const sharing = items.find((d) => d.id === sharingId);

  return (
    <>
      <SavedSetBar
        {...bar}
        activeId={activeDashboardId}
        dirty={dirty}
        copy={COPY}
        suffix={(d) => d.owner_email}
        chipMenu={chipMenu}
        onLoad={load}
        onSaveNew={(name, withFilter, color) =>
          create
            .mutateAsync({ name, charts: working, filter_spec: withFilter ? currentSpec() : undefined, color })
            .then((created) => activate(created.id))
        }
        onUpdate={(d, name, withFilter, color) => {
          // Unticking the box drops a stored filter; an untouched box keeps it current.
          const filter = withFilter ? { filter_spec: currentSpec() } : d.filter_spec != null ? { filter_spec: null } : {};
          return update.mutateAsync({ id: d.id, patch: { name, color, charts: working, ...filter } });
        }}
        onRename={(d, name, color) => update.mutateAsync({ id: d.id, patch: { name, color } })}
        onDelete={(d) => drop.mutateAsync(d)}
        extra={{
          label: 'Uložit i nastavení filtru',
          initial: (d) => d != null && d.filter_spec != null,
        }}
      />
      {/* "Sdílet…": the one ShareBlock (read-only shares, no modes) in a small dialog. */}
      {sharing ? (
        <Dialog open onClose={() => setSharingId(null)} labelledBy={shareTitleId} className="w-full max-w-lg p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">{COPY.kind}</p>
              <h2 id={shareTitleId} className="mt-1 text-xl leading-tight break-words" style={DISPLAY_TITLE}>
                Sdílet „{sharing.name}“
              </h2>
            </div>
            <DialogClose onClick={() => setSharingId(null)} label="Zavřít" />
          </div>
          <div className="mt-4">
            <ShareBlock
              title="Sdílení"
              lang="cs"
              description="Komu dashboard nasdílíte, uvidí ho mezi svými dashboardy a může si ho načíst nebo uložit jako vlastní. Měnit ho nemůže; vaše uložené změny uvidí."
              recipients={sharing.shared_with}
              onShare={(email) => shareStatsDashboard(sharing.id, email).then(adopt)}
              onRevoke={(accountId) =>
                unshareStatsDashboard(sharing.id, accountId).then(() => {
                  const left = (d: SavedDashboard) => d.shared_with.filter((r) => r.account_id !== accountId);
                  qc.setQueryData(listKey, withRow(sharing.id, (d) => ({ ...d, shared_with: left(d) })));
                  void qc.invalidateQueries({ queryKey: listKey });
                })
              }
            />
          </div>
        </Dialog>
      ) : null}
    </>
  );
}
