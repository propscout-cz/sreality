import { Navigate, useSearchParams } from 'react-router-dom';

import { PhoneSuspense } from '@/components/phone/RollParts';
import { brokerBoardPath } from '@/lib/browseState';
import { lazyChunk } from '@/lib/lazyChunk';
import { usePhoneShell } from '@/lib/phoneShell';

const PhoneBrokers = lazyChunk(() => import('./phone/PhoneBrokers'));

/* /brokers. The board is Browse's Makléři tab (docs/design/broker-board.md):
 * a desktop visit lands there with whatever the link carried; the phone keeps
 * its own Makléři screen in its nav slot, over the same URL. */
export default function Brokers() {
  const [searchParams] = useSearchParams();
  const phone = usePhoneShell();
  return phone ? (
    <PhoneSuspense>
      <PhoneBrokers />
    </PhoneSuspense>
  ) : (
    <Navigate to={brokerBoardPath(searchParams)} replace />
  );
}
