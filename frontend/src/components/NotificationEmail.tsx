/* The notification email control's eager half: the bar itself is a lazy chunk (NotificationEmailBar.tsx).
 * Offline it is left out: the setting needs the API anyway, and a chunk never fetched cannot load there. */

import { Suspense } from 'react';

import { lazyChunk } from '@/lib/lazyChunk';

const Bar = lazyChunk(() => import('./NotificationEmailBar'));

export default function NotificationEmail({ tone }: { tone: 'desktop' | 'phone' }) {
  return navigator.onLine ? (
    <Suspense fallback={null}>
      <Bar tone={tone} />
    </Suspense>
  ) : null;
}
