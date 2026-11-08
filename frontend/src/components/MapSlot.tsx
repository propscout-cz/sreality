import { Suspense, type ReactNode } from 'react';

/* Where a lazy map mounts: nothing shows while its chunk downloads. */
export default function MapSlot({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>;
}
