/* The feed card's two menus as bottom sheets. Neither owns a write: the stage
 * sheet is `PipelineStageMenuBody` (rule 22's one menu, two-step remove and
 * all) and the collection sheet is `CollectionSaveList` — the same bodies the
 * desktop popovers hold, in the phone's container. */

import { CollectionSaveList } from '@/components/CollectionSaveMenu';
import { PipelineStageMenuBody } from '@/components/pipeline/PipelineStageMenu';

import BottomSheet, { SheetContext } from './BottomSheet';

export interface SheetSubject {
  property_id: number;
  /* "2+kk · 54 m² · Praha 9" — what the sheet acts on. */
  text: string;
  /* The price as the card prints it. */
  figure: string | null;
}

export function StageSheet({
  subject,
  stageId,
  onClose,
}: {
  subject: SheetSubject;
  stageId: number;
  onClose: () => void;
}) {
  return (
    <BottomSheet
      label="Přesunout do fáze"
      onClose={onClose}
      context={<SheetContext text={subject.text} figure={subject.figure} />}
    >
      <PipelineStageMenuBody
        property_id={subject.property_id}
        stageId={stageId}
        onClose={onClose}
        size="sheet"
      />
    </BottomSheet>
  );
}

export function CollectionSheet({
  subject,
  memberIds,
  onClose,
}: {
  subject: SheetSubject;
  memberIds: Set<number>;
  onClose: () => void;
}) {
  return (
    <BottomSheet
      label="Uložit do sbírky"
      onClose={onClose}
      context={<SheetContext text={subject.text} figure={subject.figure} />}
    >
      <CollectionSaveList property_id={subject.property_id} memberIds={memberIds} size="sheet" />
    </BottomSheet>
  );
}
