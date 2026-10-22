/* The property page's photos at the top: the canonical advert's first photo large
 * and the next four beside it, any of them opening the app's one lightbox. When
 * there are more, the last tile says how many and opens the Fotky tab instead. */
import { useState } from 'react';
import type { ImagePublic } from '@/lib/types';
import { imageSrc } from '@/lib/imageUrl';
import { lazyChunk } from '@/lib/lazyChunk';
import ImageTagBadge from '@/components/ImageTagBadge';

const ImageLightbox = lazyChunk(() => import('@/components/ImageLightbox'));

const SHOWN = 5;
const HEIGHT = 'h-[260px] sm:h-[360px] lg:h-[430px]';

/* Tile spans by how many photos there are, so a short set still fills the frame. */
const LAYOUT: Record<number, string[]> = {
  1: ['col-span-4 row-span-2'],
  2: ['col-span-2 row-span-2', 'col-span-2 row-span-2'],
  3: ['col-span-2 row-span-2', 'col-span-2', 'col-span-2'],
  4: ['col-span-2 row-span-2', 'col-span-2', '', ''],
  5: ['col-span-2 row-span-2', '', '', '', ''],
};

export default function PhotoMosaic({
  images,
  loading,
  isActive,
  onAll,
}: {
  images: ImagePublic[];
  loading: boolean;
  isActive: boolean;
  /* Opens every photo (the Fotky tab). */
  onAll: () => void;
}) {
  const [openAt, setOpenAt] = useState<number | null>(null);

  if (loading && images.length === 0) {
    return (
      <div className={`grid grid-cols-4 grid-rows-2 gap-1.5 ${HEIGHT}`} aria-hidden>
        {LAYOUT[5].map((span, i) => (
          <div key={i} className={`${span} rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]`} />
        ))}
      </div>
    );
  }
  if (images.length === 0) {
    return (
      <div className={`${HEIGHT} flex items-center justify-center rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] text-sm text-[var(--color-ink-3)]`}>
        Žádné fotky
      </div>
    );
  }

  const shown = images.slice(0, SHOWN);
  const spans = LAYOUT[shown.length];
  const more = images.length - shown.length;
  return (
    <>
      <ul aria-label="Fotky" className={`grid grid-cols-4 grid-rows-2 gap-1.5 ${HEIGHT}`}>
        {shown.map((img, i) => {
          const last = i === shown.length - 1 && more > 0;
          return (
            <li key={img.id} className={`${spans[i]} min-h-0`}>
              <Tile
                image={img}
                dim={!isActive}
                onClick={last ? onAll : () => setOpenAt(i)}
                label={last ? `Všech ${images.length} fotek` : `Fotka ${i + 1} z ${images.length}`}
                overlay={last ? `Všech ${images.length} fotek` : null}
              />
            </li>
          );
        })}
      </ul>
      {openAt != null && (
        <ImageLightbox
          images={images}
          startIndex={openAt}
          onClose={() => setOpenAt(null)}
          dim={isActive ? '' : 'opacity-70'}
        />
      )}
    </>
  );
}

function Tile({
  image,
  dim,
  onClick,
  label,
  overlay,
}: {
  image: ImagePublic;
  dim: boolean;
  onClick: () => void;
  label: string;
  overlay: string | null;
}) {
  const [errored, setErrored] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="group relative block h-full w-full cursor-zoom-in overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-inset)] focus-visible:border-[var(--color-copper)]"
    >
      {errored ? (
        <span className="flex h-full w-full items-center justify-center text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-3)]">
          Nedostupné
        </span>
      ) : (
        <>
          <img
            src={imageSrc(image, { w: 1080 })}
            alt=""
            decoding="async"
            onError={() => setErrored(true)}
            className={`h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02] ${dim ? 'opacity-70' : ''}`}
          />
          {overlay == null && (
            <ImageTagBadge
              tag={image.clip_fine_tag}
              confidence={image.clip_confidence}
              className="absolute bottom-1.5 left-1.5 max-w-[calc(100%-0.75rem)] truncate"
            />
          )}
        </>
      )}
      {overlay != null && (
        <span className="absolute inset-0 flex items-center justify-center bg-black/45 text-sm font-medium text-white">
          {overlay}
        </span>
      )}
    </button>
  );
}
