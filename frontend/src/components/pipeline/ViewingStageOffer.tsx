/* "Přesunout do fáze prohlídky?": asked once a viewing date is added to a property
 * whose card is not at the board's viewing stage (operator 2026-11-06, #2429). When
 * it asks is lib/viewingStage's rule; the write is lib/usePipelineCard's (rule 22):
 * a move, or, off the board, one add straight at that stage. Read at the moment of
 * asking from the shared board reads, so inside <ActingAs> it is the owner's board,
 * as the note is the owner's. Declining changes nothing. */

import { useId, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import Dialog, { DialogClose } from '@/components/Dialog';
import { actingKey, useActAs } from '@/lib/acting';
import { usePipelineMembers, usePipelineStages } from '@/lib/pipelineReads';
import { pipelineKeys, type PipelineMembers } from '@/lib/queries';
import type { PipelineStage } from '@/lib/types';
import { usePipelineCard } from '@/lib/usePipelineCard';
import { viewingStageOffer, type ViewingStageOffer } from '@/lib/viewingStage';

const PRIMARY =
  'inline-flex h-10 items-center px-4 text-[0.85rem] font-medium rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] transition-colors';
const QUIET =
  'inline-flex h-10 items-center px-3 text-[0.85rem] rounded-[var(--radius-sm)] text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] transition-colors';

export function useViewingStageOffer(propertyId: number): { offer: () => void; dialog: ReactNode } {
  const qc = useQueryClient();
  const actAs = useActAs();
  /* Mounted so the two reads are there when a date is saved; each is the page's shared entry. */
  usePipelineStages();
  usePipelineMembers();
  const { addAt, move } = usePipelineCard();
  const [asked, setAsked] = useState<ViewingStageOffer | null>(null);

  const offer = () => {
    const stages = qc.getQueryData<PipelineStage[]>(actingKey(actAs, pipelineKeys.stages));
    const members = qc.getQueryData<PipelineMembers>(actingKey(actAs, pipelineKeys.members));
    setAsked(viewingStageOffer(stages, members === undefined ? undefined : (members.get(propertyId) ?? null)));
  };

  const accept = (o: ViewingStageOffer) => {
    const vars = { propertyId, stageId: o.stage.id };
    if (o.kind === 'add') addAt.mutate(vars);
    else move.mutate(vars);
    setAsked(null);
  };

  return {
    offer,
    dialog: asked && <OfferDialog offer={asked} onAccept={() => accept(asked)} onDecline={() => setAsked(null)} />,
  };
}

function OfferDialog({
  offer,
  onAccept,
  onDecline,
}: {
  offer: ViewingStageOffer;
  onAccept: () => void;
  onDecline: () => void;
}) {
  const titleId = useId();
  const acceptRef = useRef<HTMLButtonElement>(null);
  const to = `„${offer.stage.label}“`;
  const adding = offer.kind === 'add';
  return (
    <Dialog open onClose={onDecline} labelledBy={titleId} initialFocus={acceptRef} className="w-full max-w-sm p-5">
      <div className="mb-2 flex items-start justify-between gap-3">
        <h2 id={titleId} className="font-display text-[17px] font-semibold leading-6 text-[var(--color-ink)]">
          {adding ? 'Přidat do fáze prohlídky?' : 'Přesunout do fáze prohlídky?'}
        </h2>
        <DialogClose onClick={onDecline} label="Zavřít" />
      </div>
      <p className="text-sm leading-relaxed text-[var(--color-ink-2)]">
        {adding
          ? `Nemovitost zatím není v pipeline. Přidat ji do fáze ${to}?`
          : offer.from?.is_terminal
            ? `Nemovitost je v uzavřené fázi „${offer.from.label}“. Přesunutím do fáze ${to} se obchod znovu otevře.`
            : `Nemovitost je ve fázi „${offer.from?.label}“. Přesunout ji do fáze ${to}?`}
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" onClick={onDecline} className={QUIET}>
          Ne, nechat
        </button>
        <button ref={acceptRef} type="button" onClick={onAccept} className={PRIMARY}>
          {adding ? 'Přidat' : 'Přesunout'}
        </button>
      </div>
    </Dialog>
  );
}
