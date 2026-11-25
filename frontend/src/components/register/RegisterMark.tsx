/* The register's marks, behind one lazy chunk: the property page is on the first-visit path and the marks are
 * not (they wait for the building read anyway), so they must not weigh on the entry bundle. Nothing is drawn
 * while the chunk loads, which is what a property with no register building draws for good. */
import { Suspense, type ComponentProps } from 'react';
import { lazyChunk } from '@/lib/lazyChunk';

const Mark = lazyChunk(() => import('./marks').then((m) => ({ default: m.RegisterMark })));
const Badge = lazyChunk(() => import('./marks').then((m) => ({ default: m.RegisterBadge })));

export const RegisterMark = (props: ComponentProps<typeof Mark>) => (
  <Suspense fallback={null}><Mark {...props} /></Suspense>
);
export const RegisterBadge = (props: ComponentProps<typeof Badge>) => (
  <Suspense fallback={null}><Badge {...props} /></Suspense>
);
