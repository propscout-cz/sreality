import { useEffect, useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Dialog, { DialogClose } from '@/components/Dialog';
import ErrorBanner from '@/components/ErrorBanner';
import { Field } from '@/components/controls';
import { setAgentRunLimit } from '@/lib/api';
import { agentRunKeys, useAgentRunOptions } from '@/lib/agentRunOptions';
import { fmtUsd } from '@/lib/format';

/* This account's daily agent spending limit, opened from the avatar's menu. Loaded on demand: the first visit carries
 * the menu item, never this dialog. The API judges the number; the check here only saves a round trip. */

const AMOUNT = /^\d+([.,]\d{1,2})?$/;

export default function AgentLimitDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const queryClient = useQueryClient();
  const options = useAgentRunOptions();
  const { refetch } = options;
  /* The menu's read can be minutes old, and this is the one place that shows a figure to act on. */
  useEffect(() => {
    void refetch();
  }, [refetch]);

  const limit = options.data?.limit ?? null;
  const [typed, setTyped] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const text = typed ?? (limit?.limit_usd != null ? String(limit.limit_usd) : '');

  const save = useMutation({
    mutationFn: setAgentRunLimit,
    onSuccess: () => {
      setTyped(null);
      void queryClient.invalidateQueries({ queryKey: agentRunKeys.options });
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    const valid = AMOUNT.test(trimmed);
    setInvalid(!valid);
    if (valid) save.mutate(Number(trimmed.replace(',', '.')));
  };

  let body;
  if (options.isPending) {
    body = <p className="text-sm text-[var(--color-ink-3)]">Loading your limit…</p>;
  } else if (!options.data) {
    body = (
      <ErrorBanner flush title="Could not load your limit:" message={options.error?.message ?? ''} retryLabel="Try again"
        onRetry={() => void refetch()} />
    );
  } else if (!limit) {
    body = <p className="text-sm text-[var(--color-ink-2)]">{options.data.start.reason}</p>;
  } else {
    body = (
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-[var(--color-ink-2)]">
          Spent today: <span className="font-mono tabular-nums">{fmtUsd(limit.spent_today_usd)}</span> of{' '}
          <span className="font-mono tabular-nums">{fmtUsd(limit.limit_usd)}</span>
        </p>
        <Field label="Limit, USD per day" as="control">
          <input
            inputMode="decimal"
            value={text}
            aria-invalid={invalid}
            onChange={(e) => { setTyped(e.target.value); setInvalid(false); save.reset(); }}
            className="w-32 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2 py-1.5 font-mono text-sm tabular-nums text-[var(--color-ink)] aria-[invalid=true]:border-[var(--color-brick)]"
          />
        </Field>
        {invalid && <p className="text-[0.78rem] text-[var(--color-brick)]">Enter an amount of 0 or more, with at most two decimals.</p>}
        <div className="space-y-1.5 text-[0.78rem] text-[var(--color-ink-2)]">
          <p>Once this account&apos;s spend today reaches the limit, no new run starts and queued runs wait. Nothing fails. The day resets at midnight UTC. 0 means no new run starts.</p>
          <p>A run already going finishes under its own cost cap, and two can run at once, so a day&apos;s spend can pass your limit by up to two runs&apos; caps.</p>
          <p>A daily ceiling for the whole platform also applies, so runs can wait before you reach your limit, however high you set it.</p>
        </div>
        {save.isError && <ErrorBanner flush title="Not saved:" message={save.error.message} />}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending}
            className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 py-1.5 text-sm text-[var(--color-on-copper)] transition-colors hover:bg-[var(--color-copper-2)] disabled:opacity-50">
            Save limit
          </button>
          {save.isPending && <span className="text-sm text-[var(--color-ink-3)]">Saving…</span>}
          {save.isSuccess && <span role="status" className="text-sm text-[var(--color-ink-2)]">Saved.</span>}
        </div>
      </form>
    );
  }

  return (
    <Dialog open onClose={onClose} labelledBy={titleId} className="w-full max-w-md p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 id={titleId} className="text-lg leading-tight">Agent spending limit</h2>
        <DialogClose onClick={onClose} />
      </div>
      {body}
    </Dialog>
  );
}
