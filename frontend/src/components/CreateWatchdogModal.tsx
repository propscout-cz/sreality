/* "Create watchdog from Browse" — a small name-prompt dialog.
 *
 * Browse hands this modal its current filters. The modal builds the
 * WatchdogFilterSpec (filtersToWatchdogSpec), suggests a name
 * (watchdogNameSuggestion), and on confirm POSTs /notifications/subscriptions;
 * on success the watchdog shows up in the Watchdog feed / Manage list like any
 * other.
 *
 * The map area is the one choice made here (rule 16: what Browse shows is what
 * a watchdog watches). The box Browse is filtering on is watched too unless
 * the operator unticks it — the question the preset dialog asks, because a
 * watchdog pinned to a stale viewport fires forever on the wrong place. A
 * centre+radius pick is a sidebar filter, not a viewport, so it always rides
 * along and the checkbox stays off screen. Any Browse filters the matcher
 * can't honour are surfaced as a heads-up so the operator isn't surprised.
 *
 * Self-contained (no context provider): Browse owns the open/close state. */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import { useMutation } from '@tanstack/react-query';
import { ApiError, createWatchdogSubscription } from '@/lib/api';
import {
  filtersToWatchdogSpec,
  watchdogNameSuggestion,
  type ListingFilters,
} from '@/lib/filters';
import Dialog from '@/components/Dialog';

export const MAP_AREA_LABEL = 'Include the current map area in this watchdog';

export interface CreateWatchdogModalProps {
  filters: ListingFilters;
  onClose: () => void;
}

export default function CreateWatchdogModal({ filters, onClose }: CreateWatchdogModalProps) {
  const navigate = useNavigate();
  const [name, setName] = useState(() => watchdogNameSuggestion(filters));
  const [withMapArea, setWithMapArea] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  /* The viewport box is offered; a centre+radius square is not a viewport. */
  const offersMapArea = filters.locationMode === 'viewport' && filters.bounds != null;
  const { spec, unsupported } = useMemo(
    () => filtersToWatchdogSpec(filters, { includeMapArea: offersMapArea && withMapArea }),
    [filters, offersMapArea, withMapArea],
  );

  /* Focus is the primitive's (`initialFocus` below). What this adds is the
   * SELECTION, so the suggested name can be typed straight over. Mount-only:
   * a dependency here is the focus-theft bug lib/useDialog describes. */
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const createMut = useMutation({
    mutationFn: (watchdogName: string) =>
      createWatchdogSubscription({
        name: watchdogName,
        filter_spec: spec,
        is_active: true,
      }),
    onSuccess: () => {
      navigate(ROUTES.watchdogManage.build());
    },
  });

  const trimmed = name.trim();
  const submit = () => {
    if (trimmed.length === 0 || createMut.isPending) return;
    createMut.mutate(trimmed);
  };

  const errMsg =
    createMut.error instanceof ApiError
      ? createMut.error.message
      : createMut.error
        ? 'Something went wrong.'
        : null;

  /* Escape, the focus trap, initial + restored focus, the backdrop click and
   * the body scroll lock all come from <Dialog> (lib/useDialog.ts). The name
   * is the VISIBLE heading, so what a screen reader announces is what the
   * operator reads. */
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} initialFocus={inputRef} className="w-full max-w-md p-5">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        Watchdog
      </p>
      <h2
        id={titleId}
        className="mt-1 text-xl leading-tight"
        style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
      >
        Save these filters as a watchdog
      </h2>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        You'll get a notification the moment a freshly scraped listing matches
        this filter set.
      </p>

      <label className="mt-4 block">
        <span className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">
          Watchdog name
        </span>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
          }}
          placeholder="e.g. 2+kk Praha pod 6M"
          className="mt-1 w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink)] focus-visible:border-[var(--color-copper)]"
        />
      </label>

      {offersMapArea ? (
        <button
          type="button"
          onClick={() => setWithMapArea((v) => !v)}
          aria-pressed={withMapArea}
          className="mt-3 flex w-full items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-left transition-colors hover:border-[var(--color-rule-strong)]"
        >
          <span
            className={[
              'flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border text-[0.6rem]',
              withMapArea
                ? 'border-[var(--color-copper)] bg-[var(--color-copper)] text-white'
                : 'border-[var(--color-rule-strong)] text-transparent',
            ].join(' ')}
            aria-hidden
          >
            ✓
          </span>
          <span className="text-[0.8rem] text-[var(--color-ink-2)]">{MAP_AREA_LABEL}</span>
        </button>
      ) : null}

      {unsupported.length > 0 ? (
        <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2">
          <p className="text-[0.7rem] text-[var(--color-ink-2)]">
            These active filters can't be watched and will be left off:{' '}
            <span className="text-[var(--color-ink)]">
              {unsupported.join(', ')}
            </span>
            . A watchdog fires on brand-new listings, so date / status filters
            don't apply.
          </p>
        </div>
      ) : null}

      {errMsg ? (
        <p className="mt-3 text-[0.8rem] text-[var(--color-brick)]">{errMsg}</p>
      ) : null}

      <div className="mt-5 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] hover:border-[var(--color-rule-strong)] transition-colors"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={trimmed.length === 0 || createMut.isPending}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors disabled:opacity-50"
        >
          {createMut.isPending ? 'Creating…' : 'Create watchdog'}
        </button>
      </div>
    </Dialog>
  );
}
