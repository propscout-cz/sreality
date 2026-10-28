/* The operator's own words about a single PROPERTY. On the desktop property page
 * the default export is the "Poznámky" section under the header (operator
 * 2026-10-23): the notes, read and written in place, with the artifact link in
 * the section's header; the tags are the header's "Štítky" button (TagsToggle).
 * The phone property page renders tags + artifact link + notes as
 * PhoneTagsAndNotes.
 *
 * Curation is property-grain (migration 202): a tag / note describes the
 * real-world property, so this block operates on the listing's `property_id`.
 * The viewed listing is recorded as a note's `origin_listing_ref_id` (display
 * provenance — "written while viewing this advert"). Collection membership is
 * the header's "Uložit do kolekce" (CollectionSaveToggle), not a row here.
 *
 * A viewing is a note with a date (migration 666, lib/viewings): the composer's
 * "Prohlídka" adds a day and time and makes the text optional, and the property's
 * upcoming viewings lead the list under a calendar leaf; the phone's Prohlídky
 * tab reads the same notes.
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

import { useId, useMemo, useState, type ReactNode } from 'react';
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
import {
  defaultSlot,
  isUpcoming,
  sectionOrder,
  slotInstant,
  slotIsComplete,
  viewingSlot,
  viewingWhen,
  type ViewingSlot,
} from '@/lib/viewings';
import { CalendarIcon } from '@/components/phone/phoneIcons';
import DelegatedStamp from '@/components/DelegatedStamp';
import ErrorBanner from '@/components/ErrorBanner';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { TagAssignList } from '@/components/curation/TagAssignList';
import { PencilIcon, TrashIcon } from '@/components/icons';
import Panel from '@/components/Panel';
import ArtifactLinkRow from './ArtifactLinkRow';
import { ViewingFields, ViewingHead, ViewingToggle } from './NoteViewing';
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
    <div className="mt-6">
      <NotesRow
        property_id={property_id}
        listing_id={listing_id}
        aside={<ArtifactLinkRow property_id={property_id} bare />}
      />
    </div>
  );
}

/* The phone property page's tags + notes (#1767): the same rows, the notes
 * composer always open, each its own section card. Collections sit in the page's
 * curation row there (the collection sheet), so they are not repeated. */
export function PhoneTagsAndNotes({
  property_id,
  listing_id,
}: {
  property_id: number;
  listing_id: number;
}) {
  return (
    <div className="space-y-4">
      <TagsRow property_id={property_id} label="Štítky" />
      <ArtifactLinkRow property_id={property_id} />
      <NotesRow property_id={property_id} listing_id={listing_id} alwaysOpen />
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
      <Panel title={label}>
        <ErrorBanner
          title="Štítky"
          message="se nepodařilo načíst."
          onRetry={() => void membershipQ.refetch()}
        />
      </Panel>
    );
  }

  return (
    <Panel title={label}>
      <TagAssignList property_id={property_id} memberIds={memberIds} size="block" />
    </Panel>
  );
}

/* -------------------------------------------------------------------------- */
/* Notes                                                                      */
/* -------------------------------------------------------------------------- */

const WORDS = {
  lang: 'cs',
  shareWith: (who: string) => `Sdílet s ${who}`,
  shared: 'Sdíleno',
  sharedNobody: 'Sdíleno · zatím to nikdo nevidí',
  unshare: 'Nesdílet',
  noteFrom: 'Poznámka z',
  noteBy: 'Poznámka od',
  edited: 'upraveno',
} as const;

/* The newest few; the rest one click away, so the section stays a glance on every tab. */
const NOTES_SHOWN = 3;
const NO_NOTES: Note[] = [];
const BODY_MAX = 4000;

const PRIMARY =
  'inline-flex items-center px-3.5 py-1.5 text-[0.8rem] font-medium rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
const QUIET =
  'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[0.8rem] rounded-[var(--radius-sm)] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] hover:bg-[var(--color-paper-2)] disabled:opacity-50 transition-colors';

export function NotesRow({
  property_id,
  listing_id,
  label = 'Poznámky',
  alwaysOpen = false,
  aside,
}: {
  property_id: number;
  listing_id: number;
  label?: string;
  /* The phone keeps the composer open: its curation row's "Poznámka" scrolls here. */
  alwaysOpen?: boolean;
  /* Drawn at the right of the heading (the desktop section: the artifact link). */
  aside?: ReactNode;
}) {
  const qc = useQueryClient();
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
  /* Set = the new note is a viewing on this Prague day and time. */
  const [slot, setSlot] = useState<ViewingSlot | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Files waiting for the note to be saved, and what a drop or an upload could not take.
  const [staged, setStaged] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  /* Opened by a focus, closed only by Zrušit or a save — never by a blur: a file
   * picker's dialog blurs the page, and the pick must land in an open composer. */
  const [opened, setOpened] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const notesLabelId = useId();

  const notesQ = useQuery({
    queryKey: notesKey,
    queryFn: () => listPropertyNotes(property_id, ...actOpts(actAs)),
    staleTime: 30_000,
  });

  /* The Prohlídky tab reads the same notes. */
  const refreshNotes = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: notesKey }),
      qc.invalidateQueries({ queryKey: curationKeys.viewings }),
    ]);
  /* Browse's note marks — never moved by a note written as the owner. */
  const refreshNoteCounts = () => {
    for (const queryKey of browseReads(actAs, [curationKeys.noteCounts])) void qc.invalidateQueries({ queryKey });
  };

  const create = useMutation({
    /* The note first, then its files one by one; a file that fails leaves the note saved and
     * is reported by name (drop it onto the note to try again). */
    mutationFn: async ({ text, files, viewing_at }: { text: string; files: File[]; viewing_at: string | null }) => {
      const note = await createPropertyNote(
        property_id,
        { body: text, origin_listing_ref_id: listing_id, shared: shareNew && canShare, viewing_at },
        ...actOpts(actAs),
      );
      const failed: string[] = [];
      for (const [i, file] of files.entries()) {
        setProgress({ done: i, total: files.length });
        try {
          await uploadAttachment(property_id, file, { note_id: note.id, actAs });
        } catch (err) {
          failed.push(`${file.name}: ${(err as Error).message || 'nahrání selhalo'}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      setBody('');
      setSlot(null);
      setShareNew(false);
      setStaged([]);
      setError(null);
      setProblems(failed);
      setOpened(false);
      refreshNotes();
      refreshNoteCounts();
    },
    onError: (err: ApiError | Error) =>
      setError(err.message || 'Poznámku se nepodařilo uložit'),
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

  const notes = notesQ.data?.data ?? NO_NOTES;
  const failed = readFailed(notesQ);
  const trimmed = body.trim();
  /* A viewing needs a whole date and time and nothing else; a note, text or files. */
  const canSave =
    (slot ? slotIsComplete(slot) : trimmed.length > 0 || staged.length > 0) && !create.isPending;
  /* The composer reads as one line until it is used; then its tools appear. */
  const expanded =
    alwaysOpen || opened || body.length > 0 || staged.length > 0 || slot != null || create.isPending;
  const ordered = useMemo(() => sectionOrder(notes), [notes]);
  const shown = showAll ? ordered : ordered.slice(0, NOTES_SHOWN);

  /* A section card (C1–C10): the title and a bare count ("—" when the read failed, never a 0
   * it does not know), the viewing and the desktop's artifact link as its actions (C6). The
   * wrapper takes a dropped file anywhere on the card. */
  return (
    <div className="relative" {...composerDrop.handlers}>
      <Panel
        title={<span id={notesLabelId}>{label}</span>}
        count={failed ? '—' : notesQ.isSuccess ? notes.length : null}
        accessory={
          !alwaysOpen && slot == null ? (
            <>
              {/* The desktop composer is one line until used: the viewing is one click from the heading. */}
              <button
                type="button"
                onClick={() => {
                  setSlot(defaultSlot());
                  setOpened(true);
                }}
                className={QUIET}
              >
                <CalendarIcon className="h-3.5 w-3.5" />
                <span>Naplánovat prohlídku</span>
              </button>
              {aside}
            </>
          ) : (
            aside
          )
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!canSave) return;
            // A files-only note still needs a body (1-4000 chars); its file names say what it holds.
            // A viewing's date says what it is: its text may stay empty.
            const text =
              trimmed || (slot ? '' : `Přílohy: ${staged.map((f) => f.name).join(', ')}`.slice(0, BODY_MAX));
            create.mutate({ text, files: staged, viewing_at: slot ? slotInstant(slot) : null });
          }}
          onFocus={() => setOpened(true)}
          className={[
            'rounded-[var(--radius-md)] border bg-[var(--color-paper-3)] transition-colors',
            expanded ? 'border-[var(--color-copper)]/50' : 'border-[var(--color-rule-strong)] hover:border-[var(--color-copper)]/50',
          ].join(' ')}
        >
          {slot && (
            <div className="border-b border-[var(--color-rule-soft)] px-3.5 py-2.5">
              <ViewingFields slot={slot} onChange={setSlot} disabled={create.isPending} />
            </div>
          )}
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
            placeholder={
              slot
                ? 'Co na prohlídce ověřit, kontakt na makléře… (nepovinné)'
                : 'Napište poznámku… soubory sem přetáhněte nebo vložte'
            }
            rows={expanded ? 3 : 1}
            maxLength={BODY_MAX}
            className="block w-full resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed text-[var(--color-ink)] placeholder:text-[var(--color-ink-3)]"
          />
          {expanded && (
            <div className="border-t border-[var(--color-rule-soft)] px-2 py-2">
              <div className="px-1.5">
                <StagedFiles
                  files={staged}
                  onRemove={(file) => setStaged((prev) => prev.filter((f) => f !== file))}
                  disabled={create.isPending}
                />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1">
                  <AttachButton
                    onFiles={stage}
                    label="Přiložit soubory"
                    text="Přiložit"
                    showText
                    disabled={create.isPending || staged.length >= NOTE_ATTACHMENT_MAX_FILES}
                  />
                  <ViewingToggle slot={slot} onChange={setSlot} disabled={create.isPending} className={QUIET} />
                  {canShare && (
                    <label className="flex cursor-pointer select-none items-center gap-2 px-2 text-[0.8rem] text-[var(--color-ink-2)]">
                      <input
                        type="checkbox"
                        checked={shareNew}
                        onChange={(e) => setShareNew(e.target.checked)}
                        disabled={create.isPending}
                        className="accent-[var(--color-copper)]"
                      />
                      <span>{WORDS.shareWith(audience.join(', '))}</span>
                    </label>
                  )}
                  {audienceFailed && (
                    <ReadFailedMark what="Sdílení" variant="header" onRetry={() => void membership.query.refetch()} />
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {body.length > BODY_MAX - 400 && (
                    <span className="font-mono text-[0.72rem] tabular-nums text-[var(--color-ink-3)]">
                      {body.length} / {BODY_MAX}
                    </span>
                  )}
                  {!alwaysOpen && (
                    <button
                      type="button"
                      onClick={() => {
                        setBody('');
                        setSlot(null);
                        setStaged([]);
                        setShareNew(false);
                        setError(null);
                        setOpened(false);
                      }}
                      disabled={create.isPending}
                      className={QUIET}
                    >
                      Zrušit
                    </button>
                  )}
                  <button type="submit" disabled={!canSave} className={PRIMARY}>
                    {create.isPending
                      ? progress
                        ? `Nahrávám ${progress.done + 1} / ${progress.total}…`
                        : 'Ukládám…'
                      : slot
                        ? 'Uložit prohlídku'
                        : 'Uložit poznámku'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </form>
        {error && <p className="text-[0.75rem] text-[var(--color-brick)]">{error}</p>}
        <FileProblems problems={problems} onDismiss={() => setProblems([])} dismissLabel="Zavřít" />

        {failed ? (
          <div>
            <ErrorBanner title="Poznámky" message="se nepodařilo načíst." onRetry={() => void notesQ.refetch()} />
          </div>
        ) : notesQ.isLoading ? (
          <p className="text-sm text-[var(--color-ink-3)]">Načítám…</p>
        ) : notes.length === 0 ? null : (
          <>
            <ul className="space-y-2">
              {shown.map((n) => (
                <li key={n.id}>
                  <NoteRow
                    property_id={property_id}
                    note={n}
                    audience={audience}
                    onSave={(patch) =>
                      updatePropertyNote(property_id, n.id, patch, ...actOpts(actAs)).then(refreshNotes)
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
            {notes.length > NOTES_SHOWN && (
              <button type="button" onClick={() => setShowAll((v) => !v)} className={QUIET}>
                {showAll ? 'Zobrazit méně' : `Zobrazit všechny poznámky (${notes.length})`}
              </button>
            )}
          </>
        )}
      </Panel>
      {composerDrop.over && <DropHint label="Pusťte soubory — přiloží se k nové poznámce" />}
    </div>
  );
}

function NoteRow({
  property_id,
  note,
  audience,
  onSave,
  onToggleShared,
  onDelete,
  onFilesChanged,
}: {
  property_id: number;
  note: Note;
  audience: readonly string[] | null;
  onSave: (patch: NotePatch) => Promise<unknown>;
  onToggleShared: () => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  onFilesChanged: () => Promise<unknown>;
}) {
  if (!isMine(note)) {
    return <ForeignNoteRow property_id={property_id} note={note} />;
  }
  return (
    <OwnNoteRow
      property_id={property_id}
      note={note}
      audience={audience}
      onSave={onSave}
      onToggleShared={onToggleShared}
      onDelete={onDelete}
      onFilesChanged={onFilesChanged}
    />
  );
}

const CARD = 'relative rounded-[var(--radius-md)] border px-3.5 py-3';

/* What the editor sends: only what changed (viewing_at null drops the date). */
type NotePatch = { body?: string; viewing_at?: string | null };

const sameInstant = (a: string | null, b: string | null): boolean =>
  a == null || b == null ? a === b : new Date(a).getTime() === new Date(b).getTime();

const noteLabel = (prefix: string, note: Note): string =>
  note.viewing_at ? `Prohlídka ${viewingWhen(note.viewing_at)}` : `${prefix} ${fmtAbsolute(note.created_at)}`;

/* Another author's SHARED note (they marked it; a share put it in reach): read
 * only — no attach, edit, delete or drop zone, files open but carry no ×. A
 * neutral card one step down: it is not mine to work. */
function ForeignNoteRow({ property_id, note }: { property_id: number; note: Note }) {
  return (
    <article
      className={`${CARD} border-[var(--color-rule)] bg-[var(--color-paper)]`}
      aria-label={`${WORDS.noteBy} ${note.owner_email} · ${fmtAbsolute(note.created_at)}`}
    >
      <NoteText note={note} />
      <NoteAttachmentList propertyId={property_id} attachments={note.attachments ?? []} uploading={[]} lang="cs" />
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <NoteMeta note={note} author={note.owner_email} />
        <span className="text-[0.72rem] text-[var(--color-ink-3)]">· jen ke čtení</span>
      </div>
    </article>
  );
}

/* A note's text, under its viewing's leaf when it is one; a viewing may have none. */
function NoteText({ note }: { note: Note }) {
  return (
    <>
      {note.viewing_at && <ViewingHead iso={note.viewing_at} />}
      {note.body && (
        <p
          className={`${note.viewing_at ? 'mt-2.5 ' : ''}whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--color-ink)]`}
        >
          {note.body}
        </p>
      )}
    </>
  );
}

/* A note's time line: relative, the exact dates on hover, the author first on
 * another author's note; then its delegated stamp, if any. */
function NoteMeta({ note, author }: { note: Note; author?: string | null }) {
  return (
    <>
      <p
        className="cursor-help text-[0.75rem] text-[var(--color-ink-3)]"
        title={
          note.updated_at
            ? `Napsáno ${fmtAbsolute(note.created_at)} · upraveno ${fmtAbsolute(note.updated_at)}`
            : fmtAbsolute(note.created_at)
        }
      >
        {author != null && (
          <>
            <span className="text-[var(--color-ink-2)]">{author}</span> ·{' '}
          </>
        )}
        {fmtRelative(note.created_at)}
        {note.updated_at && ` (${WORDS.edited})`}
      </p>
      {/* Who last wrote it acting as the owner (ruling 5); nothing for the owner's own edits. */}
      <DelegatedStamp stamp={note} lang="cs" />
    </>
  );
}

function OwnNoteRow({
  property_id,
  note,
  audience,
  onSave,
  onToggleShared,
  onDelete,
  onFilesChanged,
}: {
  property_id: number;
  note: Note;
  audience: readonly string[] | null;
  onSave: (patch: NotePatch) => Promise<unknown>;
  onToggleShared: () => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  onFilesChanged: () => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const [draftSlot, setDraftSlot] = useState<ViewingSlot | null>(null);
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
        failed.push(`${file.name}: ${(err as Error).message || 'nahrání selhalo'}`);
      }
    }
    await onFilesChanged();
    setUploading((prev) => prev.filter((f) => !ok.includes(f)));
    if (failed.length > 0) setFileProblems((prev) => [...prev, ...failed]);
  };
  const drop = useFileDrop((files) => void attach(files));

  const trimmed = draft.trim();
  const openEditor = () => {
    setDraft(note.body);
    setDraftSlot(note.viewing_at ? viewingSlot(note.viewing_at) : null);
    setEditing(true);
  };
  const closeEditor = () => {
    setEditing(false);
    setDraft(note.body);
    setError(null);
  };
  /* Only what changed; a viewing may lose its text, a plain note never. */
  const canSaveEdit = draftSlot ? slotIsComplete(draftSlot) : trimmed.length > 0;
  const save = () => {
    const patch: NotePatch = {};
    if (trimmed !== note.body) patch.body = trimmed;
    const at = draftSlot ? slotInstant(draftSlot) : null;
    if (!sameInstant(at, note.viewing_at ?? null)) patch.viewing_at = at;
    if (Object.keys(patch).length === 0) {
      closeEditor();
      return;
    }
    setBusy(true);
    setError(null);
    onSave(patch)
      .then(() => setEditing(false))
      .catch((err: ApiError | Error) => setError(err.message || 'Poznámku se nepodařilo uložit'))
      .finally(() => setBusy(false));
  };
  const ahead = note.viewing_at != null && isUpcoming(note.viewing_at);
  const deleteQuestion = note.viewing_at ? 'Smazat prohlídku?' : 'Smazat poznámku?';
  const files = (
    <>
      <NoteAttachmentList
        propertyId={property_id}
        attachments={attachments}
        uploading={uploading}
        onRemoved={onFilesChanged}
        lang="cs"
      />
      <FileProblems problems={fileProblems} onDismiss={() => setFileProblems([])} dismissLabel="Zavřít" />
    </>
  );

  return (
    <article
      className={`${CARD} ${editing ? 'border-[var(--color-copper)]/50' : ahead ? 'border-[var(--color-copper)]/35' : 'border-[var(--color-rule)]'} bg-[var(--color-paper-3)]`}
      aria-label={noteLabel(WORDS.noteFrom, note)}
      {...drop.handlers}
    >
      {editing ? (
        <>
          {draftSlot && (
            <div className="mb-2">
              <ViewingFields slot={draftSlot} onChange={setDraftSlot} disabled={busy} />
            </div>
          )}
          <textarea
            aria-label="Upravit poznámku"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            maxLength={BODY_MAX}
            autoFocus
            className="block w-full resize-y rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-sm leading-relaxed text-[var(--color-ink)] focus:border-[var(--color-copper)]"
          />
          {files}
          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[0.72rem] tabular-nums text-[var(--color-ink-3)]">
                {draft.length} / {BODY_MAX}
              </span>
              {/* Dropping the date of a viewing with no text would leave nothing: delete it instead. */}
              <ViewingToggle
                slot={draftSlot}
                onChange={setDraftSlot}
                disabled={busy || (draftSlot != null && trimmed.length === 0)}
                className={QUIET}
              />
            </div>
            <div className="flex items-center gap-1.5">
              <button type="button" onClick={closeEditor} disabled={busy} className={QUIET}>
                Zrušit
              </button>
              <button type="button" onClick={save} disabled={busy || !canSaveEdit} className={PRIMARY}>
                {busy ? 'Ukládám…' : 'Uložit'}
              </button>
            </div>
          </div>
          {error && <p className="mt-1.5 text-[0.75rem] text-[var(--color-brick)]">{error}</p>}
        </>
      ) : (
        <>
          <NoteText note={note} />
          {files}
          {confirmingDelete ? (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] px-2.5 py-1.5">
              <span className="text-[0.8rem] text-[var(--color-brick)]">
                {attachments.length > 0
                  ? `${deleteQuestion} ${attachments.length === 1 ? 'Její soubor zůstane' : `Jejích ${attachments.length} souborů zůstane`} u nemovitosti.`
                  : deleteQuestion}
              </span>
              <button
                type="button"
                onClick={() => {
                  setBusy(true);
                  onDelete().catch((err: ApiError | Error) => {
                    setError(err.message || 'Poznámku se nepodařilo smazat');
                    setBusy(false);
                    setConfirmingDelete(false);
                  });
                }}
                disabled={busy}
                className="px-2.5 py-1 text-[0.8rem] font-medium rounded-[var(--radius-sm)] bg-[var(--color-brick)] text-[var(--color-on-brick)] hover:opacity-90 disabled:opacity-50"
              >
                {busy ? 'Mažu…' : 'Smazat'}
              </button>
              <button type="button" onClick={() => setConfirmingDelete(false)} disabled={busy} className={QUIET}>
                Zrušit
              </button>
            </div>
          ) : (
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <NoteMeta note={note} />
                {note.shared && (
                  <span className="rounded-[var(--radius-xs)] bg-[var(--color-copper-soft)] px-1.5 py-0.5 text-[0.68rem] uppercase tracking-[0.1em] text-[var(--color-copper)]">
                    {audience != null && audience.length === 0 ? WORDS.sharedNobody : WORDS.shared}
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
                        .catch((err: ApiError | Error) => setError(err.message || 'Poznámku se nepodařilo upravit'))
                        .finally(() => setBusy(false));
                    }}
                    disabled={busy}
                    className="text-[0.75rem] text-[var(--color-ink-3)] underline-offset-2 hover:text-[var(--color-ink)] hover:underline disabled:opacity-50"
                  >
                    {note.shared ? WORDS.unshare : WORDS.shareWith((audience ?? []).join(', '))}
                  </button>
                )}
              </div>
              <div className="flex items-center">
                <AttachButton
                  onFiles={(picked) => void attach(picked)}
                  label="Přiložit soubory k poznámce"
                  text="Přiložit"
                  showText
                />
                <button
                  type="button"
                  onClick={openEditor}
                  aria-label="Upravit poznámku"
                  className={QUIET}
                >
                  <PencilIcon className="h-3.5 w-3.5" />
                  <span aria-hidden>Upravit</span>
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(true)}
                  aria-label="Smazat poznámku"
                  className={`${QUIET} hover:text-[var(--color-brick)]`}
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                  <span aria-hidden>Smazat</span>
                </button>
              </div>
            </div>
          )}
          {error && !confirmingDelete && <p className="mt-1.5 text-[0.75rem] text-[var(--color-brick)]">{error}</p>}
        </>
      )}
      {drop.over && <DropHint label="Pusťte soubory — přiloží se k této poznámce" />}
    </article>
  );
}
