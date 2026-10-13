/* The phone card's photo strip as a projection of the card's photo read: the
 * cover first (chosen over the WHOLE set, as the Browse card chooses it), then
 * the rest in position order, at most eight, and the ad's full count beside. */
import { describe, expect, it } from 'vitest';

import type { ImagePublic } from '@/lib/types';

import { CARD_STRIP_CAP, cardPhotos } from './cardPhotos';

const photo = (sequence: number | null, id: number, over: Partial<ImagePublic> = {}): ImagePublic => ({
  id,
  sreality_id: 1,
  sequence,
  sreality_url: `https://cdn.example/${id}.jpg`,
  storage_path: `1/${id}.jpg`,
  clip_fine_tag: null,
  clip_logical_tag: null,
  clip_confidence: null,
  clip_render_score: null,
  phash: null,
  ...over,
});

const seqs = (xs: readonly ImagePublic[]) => xs.map((p) => p.sequence);

describe('cardPhotos', () => {
  it('opens on the first photo by position when no cover is asked for', () => {
    const all = [photo(3, 30), photo(1, 10), photo(2, 20)];
    const strip = cardPhotos(all, 'default');
    expect(strip.cover?.id).toBe(10);
    expect(seqs(strip.photos)).toEqual([1, 2, 3]);
    expect(strip.total).toBe(3);
  });

  it('puts the chosen cover first and keeps the rest in position order', () => {
    const all = [1, 2, 3, 4, 5, 6].map((s) =>
      photo(s, s * 10, s === 4 ? { clip_fine_tag: 'kitchen', clip_confidence: 0.9 } : {}),
    );
    const strip = cardPhotos(all, 'kitchen');
    expect(strip.cover?.sequence).toBe(4);
    expect(seqs(strip.photos)).toEqual([4, 1, 2, 3, 5, 6]);
  });

  it('caps the strip at eight and counts every photo', () => {
    const all = Array.from({ length: 12 }, (_, i) => photo(i + 1, i + 1));
    const strip = cardPhotos(all, 'default');
    expect(CARD_STRIP_CAP).toBe(8);
    expect(strip.photos).toHaveLength(8);
    expect(seqs(strip.photos)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(strip.total).toBe(12);
  });

  it('leads with a cover found past the cap — the reason the read is not position-capped', () => {
    /* The trained head (DINOv3-first) says the façade is photo 11. */
    const all = Array.from({ length: 12 }, (_, i) =>
      photo(i + 1, i + 1, i + 1 === 11 ? { tag_head_scores: { '3': 0.93 } } : {}),
    );
    const strip = cardPhotos(all, 'exterior_facade');
    expect(strip.cover?.sequence).toBe(11);
    expect(seqs(strip.photos)).toEqual([11, 1, 2, 3, 4, 5, 6, 7]);
    expect(strip.total).toBe(12);
  });

  it('orders photos without a position last, by id', () => {
    const all = [photo(null, 7), photo(2, 5), photo(null, 3), photo(1, 9)];
    expect(cardPhotos(all, 'default').photos.map((p) => p.id)).toEqual([9, 5, 3, 7]);
  });

  it('is empty for an ad without photos', () => {
    expect(cardPhotos([], 'kitchen')).toEqual({ cover: null, photos: [], total: 0 });
  });
});
