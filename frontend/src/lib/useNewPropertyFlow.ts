/* The paste-a-link flow (docs/design/new-property-from-link.md §3.6): one pasted
 * text → the ad it names → that ad's property → its pipeline card.
 *
 * The server is authoritative: the text goes to POST /listings/ingest as-is and
 * the answer is one state. While the worker fetches the page (`queued`) or the
 * maintenance lane births the ad's property (`held` with no property yet) the
 * flow re-reads GET /listings/ingest/{source}/{native} every 2 s, with NO cap — a
 * slow birth keeps "Zakládám nemovitost" on screen instead of giving up. A
 * transient failure of one re-read is retried on the same beat; any other error
 * ends the flow inline. Then the card is added through the one card hook (rule
 * 22), under whatever <ActingAs> surrounds the caller.
 *
 * `stay` (the Pipeline board): a toast with "Otevřít" and the page stays; every
 * other surface navigates to the property. A new start, cancel() or unmount
 * aborts the poll; the server keeps what it queued. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ingestListing, ingestState, isTransientApiError } from '@/lib/api';
import { ROUTES } from '@/lib/routes';
import { dismissToast, pushToast } from '@/lib/toast';
import type { IngestState } from '@/lib/types';
import { usePipelineCard } from '@/lib/usePipelineCard';

export const INGEST_POLL_MS = 2_000;

export type NewPropertyStep =
  | { kind: 'idle' }
  | { kind: 'looking' }
  | { kind: 'fetching' }
  | { kind: 'attaching' }
  | { kind: 'adding' }
  | { kind: 'refused'; message: string }
  | { kind: 'error'; error: Error };

export type BusyStep = Extract<NewPropertyStep, { kind: 'looking' | 'fetching' | 'attaching' | 'adding' }>;

export const STEP_LABEL: Record<BusyStep['kind'], string> = {
  looking: 'Hledám inzerát…',
  fetching: 'Stahuji stránku…',
  attaching: 'Zakládám nemovitost…',
  adding: 'Přidávám do pipeline…',
};

export const isBusy = (step: NewPropertyStep): step is BusyStep => step.kind in STEP_LABEL;

/* The server always sends its sentence; this covers a state this build does not know. */
const UNKNOWN_REFUSAL = 'Inzerát se nepodařilo přidat.';

const IDLE: NewPropertyStep = { kind: 'idle' };

const sleep = (ms: number, signal: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort, { once: true });
  });

async function reread(prev: IngestState, signal: AbortSignal): Promise<IngestState> {
  const { source, source_id_native: nativeId } = prev;
  if (source == null || nativeId == null) throw new Error('Odpověď serveru nenese inzerát.');
  for (;;) {
    await sleep(INGEST_POLL_MS, signal);
    try {
      return await ingestState(source, nativeId, { signal });
    } catch (err) {
      if (!isTransientApiError(err)) throw err;
    }
  }
}

export function useNewPropertyFlow({
  stay,
  onDone,
}: {
  stay: boolean;
  /* The card is on the board (added now or already there), before the toast. */
  onDone?: (state: IngestState) => void;
}) {
  const navigate = useNavigate();
  const [step, setStep] = useState<NewPropertyStep>(IDLE);
  const run = useRef<AbortController | null>(null);
  /* The failure is shown inline (the step), never as the global toast. */
  const addCard = usePipelineCard({ onAddError: () => {} }).add.mutateAsync;
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => () => run.current?.abort(), []);

  const cancel = useCallback(() => {
    run.current?.abort();
    run.current = null;
    setStep(IDLE);
  }, []);

  const start = useCallback(
    async (text: string) => {
      run.current?.abort();
      const controller = new AbortController();
      run.current = controller;
      const { signal } = controller;
      const show = (next: NewPropertyStep) => {
        if (!signal.aborted) setStep(next);
      };
      try {
        show({ kind: 'looking' });
        let state = await ingestListing(text, { signal });
        while (!(state.state === 'held' && state.property_id != null)) {
          if (state.state !== 'held' && state.state !== 'queued') {
            show({ kind: 'refused', message: state.message_cs ?? UNKNOWN_REFUSAL });
            return;
          }
          show({ kind: state.state === 'queued' ? 'fetching' : 'attaching' });
          state = await reread(state, signal);
        }
        const propertyId = state.property_id;
        show({ kind: 'adding' });
        const card = await addCard(propertyId);
        if (signal.aborted) return;
        show(IDLE);
        doneRef.current?.(state);
        const to = ROUTES.property.build({ propertyId });
        const message = !card.added
          ? card.stage_label
            ? `Už je v pipeline · ${card.stage_label}`
            : 'Už je v pipeline'
          : state.is_active === false
            ? 'Inzerát je neaktivní, karta přidána'
            : 'Přidáno do pipeline';
        const kind = card.added ? 'ok' : 'info';
        if (stay) {
          /* ttl 0: an action toast must stay reachable (lib/toast). */
          const id = pushToast(kind, message, 0, {
            label: 'Otevřít',
            onClick: () => {
              dismissToast(id);
              navigate(to);
            },
          });
        } else {
          pushToast(kind, message);
          navigate(to);
        }
      } catch (err) {
        if (signal.aborted) return;
        show({ kind: 'error', error: err instanceof Error ? err : new Error(String(err)) });
      }
    },
    [addCard, navigate, stay],
  );

  return { step, start, cancel };
}
