/* The estimation modal's body — loaded on first open (lazyChunk) so the first-visit bundle
 * carries only the provider; see NewEstimationModal.tsx. */
import { useCallback, useId, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Dialog, { DialogClose } from '@/components/Dialog';
import Spinner from '@/components/Spinner';
import type { ApiError } from '@/lib/api';
import { estimationKeys, submitEstimation } from '@/lib/queries';
import { runSurfaceUrl } from '@/lib/runLinks';
import type { CreateEstimationIn, EstimationRun } from '@/lib/types';
import type { CategoryMain, EstimateKind, NewEstimationPrefill } from './NewEstimationModal';

/* -------------------------------------------------------------------------- */
/* Modal                                                                      */
/* -------------------------------------------------------------------------- */


const CATEGORY_LABELS: Record<CategoryMain, string> = {
  byt: 'Apartment',
  dum: 'House',
  komercni: 'Commercial',
};

/* Czech words the platform stores in listings.category_type, derived from
 * the rent/sale toggle. Sent explicitly so the estimation request never
 * silently defaults to apartments-for-rent. */
function categoryTypeFor(kind: EstimateKind): string {
  return kind === 'rent' ? 'pronajem' : 'prodej';
}

export default function NewEstimationModal({
  onClose,
  prefill,
}: {
  onClose: () => void;
  prefill: NewEstimationPrefill;
}) {
  const [estimateKind, setEstimateKind] = useState<EstimateKind>(
    prefill.estimateKind ?? 'rent',
  );
  const [categoryMain, setCategoryMain] = useState<CategoryMain>(
    prefill.categoryMain ?? 'byt',
  );
  const [specialInstructions, setSpecialInstructions] = useState('');
  const [contextualText, setContextualText] = useState('');
  const titleId = useId();
  const submitRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const submitMut = useMutation<EstimationRun, ApiError, void>({
    mutationFn: () =>
      submitEstimation(
        buildEstimationPayload(
          prefill.listingId, estimateKind, categoryMain,
          specialInstructions, contextualText,
        ),
      ),
    onSuccess: (run) => {
      // The new run must show up immediately in any mounted run list (the
      // listing page's estimations section we may be navigating to).
      qc.invalidateQueries({ queryKey: estimationKeys.all });
      onClose();
      navigate(runSurfaceUrl(run));
    },
  });

  const pending = submitMut.isPending;

  const submit = useCallback(() => {
    if (pending) return;
    submitMut.mutate();
  }, [pending, submitMut]);

  /* Escape, the focus trap, focus restore, the backdrop click and the body
   * scroll lock all come from <Dialog> (lib/useDialog.ts). What does NOT come
   * from the primitive is the in-flight guard: a submit is already running
   * against the API, so every dismissal gesture (Escape, backdrop, the close
   * glyph) stays inert until it settles. useDialog reads onClose through a
   * ref, so this changing identity re-registers nothing. */
  const requestClose = useCallback(() => {
    if (pending) return;
    onClose();
  }, [onClose, pending]);

  const categoryWord = CATEGORY_LABELS[categoryMain].toLowerCase();
  const dealWord = estimateKind === 'rent' ? 'rent' : 'sale';

  return (
    <Dialog
      open
      onClose={requestClose}
      labelledBy={titleId}
      /* The subject is fixed, so the primary action is the one thing to do. */
      initialFocus={submitRef}
      className="w-full max-w-xl"
    >
      <header className="flex items-baseline justify-between gap-4 px-6 pt-5 pb-3">
        <div>
          <p className="text-[0.65rem] tracking-[0.22em] uppercase text-[var(--color-ink-3)]">
            New estimation
          </p>
          <h2
            id={titleId}
            className="mt-1 text-[1.35rem] leading-tight"
            style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
          >
            Estimate this property
          </h2>
        </div>
        {/* The one close glyph (components/Dialog.tsx), DISABLED — not merely
          * dimmed — while a submit is in flight: a disabled control is announced
          * as unavailable and stays out of the focus trap. requestClose guards
          * the other two gestures (Escape, the backdrop) the same way. */}
        <DialogClose onClick={requestClose} disabled={pending} className="-mr-1" />
      </header>

      <div className="px-6 pb-6">
        <div className="flex flex-wrap items-start gap-x-8 gap-y-2">
          <CategoryMainToggle
            categoryMain={categoryMain}
            setCategoryMain={setCategoryMain}
            disabled={pending}
          />
          <EstimateKindToggle
            estimateKind={estimateKind}
            setEstimateKind={setEstimateKind}
            disabled={pending}
          />
        </div>

        <div className="mt-4 flex items-stretch justify-end gap-2">
          <button
            ref={submitRef}
            type="button"
            onClick={submit}
            disabled={pending}
            className={[
              'shrink-0 px-4 py-2 text-sm rounded-[var(--radius-sm)] border transition-colors',
              pending
                ? 'bg-[var(--color-rule-strong)] text-[var(--color-ink-4)] border-[var(--color-rule-strong)] cursor-not-allowed'
                : 'bg-[var(--color-copper)] text-[var(--color-on-copper)] border-[var(--color-copper)] hover:bg-[var(--color-copper-2)] hover:border-[var(--color-copper-2)]',
            ].join(' ')}
          >
            {pending ? (
              <span className="inline-flex items-center gap-2">
                <Spinner size={12} /><span>Opening…</span>
              </span>
            ) : 'Estimate'}
          </button>
        </div>

        {submitMut.error && <ErrorBlock error={submitMut.error} />}

        <OperatorInputs
          specialInstructions={specialInstructions}
          setSpecialInstructions={setSpecialInstructions}
          contextualText={contextualText}
          setContextualText={setContextualText}
          disabled={pending}
        />

        <p className="mt-4 text-[0.75rem] text-[var(--color-ink-3)] leading-relaxed">
          We kick off the {dealWord} estimate of this {categoryWord}. Once the run
          opens you can navigate away — it finishes in the background.
        </p>
      </div>
    </Dialog>
  );
}

function OperatorInputs({
  specialInstructions,
  setSpecialInstructions,
  contextualText,
  setContextualText,
  disabled,
}: {
  specialInstructions: string;
  setSpecialInstructions: (v: string) => void;
  contextualText: string;
  setContextualText: (v: string) => void;
  disabled: boolean;
}) {
  const instructionsId = useId();
  const contextId = useId();
  return (
    <details
      className="mt-4 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper)]"
    >
      <summary
        className="cursor-pointer select-none px-3 py-2 text-[0.72rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]"
      >
        Operator context
        {(specialInstructions || contextualText) && (
          <span className="ml-2 text-[var(--color-copper)] normal-case tracking-normal text-[0.7rem]">
            {[
              specialInstructions && 'instructions',
              contextualText && 'context',
            ].filter(Boolean).join(' · ')}
          </span>
        )}
      </summary>
      <div className="px-3 pb-3 pt-1 space-y-3">
        <div>
          <label
            htmlFor={instructionsId}
            className="block text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]"
          >
            Special instructions
          </label>
          <textarea
            id={instructionsId}
            value={specialInstructions}
            onChange={(e) => setSpecialInstructions(e.target.value)}
            disabled={disabled}
            rows={2}
            maxLength={10_000}
            placeholder="e.g. Use a 1500m radius. Weight comparables in the same block heavier."
            className="mt-1 w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
          />
          <p className="mt-1 text-[0.7rem] text-[var(--color-ink-3)]">
            Wrapped in &lt;operator_instructions&gt; in the agent's prompt.
          </p>
        </div>
        <div>
          <label
            htmlFor={contextId}
            className="block text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]"
          >
            Property context
          </label>
          <textarea
            id={contextId}
            value={contextualText}
            onChange={(e) => setContextualText(e.target.value)}
            disabled={disabled}
            rows={3}
            maxLength={20_000}
            placeholder="Anything the listing doesn't say: legal status, recent renovations, neighbours, planning, etc."
            className="mt-1 w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-60"
          />
        </div>
      </div>
    </details>
  );
}

/* -------------------------------------------------------------------------- */
/* Selectors                                                                  */
/* -------------------------------------------------------------------------- */

function CategoryMainToggle({
  categoryMain, setCategoryMain, disabled,
}: {
  categoryMain: CategoryMain;
  setCategoryMain: (c: CategoryMain) => void;
  disabled: boolean;
}) {
  return (
    <div className="mt-3">
      <span className="block text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        Property type
      </span>
      <div
        role="radiogroup"
        aria-label="Property category"
        className="mt-2 inline-flex items-stretch gap-0 rounded-[var(--radius-sm)] border border-[var(--color-rule)] overflow-hidden bg-[var(--color-inset)]"
      >
        {(['byt', 'dum', 'komercni'] as const).map((c) => (
          <EstimateKindButton
            key={c}
            active={categoryMain === c}
            onClick={() => setCategoryMain(c)}
            disabled={disabled}
            label={CATEGORY_LABELS[c]}
          />
        ))}
      </div>
    </div>
  );
}

function EstimateKindToggle({
  estimateKind, setEstimateKind, disabled,
}: {
  estimateKind: EstimateKind;
  setEstimateKind: (k: EstimateKind) => void;
  disabled: boolean;
}) {
  return (
    <div className="mt-3">
      <span className="block text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        Estimate
      </span>
      <div
        role="radiogroup"
        aria-label="Estimate rent or sale price"
        className="mt-2 inline-flex items-stretch gap-0 rounded-[var(--radius-sm)] border border-[var(--color-rule)] overflow-hidden bg-[var(--color-inset)]"
      >
        <EstimateKindButton
          active={estimateKind === 'rent'}
          onClick={() => setEstimateKind('rent')}
          disabled={disabled}
          label="Rent"
        />
        <EstimateKindButton
          active={estimateKind === 'sale'}
          onClick={() => setEstimateKind('sale')}
          disabled={disabled}
          label="Sale"
        />
      </div>
    </div>
  );
}

function EstimateKindButton({
  active, onClick, disabled, label,
}: {
  active: boolean;
  onClick: () => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      disabled={disabled}
      className={[
        'px-4 py-1.5 text-[0.8rem] transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
        active
          ? 'bg-[var(--color-paper)] text-[var(--color-ink)]'
          : 'bg-transparent text-[var(--color-ink-3)] hover:text-[var(--color-ink)]',
      ].join(' ')}
      style={{ fontWeight: active ? 600 : 500 }}
    >
      {label}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function buildEstimationPayload(
  listingId: number,
  estimateKind: EstimateKind,
  categoryMain: CategoryMain,
  specialInstructions: string,
  contextualText: string,
): CreateEstimationIn {
  const mode = estimateKind === 'rent' ? 'agent' : 'deterministic';
  return {
    source: 'ui',
    mode,
    provider: 'anthropic',
    lifecycle: 'active',
    estimate_kind: estimateKind,
    // Sent explicitly so house / commercial estimations drive the right
    // cohort instead of silently falling back to byt / pronajem.
    category_main: categoryMain,
    category_type: categoryTypeFor(estimateKind),
    listing_id: listingId,
    special_instructions: specialInstructions.trim() || null,
    contextual_text: contextualText.trim() || null,
  };
}

/* The API's own sentence (an ad with no location is refused: "needs a
 * location"), else what failed. */
function ErrorBlock({ error }: { error: ApiError }) {
  const headline =
    error.status === 0
      ? 'Network error — check your connection or the API URL.'
      : error.message || `Request failed (HTTP ${error.status}).`;
  return (
    <div className="mt-3 px-3 py-2 rounded-[var(--radius-sm)] border border-[var(--color-brick)]/30 bg-[var(--color-brick-soft)] text-[var(--color-brick)] text-sm">
      <p className="font-medium">{headline}</p>
    </div>
  );
}
