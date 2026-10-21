/* "Nová nemovitost" — paste an ad link, get its property and its pipeline card
 * (docs/design/new-property-from-link.md §3.6). The provider/hook pair of
 * NewEstimationModal, mounted once in Shell; the Pipeline header, the Browse
 * summary and the property page's "we don't have that ad" state open it.
 *
 * Kept tiny on purpose: Browse and Pipeline are eager imports and the first visit
 * has ~726 B of headroom (scripts/bundle-budget.mjs), so the dialog body is its
 * own chunk, fetched on the first open. Admins only in v1 (ruling 2: the route
 * is `require_admin`): for anyone else the hook answers null and no trigger
 * renders. The value never changes while the dialog opens or closes, so a
 * trigger inside Browse is not re-rendered by it. */

import { Suspense, createContext, useContext, useMemo, useState, type ReactNode } from 'react';

import { useAuth } from '@/lib/auth';
import { lazyChunk } from '@/lib/lazyChunk';
import { useCloseOnNavigation } from '@/lib/useCloseOnNavigation';
import type { PipelineShare } from '@/lib/types';

const NewPropertyDialog = lazyChunk(() => import('./NewPropertyDialog'));

export interface NewPropertyPrefill {
  /* The pasted link, when the opener already has one. */
  url?: string;
  /* Pipeline: stay on the board (a toast offers the property). Else navigate to it. */
  stay?: boolean;
  /* The board the card lands on: the share the opener acts through (lib/acting). */
  acting?: PipelineShare | null;
}

interface NewPropertyCtx {
  open: (prefill?: NewPropertyPrefill) => void;
}

const ctx = createContext<NewPropertyCtx | null>(null);

/* Null outside the provider and for a non-admin: the caller renders no trigger. */
export const useNewPropertyDialog = (): NewPropertyCtx | null => useContext(ctx);

export function NewPropertyProvider({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  const [prefill, setPrefill] = useState<NewPropertyPrefill | null>(null);
  useCloseOnNavigation(() => setPrefill(null));
  const value = useMemo(() => (isAdmin ? { open: (p?: NewPropertyPrefill) => setPrefill(p ?? {}) } : null), [isAdmin]);
  return (
    <ctx.Provider value={value}>
      {children}
      {prefill && (
        <Suspense fallback={null}>
          <NewPropertyDialog prefill={prefill} onClose={() => setPrefill(null)} />
        </Suspense>
      )}
    </ctx.Provider>
  );
}
