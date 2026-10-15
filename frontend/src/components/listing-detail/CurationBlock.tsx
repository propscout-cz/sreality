/* Collections + tags + artifact link + notes for a single PROPERTY. Wired into PropertyDetail.
 *
 * Curation is property-grain (migration 202): a tag / collection membership /
 * note describes the real-world property, so this block operates on the
 * listing's `property_id`. The viewed listing is recorded as a note's
 * `origin_listing_id` + `origin_listing_ref_id` (display provenance —
 * "written while viewing this advert"); both are sent since the legacy id
 * is NULL for a post-Gate-2 listing (migration 323).
 *
 * Reads come from two places by design:
 *   - The "which collections / tags exist" indices use the bearer-gated
 *     FastAPI service so listing_count + ordering live in one place.
 *   - The "does THIS property belong to X" reverse-index reads pull from the
 *     *_public Supabase views via the anon key. Writes always go through the API.
 * A failed read is said out loud with a retry and never drawn as an empty list
 * or an unticked toggle (MS16).
 */

import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  ApiError,
  addPropertiesToCollection,
  createPropertyNote,
  deletePropertyNote,
  listCollections,
  listPropertyNotes,
  removePropertyFromCollection,
  updatePropertyNote,
  uploadNoteAttachment,
} from '@/lib/api';
import { revalidateCollections } from '@/lib/collectionCache';
import {
  curationKeys,
  fetchPropertyCollectionMemberSet,
  fetchPropertyTagMemberSet,
} from '@/lib/queries';
import { fmtAbsolute, fmtRelative } from '@/lib/format';
import type { Collection, Note } from '@/lib/types';
import ErrorBanner from '@/components/ErrorBanner';
import { readFailed } from '@/components/ReadFailedMark';
import { TagAssignList } from '@/components/curation/TagAssignList';
import { PencilIcon, TrashIcon } from '@/components/icons';
import { SectionLabel } from '@/components/section';
import ArtifactLinkRow from './ArtifactLinkRow';
import { NOTE_ATTACHMENT_MAX_FILES, sortAttachments } from '@/lib/noteAttachments';
import {
  AttachButton,
  DropHint,
  FileProblems,
  NoteAttachmentList,
  StagedFiles,
  useFileDrop,
  useStrayFileDropGuard,
} from './NoteAttachments';

export default function CurationBlock({
  property_id,
  sreality_id,
  listing_id,
}: {
  property_id: number;
  // NULL for a post-Gate-2 non-sreality listing. Curation is PROPERTY-grain
  // (rule 18) so this block keeps working either way; the note-provenance API
  // already COALESCEs the surrogate when the legacy id is absent.
  sreality_id: number | null;
  // The viewed listing's SURROGATE id — always present, unlike sreality_id
  // (NULL for a post-Gate-2 listing). Passed through to NotesRow so a note's
  // provenance survives even when the legacy id is unavailable.
  listing_id: number;
}) {
  return (
    <div className="space-y-7">
      {/* The pipeline bookmark moved to the listing-detail header action bar
          (PipelineToggle) — a deal-stage verb belongs at the top, next to
          "New estimation", not buried below the estimates. This block keeps
          the secondary curation: collections, tags, artifact link, notes. */}
      <CollectionsRow property_id={property_id} />
      <TagsRow property_id={property_id} />
      <ArtifactLinkRow property_id={property_id} />
      <NotesRow property_id={property_id} sreality_id={sreality_id} listing_id={listing_id} />
    </div>
  );
}

/* The phone property page's tags + notes (#1767): the same rows, Czech labels,
 * the notes composer always open. Collections sit in the page's curation row
 * there (the collection sheet), so they are not repeated. */
export function PhoneTagsAndNotes({
  property_id,
  sreality_id,
  listing_id,
}: {
  property_id: number;
  sreality_id: number | null;
  listing_id: number;
}) {
  return (
    <div className="space-y-7">
      <TagsRow property_id={property_id} label="Štítky" />
      <ArtifactLinkRow property_id={property_id} label="Odkaz na artefakt" />
      <NotesRow
        property_id={property_id}
        sreality_id={sreality_id}
        listing_id={listing_id}
        label="Poznámky"
        alwaysOpen
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Collections                                                                */
/* -------------------------------------------------------------------------- */

function CollectionsRow({ property_id }: { property_id: number }) {
  const qc = useQueryClient();

  const allQ = useQuery({
    queryKey: curationKeys.collections,
    queryFn: listCollections,
    staleTime: 30_000,
  });

  const membershipQ = useQuery({
    queryKey: curationKeys.propertyCollectionMembers,
    queryFn: fetchPropertyCollectionMemberSet,
    staleTime: 30_000,
  });

  const memberIds = useMemo(
    () => new Set(membershipQ.data?.get(property_id) ?? []),
    [membershipQ.data, property_id],
  );

  const collections = allQ.data?.data ?? [];

  const add = useMutation({
    mutationFn: (collection_id: number) =>
      addPropertiesToCollection(collection_id, [property_id]),
    onSuccess: (_, collection_id) => revalidateCollections(qc, { collection_id }),
  });

  const remove = useMutation({
    mutationFn: (collection_id: number) =>
      removePropertyFromCollection(collection_id, property_id),
    onSuccess: (_, collection_id) => revalidateCollections(qc, { collection_id }),
  });

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <SectionLabel>Collections</SectionLabel>
        <Link
          to={ROUTES.collections.build()}
          className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
        >
          Manage →
        </Link>
      </div>

      {readFailed(allQ) || readFailed(membershipQ) ? (
        <ErrorBanner
          title="Kolekce"
          message="se nepodařilo načíst."
          onRetry={() => {
            if (readFailed(allQ)) void allQ.refetch();
            if (readFailed(membershipQ)) void membershipQ.refetch();
          }}
        />
      ) : allQ.isLoading ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">Loading…</p>
      ) : collections.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">
          No collections yet.{' '}
          <Link
            to={ROUTES.collections.build()}
            className="text-[var(--color-copper)] hover:underline"
          >
            Start one
          </Link>
          .
        </p>
      ) : (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {collections.map((c) => (
            <li key={c.id}>
              <CollectionToggle
                c={c}
                member={memberIds.has(c.id)}
                pending={add.isPending || remove.isPending}
                onAdd={() => add.mutate(c.id)}
                onRemove={() => remove.mutate(c.id)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CollectionToggle({
  c,
  member,
  pending,
  onAdd,
  onRemove,
}: {
  c: Collection;
  member: boolean;
  pending: boolean;
  onAdd: () => void;
  onRemove: () => void;
}) {
  const cls = member
    ? 'bg-[var(--color-copper-soft)] text-[var(--color-copper)] border-[var(--color-copper)]/40'
    : 'bg-[var(--color-paper-2)] text-[var(--color-ink-3)] border-[var(--color-rule)] hover:text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)]';
  return (
    <button
      type="button"
      onClick={member ? onRemove : onAdd}
      disabled={pending}
      aria-pressed={member}
      className={[
        'inline-flex items-center gap-1.5 px-2.5 py-1 text-[0.78rem] rounded-[var(--radius-sm)] border transition-colors disabled:opacity-60',
        cls,
      ].join(' ')}
    >
      <span aria-hidden className="text-[0.65rem] leading-none">
        {member ? '✓' : '+'}
      </span>
      <span className="truncate max-w-[14rem]">{c.name}</span>
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Tags                                                                       */
/* -------------------------------------------------------------------------- */

/* The one assign list (components/curation/TagAssignList), inline: the same
 * rows and writes the board card opens in a popover and the phone card in a
 * sheet. Membership comes from the shared member map every tag chip reads. */
function TagsRow({ property_id, label = 'Tags' }: { property_id: number; label?: string }) {
  const membershipQ = useQuery({
    queryKey: curationKeys.propertyTagMembers,
    queryFn: fetchPropertyTagMemberSet,
    staleTime: 30_000,
  });
  const memberIds = useMemo(
    () => new Set(membershipQ.data?.get(property_id) ?? []),
    [membershipQ.data, property_id],
  );

  if (readFailed(membershipQ)) {
    return (
      <div>
        <SectionLabel>{label}</SectionLabel>
        <ErrorBanner
          title="Štítky"
          message="se nepodařilo načíst."
          onRetry={() => void membershipQ.refetch()}
        />
      </div>
    );
  }

  return (
    <div>
      <SectionLabel>{label}</SectionLabel>
      <div className="mt-3">
        <TagAssignList property_id={property_id} memberIds={memberIds} size="block" />
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Notes                                                                      */
/* -------------------------------------------------------------------------- */

function NotesRow({
  property_id,
  sreality_id,
  listing_id,
  label = 'Notes',
  alwaysOpen = false,
}: {
  property_id: number;
  // NULL for a non-sreality listing; createPropertyNote already omits the field
  // when absent and the server COALESCEs provenance from listing_id.
  sreality_id: number | null;
  listing_id: number;
  label?: string;
  /* The phone keeps the composer in view: its curation row's "Poznámka" scrolls here. */
  alwaysOpen?: boolean;
}) {
  const qc = useQueryClient();
  const [body, setBody] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Files waiting for the note to be saved, and what a drop or an upload could not take.
  const [staged, setStaged] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const notesLabelId = useId();

  const notesQ = useQuery({
    queryKey: curationKeys.propertyNotes(property_id),
    queryFn: () => listPropertyNotes(property_id),
    staleTime: 30_000,
  });

  const refreshNotes = () =>
    qc.invalidateQueries({ queryKey: curationKeys.propertyNotes(property_id) });

  const create = useMutation({
    /* The note first, then its files one by one; a file that fails leaves the note saved and
     * is reported by name (drop it onto the note to try again). */
    mutationFn: async ({ text, files }: { text: string; files: File[] }) => {
      const note = await createPropertyNote(property_id, text, sreality_id ?? undefined, listing_id);
      const failed: string[] = [];
      for (const [i, file] of files.entries()) {
        setProgress({ done: i, total: files.length });
        try {
          await uploadNoteAttachment(property_id, note.id, file);
        } catch (err) {
          failed.push(`${file.name}: ${(err as Error).message || 'upload failed'}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      setBody('');
      setStaged([]);
      setError(null);
      setProblems(failed);
      refreshNotes();
      qc.invalidateQueries({ queryKey: curationKeys.noteCounts });
    },
    onError: (err: ApiError | Error) =>
      setError(err.message || 'Failed to save note'),
    onSettled: () => setProgress(null),
  });

  const stage = (files: File[]) => {
    const fresh = files.filter(
      (f) => !staged.some((s) => s.name === f.name && s.size === f.size && s.lastModified === f.lastModified),
    );
    const { ok, problems: refused } = sortAttachments(fresh, NOTE_ATTACHMENT_MAX_FILES - staged.length);
    setStaged((prev) => [...prev, ...ok]);
    setProblems(refused);
  };
  const composerDrop = useFileDrop(stage);
  useStrayFileDropGuard();

  const notes = notesQ.data?.data ?? [];
  const failed = readFailed(notesQ);
  const trimmed = body.trim();
  const canSave = (trimmed.length > 0 || staged.length > 0) && !create.isPending;

  return (
    <details
      className="group"
      open={alwaysOpen || failed || notes.length > 0 || staged.length > 0 || problems.length > 0 || composerDrop.over}
      {...composerDrop.handlers}
    >
      <summary className="cursor-pointer list-none flex items-baseline justify-between gap-4">
        <SectionLabel>
          <span id={notesLabelId}>{label}</span>
          <span className="ml-2 font-mono tabular-nums text-[var(--color-ink-4)] tracking-normal">
            {failed ? '(—)' : `(${notes.length})`}
          </span>
        </SectionLabel>
        <span className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] group-open:hidden">
          Show
        </span>
        <span className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hidden group-open:inline">
          Hide
        </span>
      </summary>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSave) return;
          // A files-only note still needs a body (1-4000 chars); its file names say what it holds.
          const text = trimmed || `Attached: ${staged.map((f) => f.name).join(', ')}`.slice(0, 4000);
          create.mutate({ text, files: staged });
        }}
        className="relative mt-3"
      >
        <textarea
          aria-labelledby={notesLabelId}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files);
            if (files.length === 0) return;
            e.preventDefault();
            stage(files);
          }}
          placeholder="Add a note… (drop or paste files to attach)"
          rows={2}
          maxLength={4000}
          className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] resize-y"
        />
        <StagedFiles
          files={staged}
          onRemove={(file) => setStaged((prev) => prev.filter((f) => f !== file))}
          disabled={create.isPending}
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <AttachButton
              onFiles={stage}
              label="Attach files"
              showText
              disabled={create.isPending || staged.length >= NOTE_ATTACHMENT_MAX_FILES}
            />
            <p className="text-[0.7rem] text-[var(--color-ink-4)] tabular-nums">
              {body.length} / 4000
            </p>
          </div>
          <button
            type="submit"
            disabled={!canSave}
            className="px-3 py-1 text-[0.78rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {create.isPending
              ? progress
                ? `Uploading ${progress.done + 1} / ${progress.total}…`
                : 'Saving…'
              : 'Save note'}
          </button>
        </div>
        {error && (
          <p className="mt-1.5 text-[0.7rem] text-[var(--color-brick)]">{error}</p>
        )}
        <FileProblems problems={problems} onDismiss={() => setProblems([])} />
        {composerDrop.over && <DropHint label="Drop files to attach to a new note" />}
      </form>

      {failed ? (
        <ErrorBanner
          title="Poznámky"
          message="se nepodařilo načíst."
          onRetry={() => void notesQ.refetch()}
        />
      ) : notesQ.isLoading ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">Loading…</p>
      ) : notes.length === 0 ? null : (
        <ul className="mt-4 space-y-3">
          {notes.map((n) => (
            <li key={n.id}>
              <NoteRow
                property_id={property_id}
                note={n}
                onSave={(text) => updatePropertyNote(property_id, n.id, text).then(refreshNotes)}
                onDelete={() =>
                  deletePropertyNote(property_id, n.id).then(() => {
                    refreshNotes();
                    qc.invalidateQueries({ queryKey: curationKeys.noteCounts });
                  })
                }
                onFilesChanged={refreshNotes}
              />
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function NoteRow({
  property_id,
  note,
  onSave,
  onDelete,
  onFilesChanged,
}: {
  property_id: number;
  note: Note;
  onSave: (body: string) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  onFilesChanged: () => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<File[]>([]);
  const [fileProblems, setFileProblems] = useState<string[]>([]);

  const attachments = note.attachments ?? [];
  /* Dropped or picked files go straight onto this note, one by one; the list re-reads once
   * they are all in. */
  const attach = async (files: File[]) => {
    const { ok, problems } = sortAttachments(
      files,
      NOTE_ATTACHMENT_MAX_FILES - attachments.length - uploading.length,
    );
    setFileProblems(problems);
    if (ok.length === 0) return;
    setUploading((prev) => [...prev, ...ok]);
    const failed: string[] = [];
    for (const file of ok) {
      try {
        await uploadNoteAttachment(property_id, note.id, file);
      } catch (err) {
        failed.push(`${file.name}: ${(err as Error).message || 'upload failed'}`);
      }
    }
    await onFilesChanged();
    setUploading((prev) => prev.filter((f) => !ok.includes(f)));
    if (failed.length > 0) setFileProblems((prev) => [...prev, ...failed]);
  };
  const drop = useFileDrop((files) => void attach(files));

  const trimmed = draft.trim();
  const files = (
    <>
      <NoteAttachmentList
        propertyId={property_id}
        noteId={note.id}
        attachments={attachments}
        uploading={uploading}
        onRemoved={onFilesChanged}
      />
      <FileProblems problems={fileProblems} onDismiss={() => setFileProblems([])} />
    </>
  );

  return (
    <div
      className="group relative border-l-2 border-[var(--color-copper)]/40 pl-3"
      aria-label={`Note from ${fmtAbsolute(note.created_at)}`}
      role="group"
      {...drop.handlers}
    >
      {editing ? (
        <>
          <textarea
            aria-label="Edit note"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            maxLength={4000}
            autoFocus
            className="w-full px-3 py-2 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] resize-y"
          />
          <div className="mt-1.5 flex items-center justify-between gap-3">
            <p className="text-[0.7rem] text-[var(--color-ink-4)] tabular-nums">
              {draft.length} / 4000
            </p>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setDraft(note.body);
                  setError(null);
                }}
                disabled={busy}
                className="px-2 py-1 text-[0.75rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (trimmed.length === 0 || trimmed === note.body) {
                    setEditing(false);
                    setDraft(note.body);
                    return;
                  }
                  setBusy(true);
                  setError(null);
                  onSave(trimmed)
                    .then(() => setEditing(false))
                    .catch((err: ApiError | Error) =>
                      setError(err.message || 'Failed to save note'))
                    .finally(() => setBusy(false));
                }}
                disabled={busy || trimmed.length === 0}
                className="px-3 py-1 text-[0.75rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {busy ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
          {files}
          {error && (
            <p className="mt-1.5 text-[0.7rem] text-[var(--color-brick)]">{error}</p>
          )}
        </>
      ) : (
        <>
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-[var(--color-ink)] whitespace-pre-wrap break-words">
              {note.body}
            </p>
            <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
              <AttachButton
                onFiles={(picked) => void attach(picked)}
                label="Attach files to this note"
              />
              <button
                type="button"
                onClick={() => {
                  setDraft(note.body);
                  setEditing(true);
                }}
                aria-label="Edit note"
                title="Edit note"
                className="inline-flex items-center justify-center w-5 h-5 rounded-[var(--radius-xs)] text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] transition-colors"
              >
                <PencilIcon className="h-[11px] w-[11px]" />
              </button>
              <button
                type="button"
                onClick={() => setConfirmingDelete(true)}
                aria-label="Delete note"
                title="Delete note"
                className="inline-flex items-center justify-center w-5 h-5 rounded-[var(--radius-xs)] text-[var(--color-ink-4)] hover:text-[var(--color-brick)] hover:bg-[var(--color-paper-2)] transition-colors"
              >
                <TrashIcon className="h-[11px] w-[11px]" />
              </button>
            </div>
          </div>
          {files}
          {confirmingDelete ? (
            <div className="mt-1.5 flex items-center gap-2">
              <span className="text-[0.7rem] text-[var(--color-brick)]">
                {attachments.length > 0
                  ? `Delete this note and its ${attachments.length === 1 ? 'file' : `${attachments.length} files`}?`
                  : 'Delete this note?'}
              </span>
              <button
                type="button"
                onClick={() => {
                  setBusy(true);
                  onDelete().catch((err: ApiError | Error) => {
                    setError(err.message || 'Failed to delete note');
                    setBusy(false);
                    setConfirmingDelete(false);
                  });
                }}
                disabled={busy}
                className="px-2 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 disabled:opacity-50 transition-colors"
              >
                {busy ? 'Deleting…' : 'Delete'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingDelete(false)}
                disabled={busy}
                className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <p
              className="mt-1 text-[0.7rem] tracking-wide text-[var(--color-ink-4)] cursor-help"
              title={
                note.updated_at
                  ? `Written ${fmtAbsolute(note.created_at)} · edited ${fmtAbsolute(note.updated_at)}`
                  : fmtAbsolute(note.created_at)
              }
            >
              {fmtRelative(note.created_at)}
              {note.updated_at && ' (edited)'}
            </p>
          )}
          {error && !confirmingDelete && (
            <p className="mt-1.5 text-[0.7rem] text-[var(--color-brick)]">{error}</p>
          )}
        </>
      )}
      {drop.over && <DropHint label="Drop to attach to this note" />}
    </div>
  );
}
