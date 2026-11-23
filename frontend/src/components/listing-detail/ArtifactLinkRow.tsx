/* The property's "Artifact link" (migration 599): ONE operator-typed URL to a page kept about the
 * property, shown beside the notes. Property-grain like the rest of the curation block (rule 18),
 * so it follows a merge. A failed read is said out loud, never drawn as an empty field. With no
 * link it is one "+ Odkaz na artefakt" button that opens the field; `bare` drops the heading and
 * draws it on one line (the desktop notes card's title bar). Otherwise it is its own section card
 * (the phone, system.md § Section cards). */

import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, deletePropertyArtifactLink, setPropertyArtifactLink } from '@/lib/api';
import { artifactLinkLabel, safeHttpUrl } from '@/lib/artifactLink';
import { actOpts, useActAs } from '@/lib/acting';
import { revalidateTables } from '@/lib/curationSync';
import { trimDraft, useServerDraft } from '@/lib/useServerDraft';
import { useArtifactLinks } from '@/lib/useArtifactLinks';
import { fmtAbsolute } from '@/lib/format';
import ErrorBanner from '@/components/ErrorBanner';
import { readFailed } from '@/components/ReadFailedMark';
import { PencilIcon, TrashIcon } from '@/components/icons';
import Panel from '@/components/Panel';

const URL_MAX = 2000;
const NOT_A_URL = 'Zadejte celou adresu začínající http:// nebo https://';
const LABEL = 'Odkaz na artefakt';

export default function ArtifactLinkRow({
  property_id,
  label = LABEL,
  bare = false,
}: {
  property_id: number;
  label?: string;
  bare?: boolean;
}) {
  const qc = useQueryClient();
  const labelId = useId();
  const actAs = useActAs();
  const [editing, setEditing] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* The one whole-set read every surface shares; a write re-reads it (lib/curationSync),
   * and the row closes once the read has landed, so it never shows the old link. */
  const linkQ = useArtifactLinks();
  const link = linkQ.data?.get(property_id) ?? null;
  /* The field follows the stored link while untouched, so a link set elsewhere shows
   * in an open field and is never saved back over (D3, lib/useServerDraft). */
  const url = useServerDraft(link?.url ?? '', trimDraft);
  const open = () => {
    url.reset();
    setError(null);
    setEditing(true);
  };
  const close = () => {
    setEditing(false);
    url.reset();
    setError(null);
  };
  const failed = readFailed(linkQ);
  const reread = () => revalidateTables(qc, ['property_artifact_links'], actAs);

  const save = useMutation({
    mutationFn: (url: string) => setPropertyArtifactLink(property_id, url, ...actOpts(actAs)),
    onSuccess: async () => {
      await reread();
      close();
    },
    onError: (err: ApiError | Error) => setError(err.message || 'Odkaz se nepodařilo uložit'),
  });

  const remove = useMutation({
    mutationFn: () => deletePropertyArtifactLink(property_id, ...actOpts(actAs)),
    onSuccess: async () => {
      await reread();
      setConfirmingRemove(false);
      setError(null);
    },
    onError: (err: ApiError | Error) => {
      setError(err.message || 'Odkaz se nepodařilo odebrat');
      setConfirmingRemove(false);
    },
  });

  const showForm = !failed && !linkQ.isLoading && editing;
  const empty = url.normalized.length === 0;
  /* An untouched field closes, whatever the link is now; a stored link cleared
   * from the field saves nothing (its button is off, and the form submits nothing
   * either: the trash button removes a link); what the user typed must be a whole URL. */
  const submit = () => {
    const next = url.edited();
    if (next == null) {
      close();
      return;
    }
    if (empty) return;
    if (!safeHttpUrl(next)) {
      setError(NOT_A_URL);
      return;
    }
    setError(null);
    save.mutate(next);
  };

  const iconBtn =
    'inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-ink-3)] hover:bg-[var(--color-paper-3)] transition-colors';
  const quiet = 'px-2.5 py-1.5 text-[0.8rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] disabled:opacity-50';
  const primary =
    'px-3 py-1.5 text-[0.8rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

  let body: React.ReactNode = null;
  if (failed) {
    body = <ErrorBanner title={label} message="se nepodařilo načíst." onRetry={() => void linkQ.refetch()} />;
  } else if (linkQ.isLoading) {
    body = <p className="text-sm text-[var(--color-ink-3)]">Načítám…</p>;
  } else if (showForm) {
    body = (
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex w-full flex-wrap items-center gap-2"
      >
        <input
          type="url"
          inputMode="url"
          aria-labelledby={bare ? undefined : labelId}
          aria-label={bare ? label : undefined}
          {...url.bind}
          placeholder="https://…"
          maxLength={URL_MAX}
          autoFocus
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className="min-w-[14rem] flex-1 px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-paper-3)] border border-[var(--color-rule-strong)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-3)] focus:border-[var(--color-copper)]"
        />
        <button
          type="button"
          onClick={close}
          disabled={save.isPending}
          className={quiet}
        >
          Zrušit
        </button>
        <button type="submit" disabled={empty || save.isPending} className={primary}>
          {save.isPending ? 'Ukládám…' : 'Uložit odkaz'}
        </button>
      </form>
    );
  } else if (link) {
    body = (
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1">
          <span
            className="min-w-0 truncate"
            title={
              link.updated_at !== link.created_at
                ? `Upraveno ${fmtAbsolute(link.updated_at)}`
                : `Uloženo ${fmtAbsolute(link.created_at)}`
            }
          >
            <ArtifactAnchor url={link.url} />
          </span>
          <button
            type="button"
            onClick={open}
            aria-label="Upravit odkaz na artefakt"
            title="Upravit odkaz"
            className={`${iconBtn} hover:text-[var(--color-ink)]`}
          >
            <PencilIcon className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setConfirmingRemove(true)}
            aria-label="Odebrat odkaz na artefakt"
            title="Odebrat odkaz"
            className={`${iconBtn} hover:text-[var(--color-brick)]`}
          >
            <TrashIcon className="h-3.5 w-3.5" />
          </button>
        </div>
        {confirmingRemove && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-[0.8rem] text-[var(--color-brick)]">Odebrat odkaz?</span>
            <button
              type="button"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 disabled:opacity-50 transition-colors"
            >
              {remove.isPending ? 'Odebírám…' : 'Odebrat'}
            </button>
            <button type="button" onClick={() => setConfirmingRemove(false)} disabled={remove.isPending} className={quiet}>
              Zrušit
            </button>
          </div>
        )}
      </div>
    );
  } else {
    body = (
      <button
        type="button"
        onClick={open}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] px-3 py-1.5 text-[0.8rem] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
      >
        <span aria-hidden>+</span> {label}
      </button>
    );
  }

  if (!bare) {
    return (
      <Panel title={<span id={labelId}>{label}</span>}>
        <div>{body}</div>
        {error && <p className="text-[0.75rem] text-[var(--color-brick)]">{error}</p>}
      </Panel>
    );
  }
  return (
    <section aria-label={label} className="min-w-0">
      <div>{body}</div>
      {error && <p className="mt-1.5 text-[0.75rem] text-[var(--color-brick)]">{error}</p>}
    </section>
  );
}

/* A stored value that is not http(s) cannot occur (CHECK, migration 599), but is still never made a
 * clickable href: it is shown as text. */
export function ArtifactAnchor({ url }: { url: string }) {
  const href = safeHttpUrl(url);
  if (!href) {
    return <p className="text-sm text-[var(--color-ink)] break-all">{url}</p>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={href}
      className="text-sm text-[var(--color-copper)] hover:text-[var(--color-copper-2)] underline underline-offset-2 break-all min-w-0"
    >
      {artifactLinkLabel(href)}
    </a>
  );
}
