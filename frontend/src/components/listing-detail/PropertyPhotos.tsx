/* "Moje fotky" — the acting identity's own photos of a property (migration 660: one attachment
 * store, so a note's photos are the same records and show here and under their note). One
 * component, two mounts: the phone page's section after Poloha (Czech, with the camera) and the
 * desktop's Moje tab (the page's words, drag-and-drop), each lazy-loaded.
 *
 * A roll of 4:3 tiles, newest first, each drawn from its 1080 px display copy once it nears view
 * and dated like a field photo; a tap opens the original in the shared viewer (Share, Delete).
 * Vybrat picks several for Share (N) / Delete (N); a pick fetches its original at once, so the
 * share sheet opens inside the tap. Every control is a button a finger reaches — nothing waits
 * for a hover. Uploads run one by one ("i / n"), each shown at once from the local file. */

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type RefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import ErrorBanner from '@/components/ErrorBanner';
import Spinner from '@/components/Spinner';
import { TwoStep } from '@/components/TwoStep';
import { PaperclipIcon } from '@/components/icons';
import { SectionLabel } from '@/components/section';
import { readFailed } from '@/components/ReadFailedMark';
import { PHOTO_DARK } from '@/components/phone/CardPhotoStrip';
import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { deleteAttachment, fetchAttachmentBlob, listAttachments, uploadAttachment } from '@/lib/api';
import { attachmentProblem, isPreviewableImage } from '@/lib/attachments';
import { fmtShortDate } from '@/lib/format';
import { curationKeys } from '@/lib/queries';
import { canShareFiles, shareFiles } from '@/lib/shareFiles';
import type { Attachment } from '@/lib/types';
import { useBlobUrl } from '@/lib/useBlobUrl';
import AttachmentViewer, { filesOf, PHOTO_WORDS, useOriginals, type PhotoLang, type PhotoWords } from './AttachmentViewer';
import { DropHint, FileProblems, useFileDrop, useStrayFileDropGuard } from './NoteAttachments';

/* What the camera and the picker offer; the API types an image by its own bytes. */
export const PHOTO_ACCEPT = 'image/jpeg,image/png,image/webp';
const PHOTO_TYPES = PHOTO_ACCEPT.split(',');

const TILE = 'relative block aspect-[4/3] w-40 overflow-hidden rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] sm:w-48';
const BUTTON = 'inline-flex h-10 cursor-pointer items-center rounded-[var(--radius-sm)] px-3.5 text-[0.85rem] transition-colors focus-within:outline focus-within:outline-2 focus-within:outline-[var(--color-copper)]';
const PRIMARY = `${BUTTON} bg-[var(--color-copper)] text-[var(--color-paper)] hover:bg-[var(--color-copper-2)]`;
const OUTLINED = `${BUTTON} border border-[var(--color-rule-strong)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)]`;
const QUIET = 'inline-flex h-10 items-center px-2 text-[0.85rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] disabled:opacity-40';

/* iOS names every camera capture "image.jpg"; a dated name tells them apart in a split's choice,
 * a share and the zip. */
function dated(file: File, now = new Date()): File {
  const p = (n: number) => String(n).padStart(2, '0');
  const ext = file.type === 'image/png' ? '.png' : file.type === 'image/webp' ? '.webp' : '.jpg';
  const stamp = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  return new File([file], `foto-${stamp}${ext}`, { type: file.type, lastModified: file.lastModified });
}

export default function PropertyPhotos({
  property_id,
  lang = 'en',
  camera = false,
}: {
  property_id: number;
  lang?: PhotoLang;
  /* The phone: "Vyfotit" opens the camera, the picker reads "Z knihovny". */
  camera?: boolean;
}) {
  const words = PHOTO_WORDS[lang];
  const qc = useQueryClient();
  const actAs = useActAs();
  /* Under the notes' key: a note's write re-reads the photos, and a photo's write the notes. */
  const notesKey = actingKey(actAs, curationKeys.propertyNotes(property_id));
  const filesQ = useQuery({
    queryKey: actingKey(actAs, curationKeys.propertyFiles(property_id)),
    queryFn: () => listAttachments(property_id, ...actOpts(actAs)),
    staleTime: 30_000,
  });
  const photos = useMemo(
    () =>
      (filesQ.data?.data ?? [])
        .filter((a) => a.owner_email == null && isPreviewableImage(a.mime_type))
        .reverse(),
    [filesQ.data],
  );

  const [pending, setPending] = useState<File[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [viewing, setViewing] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<ReadonlySet<number>>(() => new Set());
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const originals = useOriginals(property_id, actAs);
  const roll = useRef<HTMLUListElement>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: notesKey });

  const add = async (files: File[]) => {
    const ok: File[] = [];
    const refused: string[] = [];
    for (const file of files) {
      const problem = PHOTO_TYPES.includes(file.type) ? attachmentProblem(file) : words.notPhoto(file.name);
      if (problem) refused.push(problem);
      else ok.push(file);
    }
    setProblems(refused);
    if (ok.length === 0) return;
    setPending(ok);
    const failed: string[] = [];
    for (const [i, file] of ok.entries()) {
      setProgress({ done: i, total: ok.length });
      try {
        await uploadAttachment(property_id, file, { actAs });
      } catch (err) {
        failed.push(`${file.name}: ${(err as Error).message || 'upload failed'}`);
      }
    }
    await refresh();
    setPending([]);
    setProgress(null);
    if (failed.length > 0) setProblems((prev) => [...prev, ...failed]);
  };
  const drop = useFileDrop((files) => void add(files));
  useStrayFileDropGuard();

  const chosen = photos.filter((a) => picked.has(a.id));
  const files = filesOf(originals.got, chosen);
  const fetchFailed = chosen.some((a) => originals.got.get(a.id) === 'failed');
  const toggle = (a: Attachment) => {
    const next = new Set(picked);
    if (next.has(a.id)) next.delete(a.id);
    else {
      next.add(a.id);
      originals.want([a]);
    }
    setPicked(next);
    setConfirming(false);
  };
  const stopPicking = () => {
    setPicking(false);
    setPicked(new Set());
    setConfirming(false);
  };
  const removeChosen = async () => {
    setRemoving(true);
    let failed = 0;
    for (const a of chosen) {
      try {
        await deleteAttachment(property_id, a.id, { actAs });
      } catch {
        failed += 1;
      }
    }
    await refresh();
    setRemoving(false);
    stopPicking();
    if (failed > 0) setProblems([words.removeFailed]);
  };

  const busy = progress != null;
  const pick = (input: ChangeEvent<HTMLInputElement>, rename: boolean) => {
    const list = Array.from(input.target.files ?? []);
    input.target.value = ''; // the same photo may be picked again
    if (list.length > 0) void add(rename ? list.map((f) => dated(f)) : list);
  };

  return (
    <section aria-label={words.title} className="relative" {...drop.handlers}>
      <div className="flex items-center justify-between gap-3">
        <SectionLabel>
          {words.title}
          {filesQ.isSuccess && (
            <span className="ml-2 font-mono tabular-nums tracking-normal text-[var(--color-ink-4)]">
              ({photos.length})
            </span>
          )}
        </SectionLabel>
        {picking ? (
          <button type="button" onClick={stopPicking} className={QUIET}>
            {words.done}
          </button>
        ) : (
          photos.length > 0 &&
          !busy && (
            <button type="button" onClick={() => setPicking(true)} className={QUIET}>
              {words.select}
            </button>
          )
        )}
      </div>
      {!picking && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {busy ? (
            <span role="status" className="inline-flex h-10 items-center gap-2 font-mono text-[0.8rem] tabular-nums text-[var(--color-ink-2)]">
              <Spinner size={13} />
              {words.uploading(progress.done + 1, progress.total)}
            </span>
          ) : (
            <>
              {camera && (
                <label className={PRIMARY}>
                  <input
                    type="file"
                    accept={PHOTO_ACCEPT}
                    capture="environment"
                    className="sr-only"
                    onChange={(e) => pick(e, true)}
                  />
                  {words.camera}
                </label>
              )}
              <label className={OUTLINED}>
                <input
                  type="file"
                  accept={PHOTO_ACCEPT}
                  multiple
                  className="sr-only"
                  onChange={(e) => pick(e, false)}
                />
                {camera ? words.library : words.upload}
              </label>
              {!camera && <span className="text-[0.8rem] text-[var(--color-ink-3)]">{words.orDrop}</span>}
            </>
          )}
        </div>
      )}

      <div className="mt-3">
        {readFailed(filesQ) ? (
          <ErrorBanner
            flush
            title={words.title}
            message={words.readFailed}
            retryLabel={words.retry}
            onRetry={() => void filesQ.refetch()}
          />
        ) : filesQ.isLoading ? (
          <div aria-hidden className="flex gap-2 overflow-hidden">
            {[0, 1, 2].map((i) => (
              <div key={i} className={`${TILE} shrink-0`} />
            ))}
          </div>
        ) : photos.length === 0 && pending.length === 0 ? (
          <p className="text-sm text-[var(--color-ink-3)]">{words.empty}</p>
        ) : (
          <ul
            ref={roll}
            className="-mx-1 flex snap-x gap-2 overflow-x-auto overscroll-x-contain px-1 pb-1 [scrollbar-width:thin]"
          >
            {pending.map((file) => (
              <li key={`${file.name}|${file.size}|${file.lastModified}`} className="shrink-0 snap-start">
                <PendingTile file={file} />
              </li>
            ))}
            {photos.map((a, i) => (
              <li key={a.id} className="shrink-0 snap-start">
                <PhotoTile
                  propertyId={property_id}
                  photo={a}
                  root={roll}
                  words={words}
                  picking={picking}
                  picked={picked.has(a.id)}
                  onTap={() => (picking ? toggle(a) : setViewing(i))}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {picking && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="font-mono text-[0.8rem] tabular-nums text-[var(--color-ink-3)]">
            {words.picked(chosen.length)}
          </span>
          <button
            type="button"
            disabled={chosen.length === 0 || files == null}
            onClick={() => {
              if (files == null) return;
              shareFiles(files, words.shareTitle(property_id)).catch(() => setProblems([words.shareFailed]));
            }}
            className={`${OUTLINED} disabled:pointer-events-none disabled:opacity-40`}
          >
            {chosen.length > 0 && files == null && !fetchFailed
              ? words.preparing
              : canShareFiles()
                ? words.share(chosen.length)
                : words.save(chosen.length)}
          </button>
          {chosen.length > 0 && (
            <TwoStep
              idle={words.remove(chosen.length)}
              confirm={words.confirmRemove(chosen.length, chosen.some((a) => a.note_id != null))}
              cancel={words.cancel}
              confirming={confirming}
              setConfirming={setConfirming}
              pending={removing}
              onConfirm={() => void removeChosen()}
              touch
            />
          )}
          {fetchFailed && (
            <button type="button" onClick={() => originals.want(chosen)} className="text-[0.8rem] text-[var(--color-brick)] underline underline-offset-2">
              {words.fetchFailed} · {words.retry}
            </button>
          )}
        </div>
      )}

      <FileProblems problems={problems} onDismiss={() => setProblems([])} dismissLabel={words.dismiss} />
      {drop.over && <DropHint label={words.drop} />}
      {viewing != null && (
        <AttachmentViewer
          propertyId={property_id}
          photos={photos}
          startIndex={viewing}
          onClose={() => setViewing(null)}
          onRemoved={refresh}
          lang={lang}
        />
      )}
    </section>
  );
}

/* Loads once it nears the roll's view (a roll can hold a hundred photos). */
function useNear(ref: RefObject<HTMLElement | null>, root: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setNear(true);
      },
      { root: root.current, rootMargin: '0px 100% 0px 100%' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near, ref, root]);
  return near;
}

function PhotoTile({
  propertyId,
  photo,
  root,
  words,
  picking,
  picked,
  onTap,
}: {
  propertyId: number;
  photo: Attachment;
  root: RefObject<HTMLElement | null>;
  words: PhotoWords;
  picking: boolean;
  picked: boolean;
  onTap: () => void;
}) {
  const actAs = useActAs();
  const ref = useRef<HTMLButtonElement>(null);
  const near = useNear(ref, root);
  const thumb = useBlobUrl(
    near ? () => fetchAttachmentBlob(propertyId, photo.id, { w: 1080, actAs }) : null,
    `${propertyId}:${photo.id}:${actAs ?? ''}:1080`,
  );
  return (
    <button
      ref={ref}
      type="button"
      onClick={onTap}
      aria-pressed={picking ? picked : undefined}
      aria-label={picking ? words.pick(photo.filename) : words.open(photo.filename)}
      title={photo.filename}
      className={`${TILE} text-left`}
    >
      {thumb.url ? (
        <img src={thumb.url} alt="" draggable={false} className={`block h-full w-full object-cover ${PHOTO_DARK}`} />
      ) : thumb.failed ? (
        <span className="absolute inset-0 flex items-center justify-center text-[11px] text-[var(--color-ink-4)]">
          {words.unavailable}
        </span>
      ) : null}
      <span className="absolute bottom-1.5 left-1.5 flex items-center gap-1 rounded-[var(--radius-xs)] bg-[var(--color-paper-2)]/85 px-1 font-mono text-[10.5px] tabular-nums text-[var(--color-ink-2)]">
        {photo.note_id != null && <PaperclipIcon className="h-2.5 w-2.5" />}
        {fmtShortDate(photo.created_at)}
      </span>
      {picking && (
        <>
          {picked && <span aria-hidden className="absolute inset-0 rounded-[var(--radius-sm)] border-2 border-[var(--color-copper)]" />}
          <span
            aria-hidden
            className={`absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full border text-[13px] leading-none ${
              picked
                ? 'border-[var(--color-copper)] bg-[var(--color-copper)] text-[var(--color-paper)]'
                : 'border-[var(--color-rule-strong)] bg-[var(--color-paper-2)]/85 text-transparent'
            }`}
          >
            ✓
          </span>
        </>
      )}
    </button>
  );
}

/* A photo on its way up: the local file, at once. */
function PendingTile({ file }: { file: File }) {
  const preview = useBlobUrl(() => Promise.resolve(file), `${file.name}|${file.size}|${file.lastModified}`);
  return (
    <div aria-busy className={TILE}>
      {preview.url && <img src={preview.url} alt="" className="block h-full w-full object-cover opacity-60" />}
      <span className="absolute inset-0 flex items-center justify-center text-[var(--color-ink)]">
        <Spinner size={18} />
      </span>
    </div>
  );
}
