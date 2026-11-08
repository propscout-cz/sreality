import { Suspense, type ReactNode } from 'react';

import ErrorBoundary from '@/components/ErrorBoundary';

/* Where a lazy map mounts: nothing shows while its chunk downloads, and a map that throws
 * (its construction, a render, a chunk that cannot load offline or after a reload; an
 * online one reloads the page, lib/lazyChunk) leaves nothing behind but `onError`, so the
 * page around it keeps its own layout. */
export default function MapSlot({ children, onError }: { children: ReactNode; onError: () => void }) {
  return (
    <ErrorBoundary label="map" fallback={null} onError={onError}>
      <Suspense fallback={null}>{children}</Suspense>
    </ErrorBoundary>
  );
}
