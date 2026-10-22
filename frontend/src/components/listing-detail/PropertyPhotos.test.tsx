/* PropertyPhotos — "Moje fotky" (Photos W2): the acting identity's own image attachments as a
 * roll of tiles from the 1080 px copies, the camera and the picker, uploads one by one with the
 * "i / n" counter, the viewer on the original with Share and Delete, Vybrat with Share (N) /
 * Delete (N), and the share sheet with File objects or the download fallback. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { Attachment } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listAttachments: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
  deleteAttachment: vi.fn(),
}));

import { deleteAttachment, fetchAttachmentBlob, listAttachments, uploadAttachment } from '@/lib/api';
import PropertyPhotos from './PropertyPhotos';

const file = (over: Partial<Attachment>): Attachment => ({
  id: 1, property_id: 42, note_id: null, filename: 'a.jpg', mime_type: 'image/jpeg', byte_size: 10,
  created_at: '2026-10-20T10:00:00+00:00', owner_email: null, ...over,
});

/* Oldest first, as the API lists them: a photo, a PDF, another author's photo, a note's photo. */
const FILES = [
  file({ id: 1, filename: 'a.jpg' }),
  file({ id: 2, filename: 'plan.pdf', mime_type: 'application/pdf' }),
  file({ id: 3, filename: 'cizi.jpg', owner_email: 'b@x.cz' }),
  file({ id: 4, filename: 'b.jpg', note_id: 9, created_at: '2026-10-22T10:00:00+00:00' }),
];

const nav = navigator as unknown as Record<string, unknown>;

function show(props: Partial<Parameters<typeof PropertyPhotos>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PropertyPhotos property_id={42} lang="cs" camera {...props} />
    </QueryClientProvider>,
  );
}

let blobs = 0;
beforeEach(() => {
  // jsdom has no object URLs.
  Object.assign(URL, { createObjectURL: vi.fn(() => `blob:t/${++blobs}`), revokeObjectURL: vi.fn() });
  vi.mocked(listAttachments).mockResolvedValue({ data: FILES });
  vi.mocked(fetchAttachmentBlob).mockImplementation(async (_p, id, opts) =>
    new Blob([`${id}:${opts?.w ?? 'orig'}`], { type: 'image/jpeg' }));
  vi.mocked(uploadAttachment).mockResolvedValue(file({ id: 50 }));
  vi.mocked(deleteAttachment).mockResolvedValue({ deleted: true });
});

afterEach(() => {
  vi.clearAllMocks();
  delete nav.canShare;
  delete nav.share;
});

const tiles = () => screen.getAllByRole('button', { name: /^(Otevřít|Vybrat) .+\.jpg$/ });

describe('<PropertyPhotos>', () => {
  it('shows my own photos only, newest first, each from its 1080 px copy', async () => {
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    expect(tiles().map((t) => t.getAttribute('aria-label'))).toEqual(['Otevřít b.jpg', 'Otevřít a.jpg']);
    expect(screen.getByText('(2)')).toBeInTheDocument();
    await waitFor(() => expect(fetchAttachmentBlob).toHaveBeenCalledTimes(2));
    expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 4, { w: 1080, actAs: null });
    expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 1, { w: 1080, actAs: null });
    expect(listAttachments).toHaveBeenCalledWith(42);
  });

  it('offers the camera one capture at a time and the library several; the desktop uploads', async () => {
    const { unmount } = show();
    const cam = await screen.findByLabelText('Vyfotit');
    expect(cam).toHaveAttribute('type', 'file');
    expect(cam).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp');
    expect(cam).toHaveAttribute('capture', 'environment');
    expect(cam).not.toHaveAttribute('multiple');
    const lib = screen.getByLabelText('Z knihovny');
    expect(lib).toHaveAttribute('multiple');
    expect(lib).not.toHaveAttribute('capture');
    unmount();
    show({ lang: 'en', camera: false });
    expect(await screen.findByLabelText('Upload')).toHaveAttribute('multiple');
    expect(screen.queryByLabelText('Take photo')).toBeNull();
    expect(screen.getByRole('region', { name: 'My photos' })).toBeInTheDocument();
  });

  it('says so quietly when there is none, with the two buttons and no Vybrat', async () => {
    vi.mocked(listAttachments).mockResolvedValue({ data: [] });
    show();
    expect(await screen.findByText('Zatím žádné vlastní fotky')).toBeInTheDocument();
    expect(screen.getByLabelText('Vyfotit')).toBeInTheDocument();
    expect(screen.getByLabelText('Z knihovny')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Vybrat' })).toBeNull();
  });

  it('says a failed read out loud with a retry, never an empty roll', async () => {
    vi.mocked(listAttachments).mockRejectedValue(new Error('down'));
    show();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('se nepodařilo načíst');
    expect(within(alert).getByRole('button', { name: 'Zkusit znovu' })).toBeInTheDocument();
    expect(screen.queryByText('Zatím žádné vlastní fotky')).toBeNull();
  });

  it('uploads one by one with the counter, a capture under a dated name, then re-reads', async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    vi.mocked(uploadAttachment).mockImplementationOnce(
      () => new Promise((resolve) => { release = () => resolve(file({ id: 50 })); }),
    );
    show();
    await screen.findByLabelText('Vyfotit');
    await user.upload(screen.getByLabelText('Vyfotit'), new File(['x'], 'image.jpg', { type: 'image/jpeg' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Nahrávám 1 / 1…');
    const [pid, sent, opts] = vi.mocked(uploadAttachment).mock.calls[0];
    expect([pid, opts]).toEqual([42, { actAs: null }]);
    expect(sent.name).toMatch(/^foto-\d{4}-\d{2}-\d{2}-\d{6}\.jpg$/);
    release();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(listAttachments).toHaveBeenCalledTimes(2);

    await user.upload(screen.getByLabelText('Z knihovny'), [
      new File(['1'], 'IMG_1.jpg', { type: 'image/jpeg' }),
      new File(['2'], 'IMG_2.png', { type: 'image/png' }),
    ]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(3));
    expect(vi.mocked(uploadAttachment).mock.calls.slice(1).map(([, f]) => f.name)).toEqual(['IMG_1.jpg', 'IMG_2.png']);
  });

  it('refuses a dropped file that is no photo, by name', async () => {
    show();
    const strip = await screen.findByRole('region', { name: 'Moje fotky' });
    const pdf = new File(['%PDF'], 'smlouva.pdf', { type: 'application/pdf' });
    const dataTransfer = { files: [pdf], types: ['Files'], dropEffect: 'none' };
    fireEvent.dragEnter(strip, { dataTransfer });
    fireEvent.drop(strip, { dataTransfer });
    expect(await screen.findByText('smlouva.pdf: není fotka (JPEG, PNG nebo WebP)')).toBeInTheDocument();
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it('opens the viewer on the original and shares it as a File when the browser can', async () => {
    const user = userEvent.setup();
    nav.canShare = vi.fn(() => true);
    nav.share = vi.fn(() => Promise.resolve());
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Otevřít b.jpg' }));
    const viewer = await screen.findByRole('dialog');
    await waitFor(() => expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 4, { actAs: null }));
    const share = await within(viewer).findByRole('button', { name: 'Sdílet' });
    await user.click(share);
    expect(nav.share).toHaveBeenCalledTimes(1);
    const [{ files, title }] = vi.mocked(nav.share as (d: ShareData) => Promise<void>).mock.calls[0];
    expect(title).toBe('Nemovitost #42');
    expect(files).toHaveLength(1);
    expect(files![0]).toBeInstanceOf(File);
    expect([files![0].name, files![0].type]).toEqual(['b.jpg', 'image/jpeg']);
  });

  it('downloads the original where the browser cannot share files', async () => {
    const user = userEvent.setup();
    const saved: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Otevřít a.jpg' }));
    const viewer = await screen.findByRole('dialog');
    await user.click(await within(viewer).findByRole('button', { name: 'Stáhnout' }));
    expect(saved).toEqual(['a.jpg']);
    click.mockRestore();
  });

  it('deletes from the viewer only after a second, explicit tap', async () => {
    const user = userEvent.setup();
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Otevřít b.jpg' }));
    const viewer = await screen.findByRole('dialog');
    await user.click(within(viewer).getByRole('button', { name: 'Smazat' }));
    expect(deleteAttachment).not.toHaveBeenCalled();
    await user.click(within(viewer).getByRole('button', { name: 'Smazat fotku i z poznámky' }));
    await waitFor(() => expect(deleteAttachment).toHaveBeenCalledWith(42, 4, { actAs: null }));
  });

  it('Vybrat counts the picks, shares them together and deletes them behind a confirm', async () => {
    const user = userEvent.setup();
    nav.canShare = vi.fn(() => true);
    nav.share = vi.fn(() => Promise.resolve());
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Vybrat' }));
    expect(tiles().map((t) => t.getAttribute('aria-pressed'))).toEqual(['false', 'false']);
    expect(screen.getByText('Vybráno 0')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Vybrat b.jpg' }));
    await user.click(screen.getByRole('button', { name: 'Vybrat a.jpg' }));
    expect(screen.getByText('Vybráno 2')).toBeInTheDocument();
    expect(tiles().map((t) => t.getAttribute('aria-pressed'))).toEqual(['true', 'true']);

    const share = await screen.findByRole('button', { name: 'Sdílet (2)' });
    await waitFor(() => expect(share).toBeEnabled());
    await user.click(share);
    const [{ files }] = vi.mocked(nav.share as (d: ShareData) => Promise<void>).mock.calls[0];
    expect(files!.map((f) => f.name)).toEqual(['b.jpg', 'a.jpg']);

    await user.click(screen.getByRole('button', { name: 'Smazat (2)' }));
    expect(deleteAttachment).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Smazat 2 fotky i z poznámky' }));
    await waitFor(() => expect(deleteAttachment).toHaveBeenCalledTimes(2));
    expect(vi.mocked(deleteAttachment).mock.calls.map(([, id]) => id)).toEqual([4, 1]);
    await waitFor(() => expect(screen.queryByText(/^Vybráno/)).toBeNull());
  });

  it('offers a retry in place of Share when a picked original could not be read', async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAttachmentBlob).mockImplementation(async (_p, id, opts) => {
      if (!opts?.w) throw new Error('down');
      return new Blob([`${id}`], { type: 'image/jpeg' });
    });
    show();
    await waitFor(() => expect(tiles()).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Vybrat' }));
    await user.click(screen.getByRole('button', { name: 'Vybrat a.jpg' }));
    const retry = await screen.findByRole('button', { name: 'Zkusit znovu' });
    vi.mocked(fetchAttachmentBlob).mockResolvedValue(new Blob(['ok'], { type: 'image/jpeg' }));
    await user.click(retry);
    expect(await screen.findByRole('button', { name: /^(Sdílet|Stáhnout)$/ })).toBeEnabled();
  });
});
