/* How a property's photos open: the app's one lightbox on the photo tapped, dimmed
 * like the tiles while the property is off the market. The header mosaic, the Fotky
 * tab and the phone swiper all open through this, so a tap means the same thing on
 * every surface (operator 2026-11-06: the mosaic's "Všech N fotek" tile too).
 *
 * The lightbox is a static import on purpose: each of the three surfaces is a lazy
 * chunk already, so the viewer arrives with the photos and the first tap opens at
 * once. Lazy-loading it a second time made that tap suspend the nearest boundary,
 * which on the desktop is the mosaic itself: the photos blanked until the chunk came. */

import { useState, type ReactNode } from 'react';

import ImageLightbox from '@/components/ImageLightbox';
import type { ImagePublic } from '@/lib/types';

/* An off-market property's photos read as such, on the tile and in the viewer. */
export const OFF_MARKET_DIM = 'opacity-70';

export function usePhotoViewer(
  images: ImagePublic[],
  isActive: boolean,
): { open: (index: number) => void; viewer: ReactNode } {
  const [openAt, setOpenAt] = useState<number | null>(null);
  return {
    open: setOpenAt,
    viewer:
      openAt != null ? (
        <ImageLightbox
          images={images}
          startIndex={openAt}
          onClose={() => setOpenAt(null)}
          dim={isActive ? '' : OFF_MARKET_DIM}
        />
      ) : null,
  };
}
