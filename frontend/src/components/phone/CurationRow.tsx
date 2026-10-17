/* The property page's curation row on the phone (#1767, the prototype's `.cur`):
 * four thumb-sized cells under the price — pipeline, collection, note, hide —
 * the same four verbs the feed card's margin carries, labelled here because the
 * page has the room.
 *
 * No write of its own (rule 22, rule 18): the funnel is `usePipelineCard` (out →
 * a tap files it at the entry stage; in → the stage sheet, never a remove
 * toggle), the bookmark the collection sheet, the eye `useDismissal` with the
 * SPA's undo toast. "Poznámka" scrolls to the notes section below and puts the
 * cursor in its composer. The reads are the shared ones the feed dedupes onto;
 * a failed membership read is a retry, never the "add" verb (MS16). */

import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import CollectionMark from '@/components/CollectionMark';
import { readFailed } from '@/components/ReadFailedMark';
import { AlertIcon, EyeOffIcon, FunnelIcon, PencilIcon } from '@/components/icons';
import { listPropertyNotes } from '@/lib/api';
import { useCollectionMembership } from '@/lib/collectionCache';
import { curationKeys } from '@/lib/queries';
import { useDismissal } from '@/lib/useDismissal';
import { usePipelineCard } from '@/lib/usePipelineCard';

import { CollectionSheet, StageSheet, type SheetSubject } from './FeedSheets';
import { usePipelineLook } from './MarginMarks';

const CELL =
  'flex h-16 min-w-0 flex-col items-center justify-center gap-1 border-l border-[var(--color-rule)] px-1 text-center text-[11.5px] leading-tight first:border-l-0 active:bg-[var(--color-paper-2)] aria-disabled:opacity-60';

function Cell({
  icon,
  label,
  tone,
  ...props
}: {
  icon: ReactNode;
  label: string;
  tone?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...props} className={CELL} style={tone ? { color: tone } : undefined}>
      <span className={tone ? '' : 'text-[var(--color-ink-3)]'}>{icon}</span>
      <span className={`max-w-full truncate ${tone ? '' : 'text-[var(--color-ink-2)]'}`}>{label}</span>
    </button>
  );
}

export default function CurationRow({
  subject,
  onNote,
}: {
  subject: SheetSubject;
  /* Scroll to the notes section and focus its composer. */
  onNote: () => void;
}) {
  const { property_id } = subject;
  const [sheet, setSheet] = useState<'stage' | 'collections' | null>(null);

  const { membersQ, member, badge, accent } = usePipelineLook(property_id);
  const {
    ids: collectionIds,
    saved,
    query: collectionsQ,
  } = useCollectionMembership(property_id);
  /* NotesRow's own read (same key): the count beside "Poznámka". */
  const notesQ = useQuery({
    queryKey: curationKeys.propertyNotes(property_id),
    queryFn: () => listPropertyNotes(property_id),
    staleTime: 30_000,
  });
  const { add, pending: pipelinePendingFor } = usePipelineCard();
  const { dismissed, dismiss, restore, pending: dismissPending } = useDismissal(property_id);

  const pipelinePending = pipelinePendingFor(property_id);
  const notes = notesQ.data?.data.length ?? 0;

  /* A live deal is never hidden (the API refuses it); a closed one can be. */
  const liveDeal = member != null && !member.is_terminal;
  const hidden = dismissed === true;
  const eyeBlocked = liveDeal || dismissed == null || dismissPending;

  return (
    <div className="grid grid-cols-4 border-t border-[var(--color-rule)]">
      {readFailed(membersQ) ? (
        <Cell
          icon={<AlertIcon className="h-5 w-5" />}
          label="Zkusit znovu"
          tone="var(--color-brick)"
          aria-label="Pipeline se nepodařilo načíst — zkusit znovu"
          onClick={() => void membersQ.refetch()}
        />
      ) : (
        <Cell
          icon={<FunnelIcon filled={member != null} className="h-5 w-5" />}
          label={member ? [badge, member.stage_label].filter(Boolean).join(' · ') : 'Do pipeline'}
          tone={member ? accent.fg : undefined}
          aria-label={member ? `Fáze ${member.stage_label}, změnit` : 'Přidat do pipeline'}
          aria-haspopup={member ? 'dialog' : undefined}
          aria-disabled={pipelinePending || undefined}
          onClick={() => {
            if (pipelinePending) return;
            if (member) setSheet('stage');
            else add.mutate(property_id);
          }}
        />
      )}
      {readFailed(collectionsQ) ? (
        <Cell
          icon={<AlertIcon className="h-5 w-5" />}
          label="Zkusit znovu"
          tone="var(--color-brick)"
          aria-label="Sbírky se nepodařilo načíst — zkusit znovu"
          onClick={() => void collectionsQ.refetch()}
        />
      ) : (
        <Cell
          icon={<CollectionMark filled={saved} className="h-5 w-5" />}
          label={saved ? 'Ve sbírce' : 'Do sbírky'}
          tone={saved ? 'var(--color-ink)' : undefined}
          aria-haspopup="dialog"
          aria-pressed={saved}
          aria-label={saved ? 'Ve sbírce — upravit' : 'Uložit do sbírky'}
          onClick={() => setSheet('collections')}
        />
      )}
      <Cell
        icon={<PencilIcon className="h-5 w-5" strokeWidth={1.75} />}
        label={notes > 0 ? `Poznámka · ${notes}` : 'Poznámka'}
        aria-label={notes > 0 ? `Poznámky (${notes}) — napsat další` : 'Napsat poznámku'}
        onClick={onNote}
      />
      <Cell
        icon={<EyeOffIcon filled={hidden} className="h-5 w-5" />}
        label={hidden ? 'Skryto' : 'Skrýt'}
        tone={hidden ? 'var(--color-ink)' : undefined}
        aria-pressed={hidden}
        aria-disabled={eyeBlocked || undefined}
        aria-label={
          liveDeal ? 'Rozpracovaný obchod nelze skrýt' : hidden ? 'Skryto — znovu zobrazit' : 'Skrýt nemovitost'
        }
        onClick={() => {
          if (eyeBlocked) return;
          if (hidden) restore.mutate();
          else dismiss.mutate();
        }}
      />

      {sheet === 'stage' && member && (
        <StageSheet subject={subject} stageId={member.stage_id} onClose={() => setSheet(null)} />
      )}
      {sheet === 'collections' && (
        <CollectionSheet subject={subject} memberIds={collectionIds} onClose={() => setSheet(null)} />
      )}
    </div>
  );
}
