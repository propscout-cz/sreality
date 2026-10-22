/* The property's "Artifact link" (migration 599): ONE operator-typed URL to a page kept about the
 * property, shown beside the notes. Property-grain like the rest of the curation block (rule 18),
 * so it follows a merge. A failed read is said out loud, never drawn as an empty field. */

import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, deletePropertyArtifactLink, setPropertyArtifactLink } from '@/lib/api';
import { artifactLinkLabel, safeHttpUrl } from '@/lib/artifactLink';
import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { curationKeys } from '@/lib/queries';
import { useArtifactLinks } from '@/lib/useArtifactLinks';
import type { ArtifactLink } from '@/lib/types';
import { fmtAbsolute } from '@/lib/format';
import ErrorBanner from '@/components/ErrorBanner';
import { readFailed } from '@/components/ReadFailedMark';
import { PencilIcon, TrashIcon } from '@/components/icons';
import { SectionLabel } from '@/components/section';

const URL_MAX = 2000;
const NOT_A_URL = 'Enter a full web address starting with http:// or https://';

export default function ArtifactLinkRow({
  property_id,
  label = 'Artifact link',
}: {
  property_id: number;
  label?: string;
}) {
  const qc = useQueryClient();
  const labelId = useId();
  const actAs = useActAs();
  const key = actingKey(actAs, curationKeys.artifactLinks);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* The one whole-set read every surface shares; this row patches it in place. */
  const linkQ = useArtifactLinks();
  const link = linkQ.data?.get(property_id) ?? null;
  const failed = readFailed(linkQ);
  const patch = (next: ArtifactLink | null) =>
    qc.setQueryData<Map<number, ArtifactLink>>(key, (prev) => {
      const map = new Map(prev);
      if (next) map.set(property_id, next);
      else map.delete(property_id);
      return map;
    });

  const save = useMutation({
    mutationFn: (url: string) => setPropertyArtifactLink(property_id, url, ...actOpts(actAs)),
    onSuccess: (saved) => {
      patch(saved);
      setEditing(false);
      setError(null);
    },
    onError: (err: ApiError | Error) => setError(err.message || 'Failed to save the link'),
  });

  const remove = useMutation({
    mutationFn: () => deletePropertyArtifactLink(property_id, ...actOpts(actAs)),
    onSuccess: () => {
      patch(null);
      setConfirmingRemove(false);
      setDraft('');
      setError(null);
    },
    onError: (err: ApiError | Error) => {
      setError(err.message || 'Failed to remove the link');
      setConfirmingRemove(false);
    },
  });

  const trimmed = draft.trim();
  const showForm = !failed && !linkQ.isLoading && (editing || link === null);
  const submit = () => {
    if (!safeHttpUrl(trimmed)) {
      setError(NOT_A_URL);
      return;
    }
    if (link && trimmed === link.url) {
      setEditing(false);
      setError(null);
      return;
    }
    setError(null);
    save.mutate(trimmed);
  };

  return (
    <section>
      <SectionLabel>
        <span id={labelId}>{label}</span>
      </SectionLabel>

      {failed ? (
        <ErrorBanner
          title="Odkaz na artefakt"
          message="se nepodařilo načíst."
          onRetry={() => void linkQ.refetch()}
        />
      ) : linkQ.isLoading ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">Loading…</p>
      ) : showForm ? (
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="mt-3"
        >
          <input
            type="url"
            inputMode="url"
            aria-labelledby={labelId}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="https://…"
            maxLength={URL_MAX}
            autoFocus={editing}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
          <div className="mt-2 flex items-center justify-end gap-1.5">
            {editing && (
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setError(null);
                }}
                disabled={save.isPending}
                className="px-2 py-1 text-[0.75rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] disabled:opacity-50"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={trimmed.length === 0 || save.isPending}
              className="px-3 py-1 text-[0.78rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {save.isPending ? 'Saving…' : 'Save link'}
            </button>
          </div>
        </form>
      ) : link ? (
        <div className="mt-3">
          <div className="flex items-start justify-between gap-3">
            <ArtifactAnchor url={link.url} />
            <div className="flex items-center gap-0.5 shrink-0">
              <button
                type="button"
                onClick={() => {
                  setDraft(link.url);
                  setError(null);
                  setEditing(true);
                }}
                aria-label="Edit artifact link"
                title="Edit artifact link"
                className="inline-flex items-center justify-center w-6 h-6 rounded-[var(--radius-xs)] text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] transition-colors"
              >
                <PencilIcon className="h-[11px] w-[11px]" />
              </button>
              <button
                type="button"
                onClick={() => setConfirmingRemove(true)}
                aria-label="Remove artifact link"
                title="Remove artifact link"
                className="inline-flex items-center justify-center w-6 h-6 rounded-[var(--radius-xs)] text-[var(--color-ink-4)] hover:text-[var(--color-brick)] hover:bg-[var(--color-paper-2)] transition-colors"
              >
                <TrashIcon className="h-[11px] w-[11px]" />
              </button>
            </div>
          </div>
          {confirmingRemove ? (
            <div className="mt-1.5 flex items-center gap-2">
              <span className="text-[0.7rem] text-[var(--color-brick)]">Remove this link?</span>
              <button
                type="button"
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
                className="px-2 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 disabled:opacity-50 transition-colors"
              >
                {remove.isPending ? 'Removing…' : 'Remove'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingRemove(false)}
                disabled={remove.isPending}
                className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <p
              className="mt-1 text-[0.7rem] tracking-wide text-[var(--color-ink-4)] cursor-help"
              title={`Saved ${fmtAbsolute(link.created_at)}`}
            >
              {link.updated_at !== link.created_at
                ? `Updated ${fmtAbsolute(link.updated_at)}`
                : `Saved ${fmtAbsolute(link.created_at)}`}
            </p>
          )}
        </div>
      ) : null}

      {error && <p className="mt-1.5 text-[0.7rem] text-[var(--color-brick)]">{error}</p>}
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
