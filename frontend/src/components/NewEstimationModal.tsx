/* Compact popup that starts an estimation of ONE ad we hold.
 *
 * Opened from the property page (its header button, its estimations section),
 * always with the canonical ad's `listing_id`: an estimation is always of an
 * ad we hold, so there is no URL field — a pasted link becomes an ad first
 * through "Nová nemovitost" (docs/design/new-property-from-link.md §3.5).
 *
 * POST /estimations returns a 'pending' row immediately and runs the heavy
 * work as a FastAPI BackgroundTask — the modal closes and navigates to the
 * run's surface (runSurfaceUrl: the ad's page, estimations section) in ~1 s,
 * where the section polls until the status is terminal.
 *
 * The estimate kind (rent vs sale) and the property type are operator-chosen,
 * seeded from the ad; the toggle always wins. Provider and lifecycle fall back
 * to the same defaults the form used to ship with (Claude + active).
 */

import { Suspense, createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { lazyChunk } from '@/lib/lazyChunk';
import { useCloseOnNavigation } from '@/lib/useCloseOnNavigation';

const NewEstimationModalBody = lazyChunk(() => import('./NewEstimationModalBody'));

export type EstimateKind = 'rent' | 'sale';
export type CategoryMain = 'byt' | 'dum' | 'komercni';

export interface NewEstimationPrefill {
  listingId: number;
  categoryMain?: CategoryMain;
  estimateKind?: EstimateKind;
}

interface ModalCtx {
  open: (prefill: NewEstimationPrefill) => void;
}

const ctx = createContext<ModalCtx | null>(null);

export function useNewEstimationModal(): ModalCtx {
  const v = useContext(ctx);
  if (!v) {
    throw new Error('useNewEstimationModal must be used inside <NewEstimationProvider>');
  }
  return v;
}

export function NewEstimationProvider({ children }: { children: ReactNode }) {
  const [prefill, setPrefill] = useState<NewEstimationPrefill | null>(null);
  /* This provider mounts above <Outlet> in Shell, so without this a navigation
   * started from inside the modal — a submit that navigates to the run's
   * surface, or anything a future body renders — would change the page BEHIND
   * the open modal and leave the body scroll lock on. See
   * lib/useCloseOnNavigation.ts. */
  useCloseOnNavigation(() => setPrefill(null));
  const value = useMemo<ModalCtx>(() => ({ open: setPrefill }), []);
  return (
    <ctx.Provider value={value}>
      {children}
      {prefill && (
        <Suspense fallback={null}>
          <NewEstimationModalBody onClose={() => setPrefill(null)} prefill={prefill} />
        </Suspense>
      )}
    </ctx.Provider>
  );
}
