/* The panel's "Přidat do databáze" flow, free of the DOM and of `chrome` so it
 * runs under `node --test`: POST /listings/ingest, then poll its GET until the
 * ad is held with a property, a refusal sentence, an API error, or the poll
 * budget is spent. The transport is the caller's (content.ts `call`, which goes
 * through the background worker like every other request). */

import type { ApiMessage, ApiResult, IngestState } from './types';

export type IngestStep = 'resolving' | 'fetching' | 'attaching';

/* The SPA dialog's steps, in the panel's own words. */
export const INGEST_STEP_CS: Record<IngestStep, string> = {
  resolving: 'Hledám inzerát…',
  fetching: 'Stahuji stránku…',
  attaching: 'Zakládám nemovitost…',
};

export type IngestOutcome =
  | { kind: 'held'; source: string; nativeId: string; propertyId: number }
  | { kind: 'refused'; message: string }
  | { kind: 'error'; status: number; detail: string }
  | { kind: 'timeout' }
  | { kind: 'cancelled' };

export type IngestFailure = Extract<IngestOutcome, { kind: 'refused' | 'error' | 'timeout' }>;

export interface IngestDeps {
  call: (message: ApiMessage) => Promise<ApiResult<IngestState>>;
  sleep: (ms: number) => Promise<void>;
  /* False once the panel the flow started for is gone: stop asking. */
  alive: () => boolean;
  onStep: (step: IngestStep) => void;
  intervalMs: number;
  /* How many GETs after the POST before the flow gives up waiting. */
  maxPolls: number;
}

export async function runIngest(url: string, deps: IngestDeps): Promise<IngestOutcome> {
  deps.onStep('resolving');
  let res = await deps.call({ type: 'ingest_listing', url });
  for (let polls = 0; deps.alive(); polls++) {
    if (!res.ok) return { kind: 'error', status: res.status, detail: res.detail };
    const s = res.data;
    if (s.state === 'refused' || s.state === 'gone' || s.state === 'failed') {
      return { kind: 'refused', message: s.message_cs ?? 'Inzerát se nepodařilo přidat.' };
    }
    if (s.source == null || s.source_id_native == null) {
      return { kind: 'error', status: 0, detail: 'server nevrátil identitu inzerátu' };
    }
    if (s.state === 'held' && s.property_id != null) {
      return {
        kind: 'held', source: s.source, nativeId: s.source_id_native, propertyId: s.property_id,
      };
    }
    if (polls >= deps.maxPolls) return { kind: 'timeout' };
    deps.onStep(s.state === 'queued' ? 'fetching' : 'attaching');
    await deps.sleep(deps.intervalMs);
    if (!deps.alive()) break;
    res = await deps.call({ type: 'ingest_state', source: s.source, native_id: s.source_id_native });
  }
  return { kind: 'cancelled' };
}

/* The admin gate (ruling 2: admins only in v1) said no. The extension cannot read
 * the claim, so the panel hides its button after this answer, per account. */
export function isAdminGate(outcome: IngestFailure): boolean {
  return outcome.kind === 'error' && outcome.status === 403;
}

/* The one sentence the panel shows for a flow that did not end held. A 403 is
 * the admin gate, whose detail is English. */
export function outcomeMessage(
  outcome: IngestFailure, friendly: (detail: string) => string,
): string {
  switch (outcome.kind) {
    case 'refused':
      return outcome.message;
    case 'error':
      return isAdminGate(outcome)
        ? 'Přidávat inzeráty mohou zatím jen správci.'
        : `Přidání se nepodařilo: ${friendly(outcome.detail)}`;
    case 'timeout':
      return 'Inzerát se zatím nepodařilo přidat, zkuste to za chvíli znovu.';
  }
}
