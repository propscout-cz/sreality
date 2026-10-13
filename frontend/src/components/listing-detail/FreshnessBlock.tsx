/* On-demand freshness checks for an advert (`listing_freshness_checks`, append-only
 * observability keyed on sreality_id) and the "Ověřit aktuálnost" re-fetch. Shared
 * by the desktop property page and the phone layout (#1767). */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { SectionLabel } from '@/components/section';
import {
  ApiError,
  verifyListingFreshness,
  type FreshnessOutcome,
  type VerifyFreshnessResult,
} from '@/lib/api';
import { fmtAbsolute, fmtRelative } from '@/lib/format';
import type { ListingFreshnessCheckPublic } from '@/lib/types';

export default function FreshnessBlock({
  sreality_id,
  checks,
  label = 'Freshness checks',
}: {
  sreality_id: number;
  checks: ListingFreshnessCheckPublic[];
  label?: string;
}) {
  const qc = useQueryClient();
  const count = checks.length;

  const verify = useMutation<VerifyFreshnessResult, Error>({
    mutationFn: () => verifyListingFreshness(sreality_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['freshness', sreality_id] });
      // Bare prefixes: the page's keys are the property id and the advert's
      // surrogate id, neither of which this component knows.
      qc.invalidateQueries({ queryKey: ['property'] });
      qc.invalidateQueries({ queryKey: ['snapshots'] });
    },
  });

  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <SectionLabel>
          <span>{label}</span>
          <span className="ml-2 font-mono tabular-nums text-[var(--color-ink-4)] tracking-normal">
            ({count})
          </span>
        </SectionLabel>
        <button
          type="button"
          onClick={() => verify.mutate()}
          disabled={verify.isPending}
          className="px-3 py-1 text-[0.78rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {verify.isPending ? 'Ověřuji…' : 'Ověřit aktuálnost'}
        </button>
      </div>

      <VerifyResult mutation={verify} />

      {count === 0 ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">
          No on-demand freshness checks recorded.
        </p>
      ) : (
        <div className="mt-3 border border-[var(--color-rule)] rounded-[var(--radius-md)] overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)] bg-[var(--color-paper-2)]">
                <th className="px-3 py-2 font-medium">Checked</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {[...checks]
                .sort((a, b) => new Date(b.checked_at).getTime() - new Date(a.checked_at).getTime())
                .map((c) => (
                  <tr key={c.id} className="border-t border-[var(--color-rule-soft)]">
                    <td className="px-3 py-2 text-[var(--color-ink-2)] cursor-help" title={fmtAbsolute(c.checked_at)}>
                      {fmtRelative(c.checked_at)}
                    </td>
                    <td className="px-3 py-2">
                      <OutcomeChip outcome={c.outcome} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function VerifyResult({
  mutation,
}: {
  mutation: ReturnType<
    typeof useMutation<VerifyFreshnessResult, Error>
  >;
}) {
  if (mutation.isPending) {
    return (
      <p className="mt-3 text-sm text-[var(--color-ink-3)]">
        Re-fetching the listing from the source…
      </p>
    );
  }
  if (mutation.isError) {
    const err = mutation.error;
    const msg = err instanceof ApiError ? err.message : err.message;
    return (
      <p className="mt-3 text-sm text-[var(--color-brick)]">
        Verification failed: {msg}
      </p>
    );
  }
  if (mutation.isSuccess) {
    const { outcome, what_changed } = mutation.data.data;
    return (
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-[var(--color-ink-2)]">
        <OutcomeChip outcome={outcome} />
        <span>{freshnessOutcomeMessage(outcome, what_changed)}</span>
      </div>
    );
  }
  return null;
}

function freshnessOutcomeMessage(
  outcome: FreshnessOutcome,
  whatChanged: string[],
): string {
  switch (outcome) {
    case 'unchanged':
      return 'Still listed — nothing changed since the last snapshot.';
    case 'updated':
      return whatChanged.length > 0
        ? `Still listed — updated: ${whatChanged.join(', ')}.`
        : 'Still listed — the listing was updated; a new snapshot was recorded.';
    case 'gone':
      return 'No longer listed — marked inactive.';
    case 'cached':
      return 'Recently verified — still considered fresh, no re-fetch needed.';
    case 'fetch_error':
      return 'Could not reach the source listing; nothing was changed.';
    default:
      return '';
  }
}

function OutcomeChip({ outcome }: { outcome: string }) {
  const lower = outcome.toLowerCase();
  let bg = 'var(--color-rule-soft)';
  let fg = 'var(--color-ink-2)';
  if (lower === 'unchanged') {
    bg = 'var(--color-sage-soft)';
    fg = 'var(--color-sage)';
  } else if (lower === 'updated' || lower === 'changed') {
    bg = 'var(--color-copper-soft)';
    fg = 'var(--color-copper)';
  } else if (
    lower === 'gone' ||
    lower === 'inactive' ||
    lower === 'error' ||
    lower === 'fetch_error'
  ) {
    bg = 'var(--color-brick-soft)';
    fg = 'var(--color-brick)';
  }
  return (
    <span
      className="inline-block px-2 py-0.5 text-[0.65rem] tracking-[0.14em] uppercase rounded-[var(--radius-xs)]"
      style={{ background: bg, color: fg }}
    >
      {outcome}
    </span>
  );
}
