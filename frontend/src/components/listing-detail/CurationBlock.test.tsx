/* CurationBlock — accessible names for the three unlabelled curation inputs.
 *
 * All three were reachable only through a placeholder (or, for the note
 * editor, through nothing at all while autoFocus dropped the caret into it).
 * The list/membership reads are mocked so no network call fires.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import type { ArtifactLink, Note, Tag } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listCollections: vi.fn(),
  listTags: vi.fn(),
  listPropertyNotes: vi.fn(),
  createPropertyNote: vi.fn(),
  deletePropertyNote: vi.fn(),
  getPropertyArtifactLink: vi.fn(),
  setPropertyArtifactLink: vi.fn(),
  deletePropertyArtifactLink: vi.fn(),
  uploadNoteAttachment: vi.fn(),
  fetchNoteAttachmentBlob: vi.fn(),
  deleteNoteAttachment: vi.fn(),
}));

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchPropertyCollectionMemberSet: vi.fn(),
  fetchPropertyTagMemberSet: vi.fn(),
}));

import {
  createPropertyNote,
  deleteNoteAttachment,
  deletePropertyArtifactLink,
  deletePropertyNote,
  fetchNoteAttachmentBlob,
  getPropertyArtifactLink,
  listCollections,
  listPropertyNotes,
  listTags,
  setPropertyArtifactLink,
  uploadNoteAttachment,
} from '@/lib/api';
import { curationKeys, fetchPropertyCollectionMemberSet, fetchPropertyTagMemberSet } from '@/lib/queries';
import CurationBlock from './CurationBlock';

const TAG: Tag = {
  id: 3, name: 'k prohlídce', color: 'copper', created_at: '2026-05-01T00:00:00+00:00',
  listing_count: 2,
};

const NOTE: Note = {
  id: 11, property_id: 42, body: 'Sousedi jsou hlučni.', origin_listing_id: 900,
  created_at: '2026-05-02T00:00:00+00:00', updated_at: null,
};

const LINK: ArtifactLink = {
  property_id: 42, url: 'https://claude.ai/artifact/abc123',
  created_at: '2026-05-03T00:00:00+00:00', updated_at: '2026-05-03T00:00:00+00:00',
};

function mockReads() {
  vi.mocked(getPropertyArtifactLink).mockResolvedValue({ data: null });
  vi.mocked(listCollections).mockResolvedValue({ data: [] } as never);
  vi.mocked(listTags).mockResolvedValue({ data: [TAG] } as never);
  vi.mocked(listPropertyNotes).mockResolvedValue({ data: [NOTE] } as never);
  vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  vi.mocked(fetchPropertyTagMemberSet).mockResolvedValue(new Map());
}

function renderBlock() {
  mockReads();
  return renderWithReads();
}

/* Renders over whatever the mocks hold now, so a test can fail one read first. */
function renderWithReads() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CurationBlock property_id={42} sreality_id={900} listing_id={900} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, invalidate };
}

describe('<CurationBlock> control names', () => {
  it('names the note composer by the visible Notes heading', async () => {
    renderBlock();
    expect(await screen.findByRole('textbox', { name: 'Notes' }))
      .toHaveAccessibleName('Notes');
  });

  it('names the tag find-or-create box', async () => {
    renderBlock();
    expect(await screen.findByRole('textbox', { name: 'Najít nebo vytvořit štítek' }))
      .toBeInTheDocument();
  });

  it('names the note editor that autoFocus lands in', async () => {
    const user = userEvent.setup();
    renderBlock();
    await user.click(await screen.findByRole('button', { name: 'Edit note' }));
    const editor = screen.getByRole('textbox', { name: 'Edit note' });
    expect(editor).toHaveAccessibleName('Edit note');
    expect(document.activeElement).toBe(editor);
  });
});

/* MS16: every Browse row's note mark counts the account's notes, so a note
 * added or deleted here re-reads the counts. */
describe('<CurationBlock> note counts', () => {
  it('re-reads the note marks after a note is added and after one is deleted', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue(NOTE as never);
    vi.mocked(deletePropertyNote).mockResolvedValue({ deleted: true } as never);
    const { invalidate } = renderWithReads();
    const counts = () =>
      invalidate.mock.calls.filter(([f]) => f?.queryKey === curationKeys.noteCounts).length;

    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), 'Volat v pondělí.');
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(counts()).toBe(1));

    await user.click(screen.getByRole('button', { name: 'Delete note' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(counts()).toBe(2));
  });
});

/* MS16: a failed read says so and reads again; it is never drawn as "no notes",
 * "no tags" or a row of unticked collections. */
describe('<CurationBlock> failed reads', () => {
  const banner = (subject: string) =>
    screen.findByText(subject, { selector: 'strong' }).then((s) => s.parentElement as HTMLElement);

  it('says the notes failed, with no "(0)", and reads them again', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWithReads();

    const notes = await banner('Poznámky');
    expect(screen.getByText('(—)')).toBeInTheDocument();
    expect(screen.queryByText('(0)')).toBeNull();
    fireEvent.click(within(notes).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByText('Sousedi jsou hlučni.')).toBeInTheDocument();
    expect(screen.getByText('(1)')).toBeInTheDocument();
  });

  it('says the tags failed instead of offering an empty picker, and reads them again', async () => {
    mockReads();
    vi.mocked(fetchPropertyTagMemberSet).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWithReads();

    const tags = await banner('Štítky');
    expect(screen.queryByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeNull();
    fireEvent.click(within(tags).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeInTheDocument();
  });

  it('says the collection memberships failed instead of unticking every toggle', async () => {
    mockReads();
    vi.mocked(listCollections).mockResolvedValue({ data: [{ id: 7, name: 'Šortlist' }] } as never);
    vi.mocked(fetchPropertyCollectionMemberSet).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWithReads();

    const collections = await banner('Kolekce');
    expect(screen.queryByRole('button', { name: /Šortlist/ })).toBeNull();
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    fireEvent.click(within(collections).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByRole('button', { name: /Šortlist/ })).toHaveAttribute('aria-pressed', 'true');
  });
});

/* Files on notes (migration 592): dropped or picked into the composer they wait for the note;
 * dropped onto a saved note they go onto that note; a refused file is named, never sent. */
describe('<CurationBlock> note attachments', () => {
  const pdf = () => new File(['%PDF'], 'plan.pdf', { type: 'application/pdf' });
  const dropOn = (el: HTMLElement, files: File[]) => {
    const dataTransfer = { files, types: ['Files'], dropEffect: 'none' };
    fireEvent.dragEnter(el, { dataTransfer });
    fireEvent.dragOver(el, { dataTransfer });
    fireEvent.drop(el, { dataTransfer });
  };
  const savedNote = () => screen.findByRole('group', { name: /^Note from/ });

  beforeEach(() => {
    vi.clearAllMocks();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('stages a file dropped on the Notes section and uploads it once the note is saved', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    vi.mocked(uploadNoteAttachment).mockResolvedValue({} as never);
    renderWithReads();
    const box = await screen.findByRole('textbox', { name: 'Notes' });

    const file = pdf();
    dropOn(box, [file]);
    expect(within(screen.getByRole('list', { name: 'Files to attach' })).getByText('plan.pdf'))
      .toBeInTheDocument();
    expect(uploadNoteAttachment).not.toHaveBeenCalled();

    await user.type(box, 'Půdorys od makléře.');
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(uploadNoteAttachment).toHaveBeenCalledWith(42, 12, file));
    expect(createPropertyNote).toHaveBeenCalledWith(42, 'Půdorys od makléře.', 900, 900);
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull());
  });

  it('saves a files-only note under its file names', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    vi.mocked(uploadNoteAttachment).mockResolvedValue({} as never);
    renderWithReads();
    await screen.findByRole('textbox', { name: 'Notes' });

    await user.upload(screen.getAllByTestId('attach-input')[0], pdf());
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(createPropertyNote).toHaveBeenCalledWith(
      42, 'Attached: plan.pdf', 900, 900));
  });

  it('puts a file dropped on a saved note onto that note, not into the composer', async () => {
    mockReads();
    vi.mocked(uploadNoteAttachment).mockResolvedValue({} as never);
    const { invalidate } = renderWithReads();
    const file = pdf();

    dropOn(await savedNote(), [file]);
    await waitFor(() => expect(uploadNoteAttachment).toHaveBeenCalledWith(42, NOTE.id, file));
    expect(createPropertyNote).not.toHaveBeenCalled();
    expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: curationKeys.propertyNotes(42) }));
  });

  it('names a refused file and never sends it', async () => {
    mockReads();
    renderWithReads();
    dropOn(await screen.findByRole('textbox', { name: 'Notes' }),
      [new File(['<svg/>'], 'drawing.svg', { type: 'image/svg+xml' })]);
    expect(await screen.findByText('drawing.svg: this file type cannot be attached'))
      .toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull();
    expect(uploadNoteAttachment).not.toHaveBeenCalled();
  });

  it('shows a saved note’s image as a thumbnail, downloads the rest, and asks before removing',
    async () => {
      const user = userEvent.setup();
      mockReads();
      const at = '2026-05-02T00:00:00+00:00';
      vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, attachments: [
        { id: 1, note_id: 11, filename: 'foto.png', mime_type: 'image/png', byte_size: 2048,
          created_at: at },
        { id: 2, note_id: 11, filename: 'plan.pdf', mime_type: 'application/pdf',
          byte_size: 4096, created_at: at },
      ] }] } as never);
      vi.mocked(fetchNoteAttachmentBlob).mockResolvedValue(new Blob(['x']));
      vi.mocked(deleteNoteAttachment).mockResolvedValue({ deleted: true } as never);
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      renderWithReads();

      const files = await screen.findByRole('list', { name: 'Attachments' });
      expect(await within(files).findByRole('img', { name: 'foto.png' })).toBeInTheDocument();
      expect(fetchNoteAttachmentBlob).toHaveBeenCalledWith(42, 11, 1);

      await user.click(within(files).getByRole('button', { name: 'Download plan.pdf' }));
      await waitFor(() => expect(fetchNoteAttachmentBlob).toHaveBeenCalledWith(42, 11, 2));
      await waitFor(() => expect(click).toHaveBeenCalled());

      await user.click(within(files).getByRole('button', { name: 'Remove plan.pdf' }));
      expect(deleteNoteAttachment).not.toHaveBeenCalled();
      expect(screen.getByText('Remove plan.pdf?')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Remove' }));
      await waitFor(() => expect(deleteNoteAttachment).toHaveBeenCalledWith(42, 11, 2));
      click.mockRestore();
    });
});

/* The per-property Artifact link (migration 599): one clickable http(s) URL, set, replaced and
 * removed through the API; anything else is never drawn as an href. */
describe('<CurationBlock> artifact link', () => {
  beforeEach(() => {
    vi.mocked(setPropertyArtifactLink).mockReset();
    vi.mocked(deletePropertyArtifactLink).mockReset();
  });

  it('offers an empty field, names it by its heading, and saves a link', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(setPropertyArtifactLink).mockResolvedValue(LINK);
    renderWithReads();

    const field = await screen.findByRole('textbox', { name: 'Artifact link' });
    await user.type(field, '  https://claude.ai/artifact/abc123 ');
    await user.click(screen.getByRole('button', { name: 'Save link' }));

    await waitFor(() =>
      expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://claude.ai/artifact/abc123'));
    const anchor = await screen.findByRole('link', { name: 'claude.ai/artifact/abc123' });
    expect(anchor).toHaveAttribute('href', 'https://claude.ai/artifact/abc123');
    expect(anchor).toHaveAttribute('target', '_blank');
    expect(anchor).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('refuses a value that is not an http(s) address without calling the API', async () => {
    const user = userEvent.setup();
    mockReads();
    renderWithReads();

    await user.type(await screen.findByRole('textbox', { name: 'Artifact link' }), 'javascript:alert(1)');
    await user.click(screen.getByRole('button', { name: 'Save link' }));

    expect(await screen.findByText(/starting with http/)).toBeInTheDocument();
    expect(setPropertyArtifactLink).not.toHaveBeenCalled();
  });

  it('shows a saved link as a clickable anchor, and replaces it in place', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(getPropertyArtifactLink).mockResolvedValue({ data: LINK });
    vi.mocked(setPropertyArtifactLink).mockResolvedValue({ ...LINK, url: 'https://example.cz/page' });
    renderWithReads();

    expect(await screen.findByRole('link', { name: 'claude.ai/artifact/abc123' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Edit artifact link' }));
    const field = screen.getByRole('textbox', { name: 'Artifact link' });
    expect(field).toHaveValue(LINK.url);
    await user.clear(field);
    await user.type(field, 'https://example.cz/page');
    await user.click(screen.getByRole('button', { name: 'Save link' }));

    expect(await screen.findByRole('link', { name: 'example.cz/page' })).toHaveAttribute(
      'href', 'https://example.cz/page');
    expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://example.cz/page');
  });

  it('asks before removing, then clears the field', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(getPropertyArtifactLink).mockResolvedValue({ data: LINK });
    vi.mocked(deletePropertyArtifactLink).mockResolvedValue({ deleted: true });
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Remove artifact link' }));
    expect(deletePropertyArtifactLink).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(deletePropertyArtifactLink).toHaveBeenCalledWith(42));
    expect(await screen.findByRole('textbox', { name: 'Artifact link' })).toHaveValue('');
    expect(screen.queryByRole('link', { name: /claude\.ai/ })).not.toBeInTheDocument();
  });

  it('never draws a stored non-http value as a link', async () => {
    mockReads();
    vi.mocked(getPropertyArtifactLink).mockResolvedValue({
      data: { ...LINK, url: 'javascript:alert(1)' },
    });
    renderWithReads();

    expect(await screen.findByText('javascript:alert(1)')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /alert/ })).not.toBeInTheDocument();
  });

  it('says a failed read failed, instead of offering an empty field', async () => {
    mockReads();
    vi.mocked(getPropertyArtifactLink).mockRejectedValue(new Error('boom'));
    renderWithReads();

    expect(await screen.findByText(/Odkaz na artefakt/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Artifact link' })).not.toBeInTheDocument();
  });
});
