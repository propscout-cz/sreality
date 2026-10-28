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

import type { ArtifactLink, Collection, Note, Tag } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listTags: vi.fn(),
  listPropertyNotes: vi.fn(),
  createPropertyNote: vi.fn(),
  updatePropertyNote: vi.fn(),
  deletePropertyNote: vi.fn(),
  fetchArtifactLinks: vi.fn(),
  setPropertyArtifactLink: vi.fn(),
  deletePropertyArtifactLink: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
  deleteAttachment: vi.fn(),
  listCollections: vi.fn(),
}));

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchPropertyTagMemberSet: vi.fn(),
  fetchPropertyCollectionMemberSet: vi.fn(),
}));

import {
  createPropertyNote,
  deleteAttachment,
  deletePropertyArtifactLink,
  deletePropertyNote,
  fetchAttachmentBlob,
  fetchArtifactLinks,
  listCollections,
  listPropertyNotes,
  listTags,
  setPropertyArtifactLink,
  updatePropertyNote,
  uploadAttachment,
} from '@/lib/api';
import {
  curationKeys,
  fetchPropertyCollectionMemberSet,
  fetchPropertyTagMemberSet,
} from '@/lib/queries';
import { ActingAs, actingKey } from '@/lib/acting';
import { slotInstant, viewingSlot, viewingWhen } from '@/lib/viewings';
import CurationBlock, { PhoneTagsAndNotes } from './CurationBlock';

const TAG: Tag = {
  id: 3, name: 'k prohlídce', color: 'copper', created_at: '2026-05-01T00:00:00+00:00',
  listing_count: 2,
};

const NOTE: Note = {
  id: 11, property_id: 42, body: 'Sousedi jsou hlučni.',
  created_at: '2026-05-02T00:00:00+00:00', updated_at: null, shared: false, owner_email: null,
};

const LINK: ArtifactLink = {
  property_id: 42, url: 'https://claude.ai/artifact/abc123',
  created_at: '2026-05-03T00:00:00+00:00', updated_at: '2026-05-03T00:00:00+00:00',
};

/* What the composer sends for a note written on advert 900. */
const sent = (body: string, shared: boolean, viewing_at: string | null = null) => ({
  body, origin_listing_ref_id: 900, shared, viewing_at,
});

function mockReads() {
  vi.mocked(fetchArtifactLinks).mockResolvedValue(new Map());
  vi.mocked(listTags).mockResolvedValue({ data: [TAG] } as never);
  vi.mocked(listPropertyNotes).mockResolvedValue({ data: [NOTE] } as never);
  vi.mocked(fetchPropertyTagMemberSet).mockResolvedValue(new Map());
  vi.mocked(listCollections).mockResolvedValue({ data: [], total: 0 });
  vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
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
        <CurationBlock property_id={42} listing_id={900} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, invalidate };
}

/* The phone's tags + artifact link + notes: the desktop keeps the tags in the
 * header's Štítky button (TagsToggle), so the inline tag rows are tested here. */
function renderPhone() {
  mockReads();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PhoneTagsAndNotes property_id={42} listing_id={900} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/* Ruling 6 (docs/design/collection-sharing.md): the header's "Uložit do kolekce"
 * is the property page's one collection editor; this block has no second one. */
describe('<CurationBlock> collections', () => {
  it('renders no collection row', async () => {
    renderBlock();
    await screen.findByRole('textbox', { name: 'Poznámky' });
    expect(screen.queryByText('Collections')).toBeNull();
    expect(screen.queryByRole('link', { name: /Manage/ })).toBeNull();
    // The collection reads feed only the notes' share audience: no save control here.
    expect(screen.queryByRole('button', { name: /kolekce|collection/i })).toBeNull();
  });
});

describe('<CurationBlock> control names', () => {
  it('names the note composer by the visible Poznámky heading', async () => {
    renderBlock();
    expect(await screen.findByRole('textbox', { name: 'Poznámky' }))
      .toHaveAccessibleName('Poznámky');
  });

  it('names the tag find-or-create box', async () => {
    renderPhone();
    expect(await screen.findByRole('textbox', { name: 'Najít nebo vytvořit štítek' }))
      .toBeInTheDocument();
  });

  it('names the note editor that autoFocus lands in', async () => {
    const user = userEvent.setup();
    renderBlock();
    await user.click(await screen.findByRole('button', { name: 'Upravit poznámku' }));
    const editor = screen.getByRole('textbox', { name: 'Upravit poznámku' });
    expect(editor).toHaveAccessibleName('Upravit poznámku');
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

    await user.type(await screen.findByRole('textbox', { name: 'Poznámky' }), 'Volat v pondělí.');
    await user.click(screen.getByRole('button', { name: 'Uložit poznámku' }));
    await waitFor(() => expect(counts()).toBe(1));

    await user.click(screen.getByRole('button', { name: 'Smazat poznámku' }));
    await user.click(screen.getByRole('button', { name: 'Smazat' }));
    await waitFor(() => expect(counts()).toBe(2));
  });
});

/* MS16: a failed read says so and reads again; it is never drawn as "no notes"
 * or "no tags". */
describe('<CurationBlock> failed reads', () => {
  const banner = (subject: string) =>
    screen.findByText(subject, { selector: 'strong' }).then((s) => s.parentElement as HTMLElement);

  it('says the notes failed, with no "0", and reads them again', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockRejectedValueOnce(new Error('HTTP 500'));
    renderWithReads();

    const notes = await banner('Poznámky');
    /* The section card's bare count (C6): a dash, never a 0 it does not know. */
    const card = within(screen.getByRole('region', { name: 'Poznámky' }));
    expect(card.getByText('—')).toBeInTheDocument();
    expect(card.queryByText('0')).toBeNull();
    fireEvent.click(within(notes).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByText('Sousedi jsou hlučni.')).toBeInTheDocument();
    expect(card.getByText('1')).toBeInTheDocument();
  });

  it('says the tags failed instead of offering an empty picker, and reads them again', async () => {
    vi.mocked(fetchPropertyTagMemberSet).mockRejectedValueOnce(new Error('HTTP 500'));
    renderPhone();

    const tags = await banner('Štítky');
    expect(screen.queryByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeNull();
    fireEvent.click(within(tags).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeInTheDocument();
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
  const savedNote = () => screen.findByRole('article', { name: /^Poznámka z/ });

  beforeEach(() => {
    vi.clearAllMocks();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('stages a file dropped on the Notes section and uploads it once the note is saved', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    vi.mocked(uploadAttachment).mockResolvedValue({} as never);
    renderWithReads();
    const box = await screen.findByRole('textbox', { name: 'Poznámky' });

    const file = pdf();
    dropOn(box, [file]);
    expect(within(screen.getByRole('list', { name: 'Files to attach' })).getByText('plan.pdf'))
      .toBeInTheDocument();
    expect(uploadAttachment).not.toHaveBeenCalled();

    await user.type(box, 'Půdorys od makléře.');
    await user.click(screen.getByRole('button', { name: 'Uložit poznámku' }));
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith(
      42, file, expect.objectContaining({ note_id: 12 })));
    expect(createPropertyNote).toHaveBeenCalledWith(42, sent('Půdorys od makléře.', false));
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull());
  });

  it('saves a files-only note under its file names', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    vi.mocked(uploadAttachment).mockResolvedValue({} as never);
    renderWithReads();
    // The composer reads as one line until it is used; its tools appear on focus.
    await user.click(await screen.findByRole('textbox', { name: 'Poznámky' }));

    await user.upload(screen.getAllByTestId('attach-input')[0], pdf());
    await user.click(screen.getByRole('button', { name: 'Uložit poznámku' }));
    await waitFor(() => expect(createPropertyNote).toHaveBeenCalledWith(42, sent('Přílohy: plan.pdf', false)));
  });

  it('puts a file dropped on a saved note onto that note, not into the composer', async () => {
    mockReads();
    vi.mocked(uploadAttachment).mockResolvedValue({} as never);
    const { invalidate } = renderWithReads();
    const file = pdf();

    dropOn(await savedNote(), [file]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith(
      42, file, expect.objectContaining({ note_id: NOTE.id })));
    expect(createPropertyNote).not.toHaveBeenCalled();
    expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: curationKeys.propertyNotes(42) }));
  });

  it('names a refused file and never sends it', async () => {
    mockReads();
    renderWithReads();
    dropOn(await screen.findByRole('textbox', { name: 'Poznámky' }),
      [new File(['<svg/>'], 'drawing.svg', { type: 'image/svg+xml' })]);
    expect(await screen.findByText('drawing.svg: this file type cannot be attached'))
      .toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull();
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it('shows a saved note’s image as a thumbnail, downloads the rest, and asks before removing',
    async () => {
      const user = userEvent.setup();
      mockReads();
      const at = '2026-05-02T00:00:00+00:00';
      vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, attachments: [
        { id: 1, property_id: 42, note_id: 11, filename: 'foto.png', mime_type: 'image/png',
          byte_size: 2048, created_at: at },
        { id: 2, property_id: 42, note_id: 11, filename: 'plan.pdf', mime_type: 'application/pdf',
          byte_size: 4096, created_at: at },
      ] }] } as never);
      vi.mocked(fetchAttachmentBlob).mockResolvedValue(new Blob(['x']));
      vi.mocked(deleteAttachment).mockResolvedValue({ deleted: true } as never);
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      renderWithReads();

      const files = await screen.findByRole('list', { name: 'Attachments' });
      expect(await within(files).findByRole('img', { name: 'foto.png' })).toBeInTheDocument();
      expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 1, expect.objectContaining({ w: 1080 }));

      await user.click(within(files).getByRole('button', { name: 'Download plan.pdf' }));
      await waitFor(() => expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 2));
      await waitFor(() => expect(click).toHaveBeenCalled());

      await user.click(within(files).getByRole('button', { name: 'Remove plan.pdf' }));
      expect(deleteAttachment).not.toHaveBeenCalled();
      expect(screen.getByText('Remove plan.pdf?')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Remove' }));
      await waitFor(() => expect(deleteAttachment).toHaveBeenCalledWith(42, 2));
      click.mockRestore();
    });

  it('opens a note’s photo in the viewer on its original, never a new tab (Photos W2)', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, attachments: [
      { id: 1, property_id: 42, note_id: 11, filename: 'foto.png', mime_type: 'image/png',
        byte_size: 2048, created_at: '2026-05-02T00:00:00+00:00' },
    ] }] } as never);
    vi.mocked(fetchAttachmentBlob).mockResolvedValue(new Blob(['x'], { type: 'image/png' }));
    renderWithReads();

    const files = await screen.findByRole('list', { name: 'Attachments' });
    const tile = await within(files).findByRole('button', { name: 'Open foto.png' });
    expect(files.querySelector('a[target="_blank"]')).toBeNull();
    // Its × is there without a hover: a finger reaches it.
    expect(within(files).getByRole('button', { name: 'Remove foto.png' }).className).not.toMatch(/opacity-0/);
    await user.click(tile);
    const viewer = await screen.findByRole('dialog');
    await waitFor(() => expect(fetchAttachmentBlob).toHaveBeenCalledWith(42, 1, { actAs: null }));
    expect(within(viewer).getByRole('button', { name: 'Smazat' })).toBeInTheDocument();
  });
});

/* The per-property Artifact link (migration 599): one clickable http(s) URL, set, replaced and
 * removed through the API; anything else is never drawn as an href. */
describe('<CurationBlock> artifact link', () => {
  beforeEach(() => {
    vi.mocked(setPropertyArtifactLink).mockReset();
    vi.mocked(deletePropertyArtifactLink).mockReset();
  });

  it('offers "+ Odkaz na artefakt", opens a named field, and saves a link', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(setPropertyArtifactLink).mockResolvedValue(LINK);
    renderWithReads();

    expect(screen.queryByRole('textbox', { name: 'Odkaz na artefakt' })).toBeNull();
    await user.click(await screen.findByRole('button', { name: /Odkaz na artefakt/ }));
    const field = screen.getByRole('textbox', { name: 'Odkaz na artefakt' });
    await user.type(field, '  https://claude.ai/artifact/abc123 ');
    await user.click(screen.getByRole('button', { name: 'Uložit odkaz' }));

    await waitFor(() =>
      expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://claude.ai/artifact/abc123'));
    const anchor = await screen.findByRole('link', { name: 'claude.ai/artifact/abc123' });
    expect(anchor).toHaveAttribute('href', 'https://claude.ai/artifact/abc123');
    expect(anchor).toHaveAttribute('target', '_blank');
    expect(anchor).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('inside <ActingAs> saves the owner\'s link as the owner, under the owner\'s key', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(setPropertyArtifactLink).mockResolvedValue(LINK);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ActingAs share={{ account_id: 'owner-1', email: 'jana@example.cz', mode: 'edit' }}>
            <CurationBlock property_id={42} listing_id={900} />
          </ActingAs>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('button', { name: /Odkaz na artefakt/ }));
    await user.type(screen.getByRole('textbox', { name: 'Odkaz na artefakt' }), 'https://claude.ai/artifact/abc123');
    await user.click(screen.getByRole('button', { name: 'Uložit odkaz' }));
    await waitFor(() =>
      expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://claude.ai/artifact/abc123', { actAs: 'owner-1' }));
    expect(fetchArtifactLinks).toHaveBeenCalledWith({ actAs: 'owner-1' });
    expect(qc.getQueryData(curationKeys.artifactLinks)).toBeUndefined();
  });

  it('refuses a value that is not an http(s) address without calling the API', async () => {
    const user = userEvent.setup();
    mockReads();
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: /Odkaz na artefakt/ }));
    await user.type(screen.getByRole('textbox', { name: 'Odkaz na artefakt' }), 'javascript:alert(1)');
    await user.click(screen.getByRole('button', { name: 'Uložit odkaz' }));

    expect(await screen.findByText(/začínající http/)).toBeInTheDocument();
    expect(setPropertyArtifactLink).not.toHaveBeenCalled();
  });

  it('shows a saved link as a clickable anchor, and replaces it in place', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(fetchArtifactLinks).mockResolvedValue(new Map([[42, LINK]]));
    vi.mocked(setPropertyArtifactLink).mockResolvedValue({ ...LINK, url: 'https://example.cz/page' });
    renderWithReads();

    expect(await screen.findByRole('link', { name: 'claude.ai/artifact/abc123' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Upravit odkaz na artefakt' }));
    const field = screen.getByRole('textbox', { name: 'Odkaz na artefakt' });
    expect(field).toHaveValue(LINK.url);
    await user.clear(field);
    await user.type(field, 'https://example.cz/page');
    await user.click(screen.getByRole('button', { name: 'Uložit odkaz' }));

    expect(await screen.findByRole('link', { name: 'example.cz/page' })).toHaveAttribute(
      'href', 'https://example.cz/page');
    expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://example.cz/page');
  });

  it('asks before removing, then offers to add one again', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(fetchArtifactLinks).mockResolvedValue(new Map([[42, LINK]]));
    vi.mocked(deletePropertyArtifactLink).mockResolvedValue({ deleted: true });
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Odebrat odkaz na artefakt' }));
    expect(deletePropertyArtifactLink).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Odebrat' }));

    await waitFor(() => expect(deletePropertyArtifactLink).toHaveBeenCalledWith(42));
    expect(await screen.findByRole('button', { name: /Odkaz na artefakt/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /claude\.ai/ })).not.toBeInTheDocument();
  });

  it('never draws a stored non-http value as a link', async () => {
    mockReads();
    vi.mocked(fetchArtifactLinks).mockResolvedValue(
      new Map([[42, { ...LINK, url: 'javascript:alert(1)' }]]),
    );
    renderWithReads();

    expect(await screen.findByText('javascript:alert(1)')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /alert/ })).not.toBeInTheDocument();
  });

  it('says a failed read failed, instead of offering an empty field', async () => {
    mockReads();
    vi.mocked(fetchArtifactLinks).mockRejectedValue(new Error('boom'));
    renderWithReads();

    expect(await screen.findByText(/Odkaz na artefakt/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Odkaz na artefakt' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Odkaz na artefakt/ })).not.toBeInTheDocument();
  });
});

/* Sharing (migration 610, docs/design/collection-sharing.md §3.7): a note crosses a
 * share only when its author marks it; another author's note is theirs to edit. */
describe('<CurationBlock> shared notes', () => {
  const SHARED_COLLECTION: Collection = {
    id: 7, name: 'Šortlist', description: null, created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z', listing_count: 1, monitoring_enabled: false,
    is_system: false, owner_email: null,
    shared_with: [{ account_id: 'b', email: 'petr@example.cz' }],
  };
  /* The property sits in one of my collections that I share with petr@. */
  const withAudience = () => {
    vi.mocked(listCollections).mockResolvedValue({ data: [SHARED_COLLECTION], total: 1 });
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
  };
  const noteGroup = () => screen.findByRole('article', { name: /^Poznámka/ });

  beforeEach(() => {
    vi.clearAllMocks();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('shows another author\'s note read-only, named by its author', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{
      ...NOTE, id: 12, shared: true, owner_email: 'petr@example.cz',
      attachments: [{ id: 5, note_id: 12, filename: 'plan.pdf', mime_type: 'application/pdf',
        byte_size: 4096, created_at: NOTE.created_at }],
    }] } as never);
    renderWithReads();

    const note = await screen.findByRole('article', { name: /^Poznámka od petr@example\.cz · / });
    expect(within(note).getByText('Sousedi jsou hlučni.')).toBeInTheDocument();
    expect(within(note).getByText('petr@example.cz')).toBeInTheDocument();
    for (const verb of [/Upravit poznámku/, /Smazat poznámku/, /Přiložit soubory/, /Remove plan\.pdf/]) {
      expect(within(note).queryByRole('button', { name: verb })).toBeNull();
    }
    // The file still opens.
    expect(within(note).getByRole('button', { name: 'Download plan.pdf' })).toBeInTheDocument();
    expect(within(note).queryByRole('button', { name: /^Sdílet|Nesdílet/ })).toBeNull();
  });

  it('never takes a file dropped on another author\'s note onto that note', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{
      ...NOTE, id: 12, shared: true, owner_email: 'petr@example.cz',
    }] } as never);
    renderWithReads();

    const note = await screen.findByRole('article', { name: /^Poznámka od petr@example\.cz/ });
    const file = new File(['%PDF'], 'plan.pdf', { type: 'application/pdf' });
    const dataTransfer = { files: [file], types: ['Files'], dropEffect: 'none' };
    fireEvent.dragEnter(note, { dataTransfer });
    fireEvent.dragOver(note, { dataTransfer });
    fireEvent.drop(note, { dataTransfer });

    // It bubbles to the composer and waits for a NEW note of mine.
    expect(await screen.findByText('plan.pdf')).toBeInTheDocument();
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it('offers no share checkbox while the property sits only in my unshared collection', async () => {
    mockReads();
    vi.mocked(listCollections).mockResolvedValue({
      data: [{ ...SHARED_COLLECTION, shared_with: [] }], total: 1,
    });
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    // Both reads settled: the mark now states the audience is empty.
    const note = await noteGroup();
    expect(await within(note).findByText('Sdíleno · zatím to nikdo nevidí')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Sdílet s/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Sdílet s/ })).toBeNull();
  });

  it('never says "nobody" while the collection list is still loading', async () => {
    mockReads();
    vi.mocked(listCollections).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(within(note).getByText('Sdíleno')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    // Making private stays safe; sharing waits until the audience is known.
    expect(within(note).getByRole('button', { name: 'Nesdílet' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Sdílet s/ })).toBeNull();
  });

  it('says a failed collection read failed, instead of "nobody", and retries it', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(listCollections).mockRejectedValue(new Error('boom'));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();
    // The share tools sit in the composer, which opens on focus.
    await user.click(await screen.findByRole('textbox', { name: 'Poznámky' }));

    const retry = await screen.findByRole('button', { name: /Sdílení se nepodařilo načíst/ });
    const note = await noteGroup();
    expect(within(note).getByText('Sdíleno')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Sdílet s/ })).toBeNull();

    withAudience();
    await user.click(retry);
    expect(await screen.findByRole('checkbox', { name: 'Sdílet s petr@example.cz' }))
      .toBeInTheDocument();
  });

  it('offers the checkbox, off by default, and saves the mark it was given', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    renderWithReads();

    await user.type(await screen.findByRole('textbox', { name: 'Poznámky' }), 'Makléř slevní.');
    const box = await screen.findByRole('checkbox', { name: 'Sdílet s petr@example.cz' });
    expect(box).not.toBeChecked();
    await user.click(box);
    await user.click(screen.getByRole('button', { name: 'Uložit poznámku' }));
    await waitFor(() =>
      expect(createPropertyNote).toHaveBeenCalledWith(42, sent('Makléř slevní.', true)));
    // Every note needs its own consent: the box is clear for the next note.
    await user.type(screen.getByRole('textbox', { name: 'Poznámky' }), 'Druhá.');
    expect(screen.getByRole('checkbox', { name: 'Sdílet s petr@example.cz' })).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Uložit poznámku' }));
    await waitFor(() =>
      expect(createPropertyNote).toHaveBeenLastCalledWith(42, sent('Druhá.', false)));
  });

  it('marks my shared note and turns the mark off with a PATCH of {shared} alone', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    vi.mocked(updatePropertyNote).mockResolvedValue({ ...NOTE, shared: false } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(await within(note).findByText('Sdíleno')).toBeInTheDocument();
    await user.click(within(note).getByRole('button', { name: 'Nesdílet' }));
    await waitFor(() => expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { shared: false }));
  });

  it('offers to share my private note once there is an audience', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(updatePropertyNote).mockResolvedValue({ ...NOTE, shared: true } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(within(note).queryByText('Sdíleno')).toBeNull();
    // The toggle names who the note will reach; no tooltip carries it.
    await user.click(await within(note).findByRole('button', { name: 'Sdílet s petr@example.cz' }));
    await waitFor(() => expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { shared: true }));
  });

  it('says a shared note reaches nobody when the audience is empty, and keeps the off switch', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(await within(note).findByText('Sdíleno · zatím to nikdo nevidí')).toBeInTheDocument();
    expect(within(note).getByRole('button', { name: 'Nesdílet' })).toBeInTheDocument();
  });

  it('acting as the owner never claims "nobody": the owner\'s audience is unknown under the header (§3.6)', async () => {
    mockReads();
    /* Under X-Act-As the owner's share rows are invisible: the collection reads
     * as shared with nobody although the owner shares it. */
    vi.mocked(listCollections).mockResolvedValue({ data: [{ ...SHARED_COLLECTION, shared_with: [] }], total: 1 });
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    vi.mocked(deletePropertyNote).mockResolvedValue({ deleted: true } as never);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ActingAs share={{ account_id: 'owner-1', email: 'jana@example.cz', mode: 'edit' }}>
            <CurationBlock property_id={42} listing_id={900} />
          </ActingAs>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const note = await noteGroup();
    expect(listPropertyNotes).toHaveBeenCalledWith(42, { actAs: 'owner-1' });
    expect(within(note).getByText('Sdíleno')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    expect(within(note).getByRole('button', { name: 'Nesdílet' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();

    // A note deleted as the owner re-reads the owner's notes, never Browse's note marks.
    fireEvent.click(within(note).getByRole('button', { name: 'Smazat poznámku' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Smazat' }));
    await waitFor(() => expect(deletePropertyNote).toHaveBeenCalledWith(42, 11, { actAs: 'owner-1' }));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: actingKey('owner-1', curationKeys.propertyNotes(42)) }),
    );
    expect(invalidate.mock.calls.some(([f]) => f?.queryKey === curationKeys.noteCounts)).toBe(false);
  });

  it('speaks Czech on the phone: Sdílet s …, Sdíleno', async () => {
    mockReads();
    withAudience();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <PhoneTagsAndNotes property_id={42} listing_id={900} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('checkbox', { name: 'Sdílet s petr@example.cz' }))
      .not.toBeChecked();
    // The row's own label speaks the page's language too.
    const note = await screen.findByRole('article', { name: /^Poznámka z / });
    expect(await within(note).findByText('Sdíleno')).toBeInTheDocument();
    expect(within(note).getByRole('button', { name: 'Nesdílet' })).toBeInTheDocument();
  });

  it('edits the body with a PATCH of {body} alone', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(updatePropertyNote).mockResolvedValue(NOTE as never);
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Upravit poznámku' }));
    const editor = screen.getByRole('textbox', { name: 'Upravit poznámku' });
    await user.clear(editor);
    await user.type(editor, 'Nový text.');
    await user.click(screen.getByRole('button', { name: 'Uložit' }));
    await waitFor(() =>
      expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { body: 'Nový text.' }));
  });
});

/* Ruling 5: a note last written by someone acting as its owner says who and
 * when, in the page's language; the owner's own notes say nothing more. */
describe('<CurationBlock> delegated stamp', () => {
  it('names the delegate on a stamped note and nothing on an unstamped one', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({
      data: [
        { ...NOTE, edited_by_email: 'c@example.cz', edited_at: '2026-10-20T10:00:00Z' },
        { ...NOTE, id: 12, body: 'Moje vlastní.', edited_by_email: null, edited_at: null },
      ],
    } as never);
    renderWithReads();
    expect(await screen.findByText(/^upravil c@example\.cz · /)).toBeInTheDocument();
    expect(screen.getAllByText(/^upravil /)).toHaveLength(1);
  });

  it('speaks Czech on the phone', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({
      data: [{ ...NOTE, edited_by_email: 'c@example.cz', edited_at: '2026-10-20T10:00:00Z' }],
    } as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <PhoneTagsAndNotes property_id={42} listing_id={900} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/^upravil c@example\.cz · /)).toBeInTheDocument();
  });
});

/* Migration 666: a viewing is a note with a date. The composer adds one from
 * the heading (desktop) or its own toolbar (phone), the text optional; the
 * section leads with the property's upcoming viewings under a calendar leaf. */
describe('<CurationBlock> viewings', () => {
  // Anchored at PRAGUE noon: the formatter counts Prague calendar days, so now ± 24 h
  // lands on the wrong day just after midnight or across a DST change (main went red
  // at 00:11 local on the clocks-back night, 2026-10-25), and the CI clock is UTC.
  const noon = Date.parse(slotInstant({ day: viewingSlot(new Date().toISOString()).day, time: '12:00' }));
  const inDays = (n: number) => new Date(noon + n * 86_400_000).toISOString();

  it('books a viewing with no text from the heading, on the Prague clock', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    const { invalidate } = renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Naplánovat prohlídku' }));
    const fields = screen.getByRole('group', { name: 'Termín prohlídky' });
    fireEvent.change(within(fields).getByLabelText('Den prohlídky'), { target: { value: '2030-05-14' } });
    fireEvent.change(within(fields).getByLabelText('Čas prohlídky'), { target: { value: '14:30' } });
    // A viewing's text is optional, and the save says what it saves.
    await user.click(screen.getByRole('button', { name: 'Uložit prohlídku' }));
    await waitFor(() =>
      expect(createPropertyNote).toHaveBeenCalledWith(42, sent('', false, '2030-05-14T12:30:00.000Z')));
    // The Prohlídky tab reads the same notes: it is re-read too.
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.viewings }));
    expect(screen.queryByRole('group', { name: 'Termín prohlídky' })).toBeNull();
  });

  it('drops the date again, and a plain note still needs its text', async () => {
    const user = userEvent.setup();
    renderPhone();
    await user.click(await screen.findByRole('button', { name: 'Prohlídka' }));
    expect(screen.getByRole('button', { name: 'Uložit prohlídku' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Odebrat termín' }));
    expect(screen.queryByRole('group', { name: 'Termín prohlídky' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Uložit poznámku' })).toBeDisabled();
  });

  it('leads with an upcoming viewing under its leaf, a past one keeps its place', async () => {
    mockReads();
    const ahead = inDays(3);
    vi.mocked(listPropertyNotes).mockResolvedValue({
      data: [
        { ...NOTE, id: 20, body: 'Nejnovější poznámka.', created_at: inDays(-1) },
        { ...NOTE, id: 21, body: 'Proběhla, hezké.', viewing_at: inDays(-5), created_at: inDays(-2) },
        { ...NOTE, id: 22, body: '', viewing_at: ahead, created_at: inDays(-3) },
      ],
    } as never);
    renderWithReads();

    const cards = await screen.findAllByRole('article');
    expect(cards[0]).toHaveAccessibleName(`Prohlídka ${viewingWhen(ahead)}`);
    expect(within(cards[0]).getByText(`Prohlídka · ${viewingWhen(ahead)}`)).toBeInTheDocument();
    expect(within(cards[0]).getByText('za 3 dny')).toBeInTheDocument();
    expect(cards[1]).toHaveTextContent('Nejnovější poznámka.');
    expect(within(cards[2]).getByText('proběhla před 5 dny')).toBeInTheDocument();
    expect(within(cards[2]).getByText('Proběhla, hezké.')).toBeInTheDocument();
  });

  it('moves a viewing with a PATCH of {viewing_at} alone, and asks before deleting it as a viewing', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({
      data: [{ ...NOTE, body: 'Vzít metr.', viewing_at: '2030-05-14T12:30:00+00:00' }],
    } as never);
    vi.mocked(updatePropertyNote).mockResolvedValue(NOTE as never);
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Upravit poznámku' }));
    const fields = screen.getByRole('group', { name: 'Termín prohlídky' });
    expect(within(fields).getByLabelText('Čas prohlídky')).toHaveValue('14:30');
    fireEvent.change(within(fields).getByLabelText('Čas prohlídky'), { target: { value: '16:00' } });
    await user.click(screen.getByRole('button', { name: 'Uložit' }));
    await waitFor(() =>
      expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { viewing_at: '2030-05-14T14:00:00.000Z' }));

    await user.click(screen.getByRole('button', { name: 'Smazat poznámku' }));
    expect(screen.getByText('Smazat prohlídku?')).toBeInTheDocument();
  });
});
