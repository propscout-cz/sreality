/* A user's own photos, opened (migration 660: one attachment store): the shared full-screen viewer
 * on each ORIGINAL (EXIF intact, fetched through the API as the strip's 1080 px tiles are), with
 * Share and Delete under the photo. One viewer for "Moje fotky" and a note's photos — the same
 * records. The words follow the page (NOTE_WORDS' rule: English on the desktop property page,
 * Czech on the phone). */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ImageLightbox from '@/components/ImageLightbox';
import { TwoStep } from '@/components/TwoStep';
import { useActAs } from '@/lib/acting';
import { deleteAttachment, fetchAttachmentBlob } from '@/lib/api';
import { czPlural } from '@/lib/format';
import { canShareFiles, shareFiles } from '@/lib/shareFiles';
import { bareImage, type Attachment } from '@/lib/types';

export const PHOTO_WORDS = {
  en: {
    title: 'My photos',
    empty: 'No photos of your own yet',
    camera: 'Take photo',
    library: 'From library',
    upload: 'Upload',
    select: 'Select',
    done: 'Done',
    share: (n: number) => (n > 1 ? `Share (${n})` : 'Share'),
    save: (n: number) => (n > 1 ? `Download (${n})` : 'Download'),
    preparing: 'Preparing…',
    remove: (n: number) => (n > 1 ? `Delete (${n})` : 'Delete'),
    confirmRemove: (n: number, inNote: boolean) =>
      `Delete ${n > 1 ? `${n} photos` : 'photo'}${inNote ? ', from its note too' : ''}`,
    cancel: 'Cancel',
    dismiss: 'Dismiss',
    uploading: (i: number, n: number) => `Uploading ${i} / ${n}…`,
    notPhoto: (name: string) => `${name}: not a photo (JPEG, PNG or WebP)`,
    drop: 'Drop photos to add them',
    orDrop: 'or drop them here',
    open: (name: string) => `Open ${name}`,
    pick: (name: string) => `Select ${name}`,
    picked: (n: number) => `${n} selected`,
    unavailable: 'Photo unavailable',
    readFailed: 'could not be loaded.',
    shareTitle: (id: number) => `Property #${id}`,
    shareFailed: 'Sharing failed',
    retry: 'Retry',
    removeFailed: 'Deleting failed',
  },
  cs: {
    title: 'Moje fotky',
    empty: 'Zatím žádné vlastní fotky',
    camera: 'Vyfotit',
    library: 'Z knihovny',
    upload: 'Nahrát',
    select: 'Vybrat',
    done: 'Hotovo',
    share: (n: number) => (n > 1 ? `Sdílet (${n})` : 'Sdílet'),
    save: (n: number) => (n > 1 ? `Stáhnout (${n})` : 'Stáhnout'),
    preparing: 'Připravuji…',
    remove: (n: number) => (n > 1 ? `Smazat (${n})` : 'Smazat'),
    confirmRemove: (n: number, inNote: boolean) =>
      `Smazat ${n > 1 ? `${n} ${czPlural(n, 'fotku', 'fotky', 'fotek')}` : 'fotku'}${inNote ? ' i z poznámky' : ''}`,
    cancel: 'Zrušit',
    dismiss: 'Zavřít',
    uploading: (i: number, n: number) => `Nahrávám ${i} / ${n}…`,
    notPhoto: (name: string) => `${name}: není fotka (JPEG, PNG nebo WebP)`,
    drop: 'Pusťte fotky sem',
    orDrop: 'nebo je sem přetáhněte',
    open: (name: string) => `Otevřít ${name}`,
    pick: (name: string) => `Vybrat ${name}`,
    picked: (n: number) => `Vybráno ${n}`,
    unavailable: 'Fotka nedostupná',
    readFailed: 'se nepodařilo načíst.',
    shareTitle: (id: number) => `Nemovitost #${id}`,
    shareFailed: 'Sdílení se nezdařilo',
    retry: 'Zkusit znovu',
    removeFailed: 'Smazání se nezdařilo',
  },
} as const;
export type PhotoLang = keyof typeof PHOTO_WORDS;
export type PhotoWords = (typeof PHOTO_WORDS)[PhotoLang];

type Original = { file: File; url: string } | 'failed';

/* The originals' bytes as named files, fetched on demand and held while mounted (share needs them
 * in hand before the tap; the viewer shows them). A failed fetch is asked again on the next want. */
export function useOriginals(propertyId: number, actAs: string | null) {
  const [got, setGot] = useState<ReadonlyMap<number, Original>>(() => new Map());
  const asked = useRef(new Set<number>());
  const urls = useRef<string[]>([]);
  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);
  const want = useCallback(
    (list: readonly Attachment[]) => {
      for (const a of list) {
        if (asked.current.has(a.id)) continue;
        asked.current.add(a.id);
        fetchAttachmentBlob(propertyId, a.id, { actAs })
          .then((blob) => {
            const file = new File([blob], a.filename, { type: a.mime_type });
            const url = URL.createObjectURL(file);
            urls.current.push(url);
            setGot((m) => new Map(m).set(a.id, { file, url }));
          })
          .catch(() => {
            asked.current.delete(a.id);
            setGot((m) => new Map(m).set(a.id, 'failed'));
          });
      }
    },
    [propertyId, actAs],
  );
  return { got, want };
}

/* The files of `list` once every one is in hand, else null. */
export function filesOf(got: ReadonlyMap<number, Original>, list: readonly Attachment[]): File[] | null {
  const files: File[] = [];
  for (const a of list) {
    const o = got.get(a.id);
    if (o == null || o === 'failed') return null;
    files.push(o.file);
  }
  return files;
}

export default function AttachmentViewer({
  propertyId,
  photos,
  startIndex,
  onClose,
  onRemoved,
  lang,
}: {
  propertyId: number;
  photos: readonly Attachment[];
  startIndex: number;
  onClose: () => void;
  /* Absent = read only (another author's shared note): Share, no Delete. */
  onRemoved?: () => Promise<unknown>;
  lang: PhotoLang;
}) {
  const actAs = useActAs();
  const { got, want } = useOriginals(propertyId, actAs);
  const images = useMemo(
    () =>
      photos.map((a) => {
        const o = got.get(a.id);
        return bareImage(a.id, null, o != null && o !== 'failed' ? o.url : '');
      }),
    [photos, got],
  );
  // The last photo deleted: nothing left to show.
  useEffect(() => {
    if (photos.length === 0) onClose();
  }, [photos.length, onClose]);

  return (
    <ImageLightbox
      images={images}
      startIndex={startIndex}
      onClose={onClose}
      actionsAt={(i) =>
        photos[i] && (
          <ViewerActions
            key={photos[i].id}
            propertyId={propertyId}
            photo={photos[i]}
            original={got.get(photos[i].id)}
            want={want}
            onRemoved={onRemoved}
            words={PHOTO_WORDS[lang]}
          />
        )
      }
    />
  );
}

function ViewerActions({
  propertyId,
  photo,
  original,
  want,
  onRemoved,
  words,
}: {
  propertyId: number;
  photo: Attachment;
  original: Original | undefined;
  want: (list: readonly Attachment[]) => void;
  onRemoved?: () => Promise<unknown>;
  words: PhotoWords;
}) {
  const actAs = useActAs();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => want([photo]), [want, photo]);

  const ready = original != null && original !== 'failed' ? original.file : null;
  const share = () => {
    if (!ready) return;
    setProblem(null);
    shareFiles([ready], words.shareTitle(propertyId)).catch(() => setProblem(words.shareFailed));
  };
  const remove = () => {
    setBusy(true);
    setProblem(null);
    deleteAttachment(propertyId, photo.id, { actAs })
      .then(() => onRemoved?.())
      .catch(() => setProblem(words.removeFailed))
      .finally(() => {
        setBusy(false);
        setConfirming(false);
      });
  };
  const button =
    'inline-flex h-10 items-center rounded-[var(--radius-sm)] border border-white/30 px-4 text-[0.85rem] text-white/90 hover:bg-white/10 disabled:opacity-50';

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex flex-wrap items-center justify-center gap-3">
        {original === 'failed' ? (
          <button type="button" onClick={() => want([photo])} className={button}>
            {words.retry}
          </button>
        ) : (
          <button type="button" onClick={share} disabled={!ready} className={button}>
            {!ready ? words.preparing : canShareFiles() ? words.share(1) : words.save(1)}
          </button>
        )}
        {onRemoved && (
          <TwoStep
            idle={words.remove(1)}
            confirm={words.confirmRemove(1, photo.note_id != null)}
            cancel={words.cancel}
            confirming={confirming}
            setConfirming={setConfirming}
            pending={busy}
            onConfirm={remove}
            touch
            tone="onDark"
          />
        )}
      </div>
      {problem && (
        <p role="alert" className="text-[0.8rem] text-white/80">
          {problem}
        </p>
      )}
    </div>
  );
}
