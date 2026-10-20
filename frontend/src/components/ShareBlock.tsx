/* THE sharing block (migrations 610 + 641): share by email, the recipients,
 * and a two-step "Stop sharing" per recipient. One component for the
 * collection page (read-only shares, English) and the Pipeline tab (read or
 * edit shares, Czech) — the collection page's SharingBlock, generalised.
 *
 * The block owns its form state and its two writes' pending / error state; the
 * page owns what the writes ARE (`onShare` / `onRevoke`, each a promise that
 * also revalidates whatever the page reads). A refusal (no such account, your
 * own address — the API's 422) is a sentence about the field, so it sits under
 * the field. The mode choice exists only where a share has modes (`modes`). */

import { useState, type ReactNode } from 'react';
import { useMutation } from '@tanstack/react-query';

import { Field, Segmented } from '@/components/controls';
import { SectionLabel } from '@/components/section';
import { TwoStep } from '@/components/TwoStep';
import type { PipelineShareMode } from '@/lib/types';

interface ShareRecipient {
  account_id: string;
  email: string | null;
  mode?: PipelineShareMode;
}

const WORDS = {
  en: {
    email: 'Email',
    share: 'Share',
    sharing: 'Sharing…',
    failed: 'Failed to share',
    nobody: 'Not shared with anyone.',
    list: 'Shared with',
    noEmail: 'an account without an email',
    stop: 'Stop sharing',
    stopWith: (who: string) => `Stop sharing with ${who}`,
    stopping: 'Stopping…',
    cancel: 'Cancel',
    mode: 'Access',
    modes: { read: 'View only', edit: 'Can edit' },
  },
  cs: {
    email: 'E-mail',
    share: 'Sdílet',
    sharing: 'Sdílím…',
    failed: 'Sdílení se nepodařilo',
    nobody: 'Zatím s nikým nesdíleno.',
    list: 'Sdíleno s',
    noEmail: 'účet bez e-mailu',
    stop: 'Přestat sdílet',
    stopWith: (who: string) => `Přestat sdílet s ${who}`,
    stopping: 'Ukončuji…',
    cancel: 'Zrušit',
    mode: 'Přístup',
    modes: { read: 'Jen ke čtení', edit: 'Úpravy' },
  },
} as const;

type ShareBlockLang = keyof typeof WORDS;

export default function ShareBlock({
  title,
  description,
  recipients,
  onShare,
  onRevoke,
  modes = false,
  lang = 'en',
}: {
  title: string;
  description: ReactNode;
  recipients: readonly ShareRecipient[];
  /* `mode` is passed only when `modes` is on. */
  onShare: (email: string, mode?: PipelineShareMode) => Promise<unknown>;
  onRevoke: (account_id: string) => Promise<unknown>;
  modes?: boolean;
  lang?: ShareBlockLang;
}) {
  const w = WORDS[lang];
  const [email, setEmail] = useState('');
  const [mode, setMode] = useState<PipelineShareMode>('read');
  const [error, setError] = useState<string | null>(null);

  const share = useMutation({
    mutationFn: (address: string) => (modes ? onShare(address, mode) : onShare(address)),
    onSuccess: () => {
      setEmail('');
      setError(null);
    },
    onError: (err: Error) => setError(err.message || w.failed),
  });

  const trimmed = email.trim();

  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <p className="mt-2 text-[0.78rem] text-[var(--color-ink-4)] max-w-2xl">{description}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed === '' || share.isPending) return;
          share.mutate(trimmed);
        }}
        className="mt-3 flex flex-wrap items-end gap-3 max-w-2xl"
      >
        <Field label={w.email} as="control" className="flex-1 min-w-[14rem]">
          <input
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setError(null);
            }}
            placeholder="name@example.com"
            maxLength={320}
            autoComplete="off"
            aria-invalid={error != null || undefined}
            className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
        </Field>
        {modes && (
          <Field label={w.mode}>
            <Segmented
              options={[
                { value: 'read' as const, label: w.modes.read },
                { value: 'edit' as const, label: w.modes.edit },
              ]}
              value={mode}
              onChange={setMode}
            />
          </Field>
        )}
        <button
          type="submit"
          disabled={trimmed === '' || share.isPending}
          className="shrink-0 px-4 py-1.5 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper-2)] text-[var(--color-ink)] hover:border-[var(--color-ink-3)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {share.isPending ? w.sharing : w.share}
        </button>
      </form>
      {error && (
        <p role="alert" className="mt-2 text-[0.75rem] text-[var(--color-brick)]">
          {error}
        </p>
      )}
      {recipients.length === 0 ? (
        <p className="mt-3 text-[0.78rem] text-[var(--color-ink-3)]">{w.nobody}</p>
      ) : (
        <ul
          aria-label={w.list}
          className="mt-4 max-w-2xl divide-y divide-[var(--color-rule-soft)] border-y border-[var(--color-rule-soft)]"
        >
          {recipients.map((r) => (
            <RecipientRow key={r.account_id} recipient={r} lang={lang} onRevoke={onRevoke} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RecipientRow({
  recipient,
  lang,
  onRevoke,
}: {
  recipient: ShareRecipient;
  lang: ShareBlockLang;
  onRevoke: (account_id: string) => Promise<unknown>;
}) {
  const w = WORDS[lang];
  const [confirming, setConfirming] = useState(false);
  /* Its failure shows on the row; an onError keeps the global toast off it. */
  const revoke = useMutation({ mutationFn: () => onRevoke(recipient.account_id), onError: () => {} });
  const who = recipient.email ?? w.noEmail;
  return (
    <li className="py-2.5 flex items-center gap-3">
      <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-ink-2)]">{who}</span>
      {recipient.mode && (
        <span className="shrink-0 text-[0.72rem] tracking-wide text-[var(--color-ink-3)]">
          {w.modes[recipient.mode]}
        </span>
      )}
      {revoke.error && (
        <span className="text-[0.72rem] text-[var(--color-brick)]">{revoke.error.message}</span>
      )}
      <TwoStep
        idle={w.stop}
        label={w.stopWith(who)}
        confirm={revoke.isPending ? w.stopping : w.stop}
        cancel={w.cancel}
        confirming={confirming}
        setConfirming={setConfirming}
        pending={revoke.isPending}
        onConfirm={() => revoke.mutate()}
      />
    </li>
  );
}
