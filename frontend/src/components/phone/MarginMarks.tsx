/* The feed card's margin (#1762's signature): a 44 px translucent paper strip
 * over the photo's right edge holding the extension's three actions — funnel,
 * bookmark, eye — and the map-pin that opens Poloha, like ticks in a notebook
 * margin, with the photo count at its foot (four 48 px marks and the foot still
 * fit the 292 px photo of a 390 px phone). Only the marks take a touch: the column's bare paper passes it through,
 * so a swipe that starts there still turns the card's photos (CardPhotoStrip).
 *
 * Every write is the SPA's own, unchanged (rule 22): the funnel is
 * `usePipelineCard` (out → a tap adds at the entry stage; in → the stage menu,
 * never a remove toggle), the bookmark is the collection save list, the eye is
 * `useDismissal` (hide with an undo toast; on a hidden property, restore), and
 * each settles the feed's own Browse read in place (lib/curationSettle); the pin
 * writes nothing (the location sheet reads only while open). The
 * reads are the shared ones every Browse card dedupes onto: pipeline members +
 * stages, the collection member map, the per-property dismissal state. A failed
 * membership read is a retry, never the "add" verb (MS16). */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import CollectionMark from '@/components/CollectionMark';
import { readFailed } from '@/components/ReadFailedMark';
import { AlertIcon, EyeOffIcon, FunnelIcon } from '@/components/icons';
import { useCollectionMembership } from '@/lib/collectionCache';
import {
  fetchPipelineMembers,
  fetchPipelineStages,
  pipelineKeys,
} from '@/lib/queries';
import { stageAccent, stageBadge } from '@/lib/pipelineStage';
import { useDismissal } from '@/lib/useDismissal';
import { usePipelineCard } from '@/lib/usePipelineCard';

import { CollectionSheet, StageSheet, type SheetSubject } from './FeedSheets';
import LocationSheet from './LocationSheet';
import { PinIcon } from './phoneIcons';

const MARK =
  'pointer-events-auto flex h-12 w-full flex-col items-center justify-center gap-0.5 transition-colors aria-disabled:opacity-60';
const IDLE = 'text-[var(--color-ink-3)] active:text-[var(--color-ink)]';
const PRESSED = 'text-[var(--color-ink)]';

function Tick() {
  return <span aria-hidden className="h-px w-4 bg-[var(--color-rule-strong)]" />;
}

function RetryMark({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <button
      type="button"
      onClick={onRetry}
      aria-label={`${what} se nepodařilo načíst — zkusit znovu`}
      className={`${MARK} text-[var(--color-brick)]`}
    >
      <AlertIcon className="h-5 w-5" />
    </button>
  );
}

/* The property's pipeline card as the feed paints it: the shared members +
 * stages reads (deduped across every card), the badge and the accent. The card
 * uses it for the stage wash, the margin for the funnel. */
export function usePipelineLook(property_id: number) {
  const membersQ = useQuery({
    queryKey: pipelineKeys.members,
    queryFn: fetchPipelineMembers,
    staleTime: 30_000,
  });
  const stagesQ = useQuery({
    queryKey: pipelineKeys.stages,
    queryFn: fetchPipelineStages,
    staleTime: 60_000,
  });
  const member = membersQ.data?.get(property_id) ?? null;
  const look = member ? { id: member.stage_id, color: member.stage_color, code: member.stage_code } : null;
  return {
    membersQ,
    member,
    badge: look ? stageBadge(look, stagesQ.data ?? []) : null,
    accent: stageAccent(look),
  };
}

export interface MarginMarksProps {
  subject: SheetSubject;
  /* How many photos the property's card carousel holds; null while loading. */
  photoCount: number | null;
}

export default function MarginMarks({ subject, photoCount }: MarginMarksProps) {
  const { property_id } = subject;
  const [sheet, setSheet] = useState<'stage' | 'collections' | 'location' | null>(null);

  const { membersQ, member, badge, accent } = usePipelineLook(property_id);
  const {
    ids: collectionIds,
    saved,
    query: collectionsQ,
  } = useCollectionMembership(property_id);
  const { add, pending: pipelinePendingFor } = usePipelineCard();
  const { dismissed, dismiss, restore, pending: dismissPending } = useDismissal(property_id);

  const pipelinePending = pipelinePendingFor(property_id);

  /* A live deal and a dismissal never coexist (the API refuses it); a deal closed
   * into a terminal stage is history and can be hidden (DismissButton's rule). */
  const liveDeal = member != null && !member.is_terminal;
  const hidden = dismissed === true;
  const eyeBlocked = liveDeal || dismissed == null || dismissPending;

  return (
    <div className="pointer-events-none absolute inset-y-0 right-0 z-[var(--z-card-action)] flex w-11 flex-col items-center border-l border-[var(--color-rule-strong)] bg-[var(--color-paper-2)]/88 pt-1">
      {readFailed(membersQ) ? (
        <RetryMark what="Pipeline" onRetry={() => void membersQ.refetch()} />
      ) : (
        <button
          type="button"
          onClick={() => {
            if (pipelinePending) return;
            if (member) setSheet('stage');
            else add.mutate(property_id);
          }}
          aria-disabled={pipelinePending || undefined}
          aria-haspopup={member ? 'dialog' : undefined}
          aria-label={member ? `Fáze ${member.stage_label}, změnit` : 'Přidat do pipeline'}
          className={`${MARK} ${member ? '' : IDLE}`}
          style={member ? { color: accent.fg } : undefined}
        >
          <FunnelIcon filled={member != null} className="h-5 w-5" />
          {badge && (
            <span className="font-mono text-[10px] font-medium leading-none tabular-nums">{badge}</span>
          )}
        </button>
      )}
      <Tick />
      {readFailed(collectionsQ) ? (
        <RetryMark what="Sbírky" onRetry={() => void collectionsQ.refetch()} />
      ) : (
        <button
          type="button"
          onClick={() => setSheet('collections')}
          aria-haspopup="dialog"
          aria-pressed={saved}
          aria-label={saved ? 'Ve sbírce — upravit' : 'Uložit do sbírky'}
          className={`${MARK} ${saved ? PRESSED : IDLE}`}
        >
          <CollectionMark filled={saved} className="h-5 w-5" />
        </button>
      )}
      <Tick />
      <button
        type="button"
        onClick={() => {
          if (eyeBlocked) return;
          if (hidden) restore.mutate();
          else dismiss.mutate();
        }}
        aria-disabled={eyeBlocked || undefined}
        aria-pressed={hidden}
        aria-label={
          liveDeal
            ? 'Rozpracovaný obchod nelze skrýt'
            : hidden
              ? 'Skryto — znovu zobrazit'
              : 'Skrýt nemovitost'
        }
        className={`${MARK} ${hidden ? PRESSED : IDLE}`}
      >
        <EyeOffIcon filled={hidden} className="h-5 w-5" />
      </button>
      <Tick />
      <button
        type="button"
        onClick={() => setSheet('location')}
        aria-haspopup="dialog"
        aria-label="Poloha"
        className={`${MARK} ${IDLE}`}
      >
        <PinIcon className="h-5 w-5" />
      </button>
      {photoCount != null && photoCount > 0 && (
        <span className="mt-auto pb-2 text-center font-mono text-[10px] leading-[1.3] tabular-nums text-[var(--color-ink-3)]">
          {photoCount}
          <br />
          fot.
        </span>
      )}

      {sheet === 'stage' && member && (
        <StageSheet subject={subject} stageId={member.stage_id} onClose={() => setSheet(null)} />
      )}
      {sheet === 'collections' && (
        <CollectionSheet subject={subject} memberIds={collectionIds} onClose={() => setSheet(null)} />
      )}
      {sheet === 'location' && <LocationSheet subject={subject} onClose={() => setSheet(null)} />}
    </div>
  );
}
