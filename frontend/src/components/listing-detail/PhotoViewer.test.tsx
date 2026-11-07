/* PhotoViewer — every property photo surface opens the one viewer on the photo tapped.
 *
 * Pins:
 *   * the header mosaic: a tile opens on itself, and so does the "Všech N fotek" tile,
 *     from where the arrows walk every photo (operator 2026-11-06: it used to switch
 *     to the Fotky tab below the fold, which looked like nothing happened);
 *   * the Fotky tab's grid and the phone swiper open on the photo tapped;
 *   * an off-market property's photos are dimmed in the viewer too;
 *   * closing returns to the page. */

import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { bareImage, type ImagePublic } from '@/lib/types';
import PropertyGallery from '@/components/phone/PropertyGallery';
import Gallery from './Gallery';
import PhotoMosaic from './PhotoMosaic';

const photos = (n: number): ImagePublic[] =>
  Array.from({ length: n }, (_, i) => bareImage(i + 1, null, `https://example.test/${i + 1}.jpg`));

const viewer = () => screen.getByRole('dialog');

describe('the header mosaic', () => {
  it('opens a tile on its own photo', () => {
    render(<PhotoMosaic images={photos(25)} loading={false} isActive />);
    fireEvent.click(screen.getByRole('button', { name: 'Fotka 2 z 25' }));
    expect(viewer()).toHaveAccessibleName('2 / 25');
  });

  it('opens the "Všech N fotek" tile on its photo, and the arrows walk them all', () => {
    render(<PhotoMosaic images={photos(25)} loading={false} isActive />);
    const more = screen.getByRole('button', { name: 'Všech 25 fotek' });
    expect(more).toHaveTextContent('Všech 25 fotek');
    fireEvent.click(more);
    expect(viewer()).toHaveAccessibleName('5 / 25');
    fireEvent.click(within(viewer()).getByRole('button', { name: 'Next photo' }));
    expect(viewer()).toHaveAccessibleName('6 / 25');
  });

  it('dims an off-market property in the viewer, and closes back to the page', () => {
    render(<PhotoMosaic images={photos(3)} loading={false} isActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fotka 1 z 3' }));
    expect(viewer().querySelector('img')).toHaveClass('opacity-70');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('the Fotky tab and the phone', () => {
  it('opens the Fotky grid on the photo tapped', () => {
    render(<Gallery images={photos(8)} isActive />);
    fireEvent.click(screen.getAllByRole('button')[6]);
    expect(viewer()).toHaveAccessibleName('7 / 8');
  });

  it('opens the phone swiper on the photo tapped', () => {
    render(<PropertyGallery images={photos(4)} loading={false} isActive />);
    fireEvent.click(screen.getByRole('button', { name: 'Fotka 3 z 4' }));
    expect(viewer()).toHaveAccessibleName('3 / 4');
  });
});
