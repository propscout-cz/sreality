/* The property page's photo swiper on the phone (#1767, the prototype's
 * "Nemovitost"): the canonical ad's photos as full-width 4:3 slides — the 1080 px
 * copies (#1765) — snapped one per swipe, dots and "N fotek" under them. The
 * photos show bare, no stage wash: here the stage is the curation row's funnel
 * cell (operator ruling 2026-10-22). A tap opens the app's lightbox on that photo
 * at full size. */

import { Suspense, useRef, useState } from 'react';

import { czPlural } from '@/lib/format';
import { imageSrc } from '@/lib/imageUrl';
import { lazyChunk } from '@/lib/lazyChunk';
import type { ImagePublic } from '@/lib/types';

import { PHOTO_DARK } from './CardPhotoStrip';

const ImageLightbox = lazyChunk(() => import('@/components/ImageLightbox'));

/* More dots than this stop reading as positions; a counter takes over. */
const MAX_DOTS = 12;

export const photosWord = (n: number): string => `${n} ${czPlural(n, 'fotka', 'fotky', 'fotek')}`;

export default function PropertyGallery({
  images,
  loading,
  isActive,
}: {
  images: ImagePublic[];
  loading: boolean;
  isActive: boolean;
}) {
  const [at, setAt] = useState(0);
  const [openAt, setOpenAt] = useState<number | null>(null);
  const [broken, setBroken] = useState<ReadonlySet<number>>(() => new Set());
  const raf = useRef(0);

  if (images.length === 0) {
    return loading ? (
      <div aria-hidden className="aspect-[4/3] w-full bg-[var(--color-inset)]" />
    ) : (
      <p className="px-4 pt-4 text-[13px] text-[var(--color-ink-3)]">Bez fotek</p>
    );
  }

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      if (el.clientWidth) setAt(Math.round(el.scrollLeft / el.clientWidth));
    });
  };

  return (
    <div>
      <div
        aria-label="Fotky"
        role="group"
        onScroll={onScroll}
        className="flex snap-x snap-mandatory overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {images.map((img, i) => {
          const src = imageSrc(img, { w: 1080 });
          return (
            <button
              key={img.id}
              type="button"
              onClick={() => setOpenAt(i)}
              aria-label={`Fotka ${i + 1} z ${images.length}`}
              className="relative aspect-[4/3] w-full shrink-0 snap-start overflow-hidden bg-[var(--color-inset)]"
            >
              {broken.has(img.id) ? (
                <span className="absolute inset-0 flex items-center justify-center text-[12px] text-[var(--color-ink-4)]">
                  Fotka nedostupná
                </span>
              ) : (
                <img
                  src={src}
                  alt=""
                  loading={i === 0 ? 'eager' : 'lazy'}
                  decoding="async"
                  onError={() => setBroken((prev) => new Set(prev).add(img.id))}
                  className={`block h-full w-full object-cover ${isActive ? '' : 'opacity-70'} ${PHOTO_DARK}`}
                />
              )}
            </button>
          );
        })}
      </div>
      <div className="relative flex h-6 items-center justify-center gap-1.5" aria-hidden>
        {images.length <= MAX_DOTS ? (
          images.map((img, i) => (
            <i
              key={img.id}
              className={`h-1.5 w-1.5 rounded-full transition-colors ${
                i === at ? 'bg-[var(--color-ink)]' : 'bg-[var(--color-ink-4)]'
              }`}
            />
          ))
        ) : (
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-ink-2)]">
            {Math.min(at + 1, images.length)} / {images.length}
          </span>
        )}
        <span className="absolute right-4 font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
          {photosWord(images.length)}
        </span>
      </div>
      {openAt != null && (
        <Suspense fallback={null}>
          <ImageLightbox
            images={images}
            startIndex={openAt}
            onClose={() => setOpenAt(null)}
            dim={isActive ? '' : 'opacity-70'}
          />
        </Suspense>
      )}
    </div>
  );
}
