/* Compact popup for kicking off a new estimation OR a new building decomposition.
 *
 * Both POST endpoints return a 'pending' row immediately and run the
 * heavy work as a FastAPI BackgroundTask — the modal closes and
 * navigates to the detail page in ~1 s. The detail page polls until
 * status reaches a terminal value.
 *
 * Apartment kind (default):
 *   Paste a listing URL → POST /estimations/preview to extract spec →
 *   POST /estimations to enqueue the run → navigate to the run's surface
 *   (runSurfaceUrl: the listing page's estimations section when the
 *   subject is in our DB, /estimation/:id only for orphan-URL runs).
 *
 * Building kind (Phase B1):
 *   Paste a `dum` / `komercni` URL → POST /buildings/from_url enqueues
 *   extraction → navigate to /building/:id where the operator reviews
 *   the unit proposal once extraction lands. When the operator
 *   uploaded attachments in the modal we additionally wait for the
 *   first extraction pass to finish ('awaiting_input') and then
 *   re-extract so the attachments are folded in.
 *
 * Estimate kind (rent vs sale) for the apartment path is operator-
 * chosen via a segmented control, defaulted to rent. The toggle always
 * wins — pasting a `prodej` URL while the toggle is on Rent still
 * submits a rent estimate. Provider and lifecycle fall back to the
 * same defaults the form used to ship with (Claude + active).
 */

import { Suspense, createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { lazyChunk } from '@/lib/lazyChunk';
import { useCloseOnNavigation } from '@/lib/useCloseOnNavigation';

const NewEstimationModalBody = lazyChunk(() => import('./NewEstimationModalBody'));

export type EstimateKind = 'rent' | 'sale';
export type CategoryMain = 'byt' | 'dum' | 'komercni';

export interface NewEstimationPrefill {
  url?: string;
  categoryMain?: CategoryMain;
  estimateKind?: EstimateKind;
}

interface ModalCtx {
  open: (prefill?: NewEstimationPrefill) => void;
  close: () => void;
  isOpen: boolean;
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
  const [isOpen, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<NewEstimationPrefill | null>(null);
  /* This provider mounts above <Outlet> in Shell, so without this a navigation
   * started from inside the modal — a submit that navigates to the run's
   * surface, or anything a future body renders — would change the page BEHIND
   * the open modal and leave the body scroll lock on. See
   * lib/useCloseOnNavigation.ts. */
  useCloseOnNavigation(() => setOpen(false));
  const value = useMemo<ModalCtx>(
    () => ({
      open: (p?: NewEstimationPrefill) => {
        setPrefill(p ?? null);
        setOpen(true);
      },
      close: () => setOpen(false),
      isOpen,
    }),
    [isOpen],
  );
  return (
    <ctx.Provider value={value}>
      {children}
      {isOpen && (
        <Suspense fallback={null}>
          <NewEstimationModalBody onClose={() => setOpen(false)} prefill={prefill} />
        </Suspense>
      )}
    </ctx.Provider>
  );
}
