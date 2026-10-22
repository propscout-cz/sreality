/* "Moje" — what the operator has already decided about this property, one line
 * under the header on every tab: its tags, the latest note and the artifact link.
 * A summary only; the Moje tab holds the editors (CurationBlock). It reads the
 * same queries those editors do, as the identity the page acts as (render it
 * inside ActingAs), and a failed read says so with a retry (MS16). */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listPropertyNotes } from '@/lib/api';
import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { artifactLinkLabel, safeHttpUrl } from '@/lib/artifactLink';
import { fmtShortDate } from '@/lib/format';
import { curationKeys } from '@/lib/queries';
import { useArtifactLinks } from '@/lib/useArtifactLinks';
import { useCardTags } from '@/lib/useCardTags';
import TagChip from '@/components/curation/TagChip';
import { readFailed } from '@/components/ReadFailedMark';

export default function MineStrip({
  property_id,
  onOpen,
}: {
  property_id: number;
  /* Opens the Moje tab. */
  onOpen: () => void;
}) {
  const actAs = useActAs();
  const { membersQ, mine } = useCardTags(property_id);
  const notesQ = useQuery({
    queryKey: actingKey(actAs, curationKeys.propertyNotes(property_id)),
    queryFn: () => listPropertyNotes(property_id, ...actOpts(actAs)),
    staleTime: 30_000,
  });
  const linkQ = useArtifactLinks();

  const notes = useMemo(() => notesQ.data?.data ?? [], [notesQ.data]);
  const latest = useMemo(
    () => notes.reduce<(typeof notes)[number] | null>((a, n) => (a == null || n.created_at > a.created_at ? n : a), null),
    [notes],
  );
  const link = linkQ.data?.get(property_id) ?? null;
  const href = link ? safeHttpUrl(link.url) : null;
  const failed = readFailed(membersQ) || readFailed(notesQ) || readFailed(linkQ);
  const loading = membersQ.isLoading || notesQ.isLoading;

  return (
    <section
      aria-label="Moje"
      className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-2.5"
    >
      <span className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--color-ink-3)]">Moje</span>
      {failed ? (
        <p className="flex-1 text-sm text-[var(--color-brick)]">
          Štítky a poznámky se nepodařilo načíst
          <button
            type="button"
            onClick={() => {
              if (readFailed(membersQ)) void membersQ.refetch();
              if (readFailed(notesQ)) void notesQ.refetch();
              if (readFailed(linkQ)) void linkQ.refetch();
            }}
            className="ml-3 font-medium underline underline-offset-2 hover:no-underline"
          >
            Zkusit znovu
          </button>
        </p>
      ) : loading ? (
        <p className="flex-1 text-sm text-[var(--color-ink-3)]">Načítám…</p>
      ) : (
        <>
          {mine.length > 0 && (
            <span className="flex flex-wrap gap-1.5">
              {mine.map((t) => (
                <TagChip key={t.id} tag={t} />
              ))}
            </span>
          )}
          <p className="min-w-0 flex-[1_1_16rem] truncate text-sm text-[var(--color-ink-2)]">
            {latest ? (
              <>
                <span className="mr-2 font-mono text-[0.75rem] tabular-nums text-[var(--color-ink-3)]">
                  {fmtShortDate(latest.created_at)}
                </span>
                {latest.body}
              </>
            ) : (
              <span className="text-[var(--color-ink-3)]">Zatím žádná poznámka</span>
            )}
          </p>
          {link &&
            (href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="max-w-[16rem] truncate text-[0.8rem] text-[var(--color-copper)] hover:underline underline-offset-2"
              >
                {artifactLinkLabel(href)} ↗
              </a>
            ) : (
              <span className="max-w-[16rem] truncate text-[0.8rem] text-[var(--color-ink-2)]">{link.url}</span>
            ))}
        </>
      )}
      <button
        type="button"
        onClick={onOpen}
        className="text-[0.8rem] font-medium text-[var(--color-copper)] hover:underline underline-offset-2"
      >
        {notes.length > 0 ? `Poznámky (${notes.length}) →` : 'Štítky a poznámky →'}
      </button>
    </section>
  );
}
