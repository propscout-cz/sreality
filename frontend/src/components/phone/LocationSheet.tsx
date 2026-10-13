/* The feed card's Poloha sheet: the margin's map-pin opens it over the roll. The feed row
 * carries no coordinates, so the sheet asks the API when it opens (and only then) and
 * draws LocationBody, the same body the property page shows inline. */

import BottomSheet, { SheetContext } from './BottomSheet';
import type { SheetSubject } from './FeedSheets';
import LocationBody from './LocationBody';

export default function LocationSheet({ subject, onClose }: { subject: SheetSubject; onClose: () => void }) {
  return (
    <BottomSheet
      label="Poloha"
      onClose={onClose}
      context={<SheetContext text={subject.text} figure={subject.figure} />}
    >
      <div className="px-4 pt-3">
        <LocationBody propertyId={subject.property_id} />
      </div>
    </BottomSheet>
  );
}
