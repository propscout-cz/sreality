/* The "Nová nemovitost" dialog body — its own chunk (components/NewPropertyProvider
 * opens it). One field, "Odkaz na inzerát", one primary, "Přidat", and the flow's
 * one line of progress (lib/useNewPropertyFlow): Hledám inzerát → Stahuji stránku
 * → Zakládám nemovitost, or one refusal sentence, or the inline failure block. The
 * phone's Odkaz tab renders the same progress line under its own field.
 *
 * Opened from a shared board, the dialog acts through that board's share, so the
 * card lands on the board the operator was looking at. Closing cancels the poll;
 * only the card write itself (a second at most) holds the dialog open. */

import { useCallback, useId, useRef, useState } from 'react';

import Dialog, { DialogClose } from '@/components/Dialog';
import ErrorBanner from '@/components/ErrorBanner';
import Spinner from '@/components/Spinner';
import type { NewPropertyPrefill } from '@/components/NewPropertyProvider';
import { ActingAs } from '@/lib/acting';
import { ApiError } from '@/lib/api';
import { firstUrl } from '@/lib/pastedLink';
import { STEP_LABEL, isBusy, useNewPropertyFlow, type NewPropertyStep } from '@/lib/useNewPropertyFlow';

export default function NewPropertyDialog({
  prefill,
  onClose,
}: {
  prefill: NewPropertyPrefill;
  onClose: () => void;
}) {
  return (
    <ActingAs share={prefill.acting ?? null}>
      <NewPropertyModal prefill={prefill} onClose={onClose} />
    </ActingAs>
  );
}

function NewPropertyModal({ prefill, onClose }: { prefill: NewPropertyPrefill; onClose: () => void }) {
  const titleId = useId();
  const fieldId = useId();
  const fieldRef = useRef<HTMLInputElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  const [text, setText] = useState(prefill.url ?? '');
  const flow = useNewPropertyFlow({ stay: prefill.stay === true, onDone: onClose });
  const busy = isBusy(flow.step);
  const adding = flow.step.kind === 'adding';

  const requestClose = useCallback(() => {
    if (!adding) onClose();
  }, [adding, onClose]);

  const submit = () => {
    if (!text.trim() || busy) return;
    void flow.start(text);
  };

  return (
    <Dialog
      open
      onClose={requestClose}
      labelledBy={titleId}
      initialFocus={prefill.url ? submitRef : fieldRef}
      className="w-full max-w-lg"
    >
      <header className="flex items-baseline justify-between gap-4 px-6 pt-5 pb-3">
        <h2
          id={titleId}
          className="text-[1.35rem] leading-tight"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          Nová nemovitost
        </h2>
        <DialogClose onClick={requestClose} disabled={adding} label="Zavřít" className="-mr-1" />
      </header>

      <div className="px-6 pb-6 text-sm">
        <label
          htmlFor={fieldId}
          className="block text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]"
        >
          Odkaz na inzerát
        </label>
        <div className="mt-2 flex items-stretch gap-2">
          <input
            id={fieldId}
            ref={fieldRef}
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              /* A different link: the old one's progress no longer applies. */
              if (flow.step.kind !== 'idle') flow.cancel();
            }}
            onPaste={(e) => {
              /* A share-sheet message pastes as its link alone; the server still reads whatever is sent. */
              const pasted = e.clipboardData.getData('text');
              const url = firstUrl(pasted);
              if (url && url !== pasted.trim()) {
                e.preventDefault();
                setText(url);
                if (flow.step.kind !== 'idle') flow.cancel();
              }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submit();
              }
            }}
            disabled={adding}
            placeholder="https://…"
            className="flex-1 min-w-0 px-3 py-2 rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
          />
          <button
            ref={submitRef}
            type="button"
            onClick={submit}
            disabled={!text.trim() || busy}
            className="shrink-0 px-4 py-2 rounded-[var(--radius-sm)] border border-[var(--color-copper)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] hover:border-[var(--color-copper-2)] transition-colors disabled:bg-[var(--color-rule-strong)] disabled:border-[var(--color-rule-strong)] disabled:text-[var(--color-ink-4)] disabled:cursor-not-allowed"
          >
            Přidat
          </button>
        </div>
        <NewPropertyProgress step={flow.step} className="mt-3" />
        <p className="mt-4 text-[0.75rem] leading-relaxed text-[var(--color-ink-3)]">
          Inzerát, který máme, přidáme do pipeline hned. Nový nejdřív stáhneme z portálu — obvykle do
          minuty.
        </p>
      </div>
    </Dialog>
  );
}

/* The flow's one line: progress, a refusal sentence, or the failure block. */
export function NewPropertyProgress({ step, className = '' }: { step: NewPropertyStep; className?: string }) {
  if (step.kind === 'idle') return null;
  if (step.kind === 'refused') {
    return (
      <p role="alert" className={`${className} text-[var(--color-brick)]`}>
        {step.message}
      </p>
    );
  }
  if (step.kind === 'error') return <ErrorBlock error={step.error} className={className} />;
  return (
    <p role="status" className={`${className} flex items-center gap-2 text-[var(--color-ink-2)]`}>
      <Spinner size={12} />
      <span>{STEP_LABEL[step.kind]}</span>
    </p>
  );
}

function errorHeadline(error: Error): string {
  if (!(error instanceof ApiError)) return 'Inzerát se nepodařilo přidat.';
  if (error.kind === 'network') return 'Chyba sítě — zkontrolujte připojení.';
  if (error.kind === 'timeout') return 'Server neodpověděl včas.';
  if (error.status === 401) return 'Přihlášení vypršelo — přihlaste se znovu.';
  if (error.status === 403) return 'Přidávat inzeráty mohou zatím jen správci.';
  return `Požadavek selhal (HTTP ${error.status}).`;
}

function ErrorBlock({ error, className }: { error: Error; className: string }) {
  const headline = errorHeadline(error);
  return (
    <div className={className}>
      <ErrorBanner flush title={headline} message={error.message === headline ? '' : error.message} />
    </div>
  );
}
