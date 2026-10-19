/* THE curated-set filter (docs/design/curated-set-scope.md): tags, collections
 * and pipeline stages in one control on the Browse sidebar, the phone's Filtry
 * sheet and the pipeline board's bar. One chip per unit cycles off → in it
 * (copper) → not in it (brick, "− name"); the dedicated "not in" chip closes the
 * row ("V žádné kolekci", "Mimo pipeline", "Bez štítků"), and the pipeline alone
 * leads with the whole-set include chip ("V pipeline"). While the whole set is
 * excluded the unit chips are disabled. A picked unit the list no longer holds
 * (deleted, revoked) stays as a clearable `#id` chip rather than filtering
 * invisibly — the one copy of that rule. A failed read is a retry, never "none
 * yet" (MS16). What a choice MEANS is lib/setScope + migration 629. */

import type { ReactElement, ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { PickButton } from '@/components/controls';
import PipelineMark from '@/components/PipelineMark';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { listTags } from '@/lib/api';
import { collectionLabel, isMine, useCollections } from '@/lib/collectionCache';
import { usePipelineStages } from '@/lib/pipelineReads';
import { stageAccent, stageBadge } from '@/lib/pipelineStage';
import { curationKeys } from '@/lib/queries';
import {
  CURATED_SETS,
  cycleSet,
  cycleUnit,
  normalizeScope,
  type CuratedSet,
  type SetScope,
} from '@/lib/setScope';
import type { Collection, PipelineStage, Tag } from '@/lib/types';

export interface SetUnit {
  id: number;
  label: string;
  /* Drawn before the label: a tag's colour dot, a stage's badge. */
  adorn?: ReactNode;
  group?: 'mine' | 'shared';
}

/* The read the units stand on, as the frame needs it. */
export interface UnitsRead {
  isLoading: boolean;
  isError: boolean;
  data: unknown;
  refetch: () => unknown;
}

const FRAME: Record<CuratedSet, { what: string; empty: string }> = {
  tags: { what: 'Štítky', empty: 'No tags yet. Add one from any property page or pipeline card.' },
  collections: { what: 'Kolekce', empty: 'No collections yet. Create one from the Collections page.' },
  pipeline: { what: 'Pipeline', empty: 'No pipeline stages yet.' },
};

const hint = 'text-[0.65rem] text-[var(--color-ink-4)]';

export function CuratedSetFilter({
  set,
  units,
  value,
  onChange,
  query,
}: {
  set: CuratedSet;
  units: ReadonlyArray<SetUnit>;
  value: SetScope | null;
  onChange: (next: SetScope | null) => void;
  query: UnitsRead;
}) {
  if (query.isLoading) {
    return <p className="text-[0.75rem] text-[var(--color-ink-4)]">Loading…</p>;
  }
  if (readFailed(query)) {
    return <ReadFailedMark what={FRAME[set].what} variant="header" onRetry={() => void query.refetch()} />;
  }
  const d = CURATED_SETS[set];
  const scope = normalizeScope(value);
  if (units.length === 0 && !scope) {
    return <p className="text-[0.75rem] text-[var(--color-ink-4)]">{FRAME[set].empty}</p>;
  }
  const known = new Set(units.map((u) => u.id));
  const vanished = [...(scope?.in ?? []), ...(scope?.out ?? [])].filter((id) => !known.has(id));
  const wholeOut = scope?.set === 'out';

  const unitChip = (u: SetUnit, gone: boolean) => {
    const out = scope?.out.includes(u.id) ?? false;
    const clear = () =>
      normalizeScope(scope && {
        ...scope,
        in: scope.in.filter((id) => id !== u.id),
        out: scope.out.filter((id) => id !== u.id),
      });
    return (
      <PickButton
        key={u.id}
        on={out || (scope?.in.includes(u.id) ?? false)}
        negative={out}
        disabled={wholeOut}
        onClick={() => onChange(gone ? clear() : cycleUnit(scope, u.id))}
        variant="solid"
        className="min-w-16"
        ariaLabel={out ? `${u.label} (excluded)` : undefined}
      >
        {u.adorn ? (
          <span aria-hidden className="mr-1.5 inline-flex align-middle">
            {u.adorn}
          </span>
        ) : null}
        {out ? `− ${u.label}` : u.label}
      </PickButton>
    );
  };
  const setChip = (side: 'in' | 'out', label: string) => (
    <PickButton
      key={`set-${side}`}
      on={scope?.set === side}
      negative={side === 'out'}
      onClick={() => onChange(cycleSet(scope, side))}
      variant="solid"
    >
      {label}
    </PickButton>
  );
  const head = d.inAnyLabel ? setChip('in', d.inAnyLabel) : null;
  const tail = setChip('out', d.notInLabel);

  const mine = units.filter((u) => u.group !== 'shared');
  const shared = units.filter((u) => u.group === 'shared');
  const groups: Array<{ label: string | null; units: SetUnit[]; gone: boolean }> = [];
  if (mine.length) groups.push({ label: shared.length ? 'Mine' : null, units: mine, gone: false });
  if (shared.length) groups.push({ label: 'Shared with me', units: shared, gone: false });
  if (vanished.length) {
    groups.push({
      label: 'No longer available',
      units: vanished.map((id) => ({ id, label: `#${id}` })),
      gone: true,
    });
  }
  const row = (children: ReactNode) => <div className="flex flex-wrap gap-1">{children}</div>;
  const single = groups.length <= 1 && groups[0]?.label == null;

  return (
    <div className="flex flex-col gap-2">
      {single ? (
        row(<>{head}{groups[0]?.units.map((u) => unitChip(u, false))}{tail}</>)
      ) : (
        <>
          {groups.map((g) => (
            <div key={g.label ?? ''} role="group" aria-label={g.label ?? undefined} className="flex flex-col gap-1">
              {g.label ? (
                <p className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">{g.label}</p>
              ) : null}
              {row(g.units.map((u) => unitChip(u, g.gone)))}
            </div>
          ))}
          {row(<>{head}{tail}</>)}
        </>
      )}
      {d.inRule === 'every' && (scope?.in.length ?? 0) > 1 && (
        <p className={hint}>Nemovitost musí mít všechny vybrané štítky.</p>
      )}
    </div>
  );
}

/* The units of each set, as every surface draws them. */
export const tagUnit = (t: Pick<Tag, 'id' | 'name' | 'color'>): SetUnit => ({
  id: t.id,
  label: t.name,
  adorn: (
    <span className="inline-block h-2 w-2 rounded-full" style={{ background: `var(--color-tag-${t.color})` }} />
  ),
});

export const collectionUnit = (c: Collection): SetUnit => ({
  id: c.id,
  label: collectionLabel(c),
  group: isMine(c) ? 'mine' : 'shared',
});

export const stageUnit = (s: PipelineStage, stages: ReadonlyArray<PipelineStage>): SetUnit => ({
  id: s.id,
  label: s.label,
  adorn: (
    <span style={{ color: stageAccent(s).fg }}>
      <PipelineMark badge={stageBadge(s, stages)} />
    </span>
  ),
});

interface AccountProps {
  value: SetScope | null;
  onChange: (next: SetScope | null) => void;
}

function AccountTags(p: AccountProps) {
  const q = useQuery({ queryKey: curationKeys.tags, queryFn: listTags, staleTime: 60_000 });
  return <CuratedSetFilter set="tags" units={(q.data?.data ?? []).map(tagUnit)} query={q} {...p} />;
}

function AccountCollections(p: AccountProps) {
  const { collections, query } = useCollections();
  return <CuratedSetFilter set="collections" units={collections.map(collectionUnit)} query={query} {...p} />;
}

function AccountStages(p: AccountProps) {
  const q = usePipelineStages();
  const stages = q.data ?? [];
  return <CuratedSetFilter set="pipeline" units={stages.map((s) => stageUnit(s, stages))} query={q} {...p} />;
}

const ACCOUNT: Record<CuratedSet, (p: AccountProps) => ReactElement> = {
  tags: AccountTags,
  collections: AccountCollections,
  pipeline: AccountStages,
};

/* The whole account's units of one set, over the shared caches — Browse's
 * sidebar and the phone sheet. */
export function AccountCuratedSetFilter({ set, ...p }: AccountProps & { set: CuratedSet }) {
  const Units = ACCOUNT[set];
  return <Units {...p} />;
}
