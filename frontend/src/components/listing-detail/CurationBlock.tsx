/* Tags + artifact link + notes for a single PROPERTY. Wired into PropertyDetail
 * (and, as PhoneTagsAndNotes, into the phone property page).
 *
 * Curation is property-grain (migration 202): a tag / note describes the
 * real-world property, so this block operates on the listing's `property_id`.
 * The viewed listing is recorded as a note's `origin_listing_ref_id` (display
 * provenance — "written while viewing this advert"). Collection membership is
 * the header's "Uložit do kolekce" (CollectionSaveToggle), not a row here.
 *
 * Notes cross a share only when their author marks them shared (migration 610,
 * docs/design/collection-sharing.md §3.7): the composer offers the mark when the
 * property has an AUDIENCE (lib/collectionCache `shareAudience`), and another
 * author's note reads as theirs — named, never editable here.
 *
 * Reads run as the signed-in user: the tag index and the notes through the
 * JWT-gated API, the "does THIS property carry tag X" map from
 * `property_tags_public`, a security_invoker view over the account-scoped RLS.
 * Writes always go through the API. A failed read is said out loud with a
 * retry and never drawn as an empty list or an unticked toggle (MS16).
 */

import { useId, useMemo, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  ApiError,
  createPropertyNote,
  deletePropertyNote,
  listPropertyNotes,
  updatePropertyNote,
  uploadAttachment,
} from '@/lib/api';
import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { curationKeys } from '@/lib/queries';
import { browseReads } from '@/lib/curationSettle';
import { useTagReads } from '@/lib/useCardTags';
import {
  isMine,
  shareAudience,
  useCollectionMembership,
} from '@/lib/collectionCache';
import { fmtAbsolute, fmtRelative } from '@/lib/format';
import type { Note } from '@/lib/types';
import DelegatedStamp from '@/components/DelegatedStamp';
import ErrorBanner from '@/components/ErrorBanner';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { TagAssignList } from '@/components/curation/TagAssignList';
import { PencilIcon, TrashIcon } from '@/components/icons';
import { SectionLabel } from '@/components/section';
import ArtifactLinkRow from './ArtifactLinkRow';
import { NOTE_ATTACHMENT_MAX_FILES, sortAttachments } from '@/lib/attachments';
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
  listing_id,
}: {
  property_id: number;
  /* The viewed listing's surrogate id: a new note's provenance. */
  listing_id: number;
}) {
  return (
    <div className="space-y-7">
      {/* The pipeline bookmark and the collection save live in the header
          action bar (PipelineToggle, CollectionSaveToggle) — a verb belongs at
          the top, next to "New estimation", not buried below the estimates.
          This block keeps the secondary curation: tags, artifact link, notes. */}
      <TagsRow property_id={property_id} />
      <ArtifactLinkRow property_id={property_id} />
      <NotesRow property_id={property_id} listing_id={listing_id} />
    </div>
  );
}

/* The phone property page's tags + notes (#1767): the same rows, Czech labels,
 * the notes composer always open. Collections sit in the page's curation row
 * there (the collection sheet), so they are not repeated. */
export function PhoneTagsAndNotes({
  property_id,
  listing_id,
}: {
  property_id: number;
  listing_id: number;
}) {
  return (
    <div className="space-y-7">
      <TagsRow property_id={property_id} label="Štítky" />
      <ArtifactLinkRow property_id={property_id} label="Odkaz na artefakt" />
      <NotesRow
        property_id={property_id}
        listing_id={listing_id}
        label="Poznámky"
        lang="cs"
        alwaysOpen
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Tags                                                                       */
/* -------------------------------------------------------------------------- */

/* The one assign list (components/curation/TagAssignList), inline: the same
 * rows and writes the board card opens in a popover and the phone card in a
 * sheet. Membership comes from the shared member map every tag chip reads. */
function TagsRow({ property_id, label = 'Tags' }: { property_id: number; label?: string }) {
  const { membersQ: membershipQ } = useTagReads({ index: false });
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

/* The note rows' words in each page's language (ruling 8): English on the
 * desktop property page, Czech on the phone. */
const NOTE_WORDS = {
  en: {
    lang: 'en',
    shareWith: (who: string) => `Share with ${who}`,
    shared: 'Shared',
    sharedNobody: 'Shared · nobody can see it yet',
    unshare: 'Make private',
    noteFrom: 'Note from',
    noteBy: 'Note by',
    edited: 'edited',
  },
  cs: {
    lang: 'cs',
    shareWith: (who: string) => `Sdílet s ${who}`,
    shared: 'Sdíleno',
    sharedNobody: 'Sdíleno · zatím to nikdo nevidí',
    unshare: 'Nesdílet',
    noteFrom: 'Poznámka z',
    noteBy: 'Poznámka od',
    edited: 'upraveno',
  },
} as const;
type NoteWords = (typeof NOTE_WORDS)[keyof typeof NOTE_WORDS];

function NotesRow({
  property_id,
  listing_id,
  label = 'Notes',
  lang = 'en',
  alwaysOpen = false,
}: {
  property_id: number;
  listing_id: number;
  label?: string;
  lang?: keyof typeof NOTE_WORDS;
  /* The phone keeps the composer in view: its curation row's "Poznámka" scrolls here. */
  alwaysOpen?: boolean;
}) {
  const qc = useQueryClient();
  const words = NOTE_WORDS[lang];
  const actAs = useActAs();
  const notesKey = actingKey(actAs, curationKeys.propertyNotes(property_id));
  /* Who a SHARED note here would reach: the counterparties of every share whose
   * collection holds this property. Empty = nobody, so no checkbox is offered;
   * null = not known (a read loading or failed), which must never read as
   * "nobody" — no checkbox, no share toggle, no "nobody can see it" claim.
   * Acting as the owner (design §3.6) it is never known: the owner's share rows
   * are invisible under the header, so their collections read as shared with
   * nobody — a false privacy claim on the owner's notes. */
  const membership = useCollectionMembership(property_id);
  const audienceFailed = actAs == null && readFailed(membership.query);
  const { ids: collectionIds, byId, ready: audienceReady } = membership;
  const audience = useMemo(
    () => (actAs == null && audienceReady ? shareAudience(collectionIds, byId) : null),
    [actAs, audienceReady, collectionIds, byId],
  );
  const canShare = audience != null && audience.length > 0;
  const [shareNew, setShareNew] = useState(false);
  const [body, setBody] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Files waiting for the note to be saved, and what a drop or an upload could not take.
  const [staged, setStaged] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const notesLabelId = useId();

  const notesQ = useQuery({
    queryKey: notesKey,
    queryFn: () => listPropertyNotes(property_id, ...actOpts(actAs)),
    staleTime: 30_000,
  });

  const refreshNotes = () => qc.invalidateQueries({ queryKey: notesKey });
  /* Browse's note marks — never moved by a note written as the owner. */
  const refreshNoteCounts = () => {
    for (const queryKey of browseReads(actAs, [curationKeys.noteCounts])) void qc.invalidateQueries({ queryKey });
  };

  const create = useMutation({
    /* The note first, then its files one by one; a file that fails leaves the note saved and
     * is reported by name (drop it onto the note to try again). */
    mutationFn: async ({ text, files }: { text: string; files: File[] }) => {
      const note = await createPropertyNote(
        property_id, text, listing_id, shareNew && canShare, ...actOpts(actAs),
      );
      const failed: string[] = [];
      for (const [i, file] of files.entries()) {
        setProgress({ done: i, total: files.length });
        try {
          await uploadAttachment(property_id, file, { note_id: note.id, actAs });
        } catch (err) {
          failed.push(`${file.name}: ${(err as Error).message || 'upload failed'}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      setBody('');
      setShareNew(false);
      setStaged([]);
      setError(null);
      setProblems(failed);
      refreshNotes();
      refreshNoteCounts();
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
        {canShare && (
          <label className="mt-2 flex items-center gap-2 w-fit text-[0.75rem] text-[var(--color-ink-2)] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={shareNew}
              onChange={(e) => setShareNew(e.target.checked)}
              disabled={create.isPending}
              className="accent-[var(--color-ink-2)]"
            />
            <span>{words.shareWith(audience.join(', '))}</span>
          </label>
        )}
        {audienceFailed && (
          <div className="mt-2">
            <ReadFailedMark
              what="Sdílení"
              variant="header"
              onRetry={() => void membership.query.refetch()}
            />
          </div>
        )}
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
                audience={audience}
                words={words}
                onSave={(text) =>
                  updatePropertyNote(property_id, n.id, { body: text }, ...actOpts(actAs)).then(refreshNotes)
                }
                onToggleShared={() =>
                  updatePropertyNote(property_id, n.id, { shared: !n.shared }, ...actOpts(actAs)).then(
                    refreshNotes,
                  )
                }
                onDelete={() =>
                  deletePropertyNote(property_id, n.id, ...actOpts(actAs)).then(() => {
                    refreshNotes();
                    refreshNoteCounts();
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
  audience,
  words,
  onSave,
  onToggleShared,
  onDelete,
  onFilesChanged,
}: {
  property_id: number;
  note: Note;
  audience: readonly string[] | null;
  words: NoteWords;
  onSave: (body: string) => Promise<unknown>;
  onToggleShared: () => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  onFilesChanged: () => Promise<unknown>;
}) {
  if (!isMine(note)) {
    return <ForeignNoteRow property_id={property_id} note={note} words={words} />;
  }
  return (
    <OwnNoteRow
      property_id={property_id}
      note={note}
      audience={audience}
      words={words}
      onSave={onSave}
      onToggleShared={onToggleShared}
      onDelete={onDelete}
      onFilesChanged={onFilesChanged}
    />
  );
}

/* Another author's SHARED note (they marked it; a share put it in reach): read
 * only — no attach, edit, delete or drop zone, files open but carry no ×. A
 * neutral border, not copper: it is not mine to work. */
function ForeignNoteRow({
  property_id,
  note,
  words,
}: {
  property_id: number;
  note: Note;
  words: NoteWords;
}) {
  return (
    <div
      className="relative border-l-2 border-[var(--color-rule-strong)] pl-3"
      aria-label={`${words.noteBy} ${note.owner_email} · ${fmtAbsolute(note.created_at)}`}
      role="group"
    >
      <p className="text-sm text-[var(--color-ink)] whitespace-pre-wrap break-words">{note.body}</p>
      <NoteAttachmentList
        propertyId={property_id}
        attachments={note.attachments ?? []}
        uploading={[]}
      />
      <div className="mt-1">
        <NoteMeta note={note} words={words} author={note.owner_email} />
      </div>
    </div>
  );
}

/* A note's time line: relative, the exact dates on hover, the author first on
 * another author's note; then its delegated stamp, if any. */
function NoteMeta({
  note,
  words,
  author,
}: {
  note: Note;
  words: NoteWords;
  author?: string | null;
}) {
  return (
    <>
      <p
        className="text-[0.7rem] tracking-wide text-[var(--color-ink-4)] cursor-help"
        title={
          note.updated_at
            ? `Written ${fmtAbsolute(note.created_at)} · edited ${fmtAbsolute(note.updated_at)}`
            : fmtAbsolute(note.created_at)
        }
      >
        {author != null && (
          <>
            <span className="text-[var(--color-ink-3)]">{author}</span> ·{' '}
          </>
        )}
        {fmtRelative(note.created_at)}
        {note.updated_at && ` (${words.edited})`}
      </p>
      {/* Who last wrote it acting as the owner (ruling 5); nothing for the owner's own edits. */}
      <DelegatedStamp stamp={note} lang={words.lang} />
    </>
  );
}

function OwnNoteRow({
  property_id,
  note,
  audience,
  words,
  onSave,
  onToggleShared,
  onDelete,
  onFilesChanged,
}: {
  property_id: number;
  note: Note;
  audience: readonly string[] | null;
  words: NoteWords;
  onSave: (body: string) => Promise<unknown>;
  onToggleShared: () => Promise<unknown>;
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

  const actAs = useActAs();
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
        await uploadAttachment(property_id, file, { note_id: note.id, actAs });
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
      aria-label={`${words.noteFrom} ${fmtAbsolute(note.created_at)}`}
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
                  ? `Delete this note? Its ${attachments.length === 1 ? 'file stays' : `${attachments.length} files stay`} with the property.`
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
            <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <NoteMeta note={note} words={words} />
              {note.shared && (
                <span className="text-[0.62rem] tracking-[0.12em] uppercase text-[var(--color-ink-3)]">
                  {audience != null && audience.length === 0 ? words.sharedNobody : words.shared}
                </span>
              )}
              {/* Making private is always safe; sharing names who it reaches, and
                  waits until that is known. */}
              {(note.shared || (audience != null && audience.length > 0)) && (
                <button
                  type="button"
                  onClick={() => {
                    setBusy(true);
                    setError(null);
                    onToggleShared()
                      .catch((err: ApiError | Error) =>
                        setError(err.message || 'Failed to update note'))
                      .finally(() => setBusy(false));
                  }}
                  disabled={busy}
                  className="text-[0.7rem] tracking-wide text-[var(--color-ink-3)] underline-offset-2 hover:text-[var(--color-ink)] hover:underline disabled:opacity-50"
                >
                  {note.shared ? words.unshare : words.shareWith((audience ?? []).join(', '))}
                </button>
              )}
            </div>
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
