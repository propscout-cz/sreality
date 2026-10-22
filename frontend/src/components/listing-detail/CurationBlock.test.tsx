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

/* Ruling 6 (docs/design/collection-sharing.md): the header's "Uložit do kolekce"
 * is the property page's one collection editor; this block has no second one. */
describe('<CurationBlock> collections', () => {
  it('renders no collection row', async () => {
    renderBlock();
    await screen.findByRole('textbox', { name: 'Notes' });
    expect(screen.queryByText('Collections')).toBeNull();
    expect(screen.queryByRole('link', { name: /Manage/ })).toBeNull();
    // The collection reads feed only the notes' share audience: no save control here.
    expect(screen.queryByRole('button', { name: /kolekce|collection/i })).toBeNull();
  });
});

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

/* MS16: a failed read says so and reads again; it is never drawn as "no notes"
 * or "no tags". */
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
    vi.mocked(uploadAttachment).mockResolvedValue({} as never);
    renderWithReads();
    const box = await screen.findByRole('textbox', { name: 'Notes' });

    const file = pdf();
    dropOn(box, [file]);
    expect(within(screen.getByRole('list', { name: 'Files to attach' })).getByText('plan.pdf'))
      .toBeInTheDocument();
    expect(uploadAttachment).not.toHaveBeenCalled();

    await user.type(box, 'Půdorys od makléře.');
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith(
      42, file, expect.objectContaining({ note_id: 12 })));
    expect(createPropertyNote).toHaveBeenCalledWith(42, 'Půdorys od makléře.', 900, false);
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Files to attach' })).toBeNull());
  });

  it('saves a files-only note under its file names', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    vi.mocked(uploadAttachment).mockResolvedValue({} as never);
    renderWithReads();
    await screen.findByRole('textbox', { name: 'Notes' });

    await user.upload(screen.getAllByTestId('attach-input')[0], pdf());
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(createPropertyNote).toHaveBeenCalledWith(
      42, 'Attached: plan.pdf', 900, false));
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
    dropOn(await screen.findByRole('textbox', { name: 'Notes' }),
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
    await user.type(await screen.findByRole('textbox', { name: 'Artifact link' }), 'https://claude.ai/artifact/abc123');
    await user.click(screen.getByRole('button', { name: 'Save link' }));
    await waitFor(() =>
      expect(setPropertyArtifactLink).toHaveBeenCalledWith(42, 'https://claude.ai/artifact/abc123', { actAs: 'owner-1' }));
    expect(fetchArtifactLinks).toHaveBeenCalledWith({ actAs: 'owner-1' });
    expect(qc.getQueryData(curationKeys.artifactLinks)).toBeUndefined();
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
    vi.mocked(fetchArtifactLinks).mockResolvedValue(new Map([[42, LINK]]));
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
    vi.mocked(fetchArtifactLinks).mockResolvedValue(new Map([[42, LINK]]));
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
    expect(screen.queryByRole('textbox', { name: 'Artifact link' })).not.toBeInTheDocument();
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
  const noteGroup = () => screen.findByRole('group', { name: /^Note/ });

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

    const note = await screen.findByRole('group', { name: /^Note by petr@example\.cz · / });
    expect(within(note).getByText('Sousedi jsou hlučni.')).toBeInTheDocument();
    expect(within(note).getByText('petr@example.cz')).toBeInTheDocument();
    for (const verb of [/Edit note/, /Delete note/, /Attach files/, /Remove plan\.pdf/]) {
      expect(within(note).queryByRole('button', { name: verb })).toBeNull();
    }
    // The file still opens.
    expect(within(note).getByRole('button', { name: 'Download plan.pdf' })).toBeInTheDocument();
    expect(within(note).queryByRole('button', { name: /^Share|Make private/ })).toBeNull();
  });

  it('never takes a file dropped on another author\'s note onto that note', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{
      ...NOTE, id: 12, shared: true, owner_email: 'petr@example.cz',
    }] } as never);
    renderWithReads();

    const note = await screen.findByRole('group', { name: /^Note by petr@example\.cz/ });
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
    expect(await within(note).findByText('Shared · nobody can see it yet')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Share with/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Share/ })).toBeNull();
  });

  it('never says "nobody" while the collection list is still loading', async () => {
    mockReads();
    vi.mocked(listCollections).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(within(note).getByText('Shared')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    // Making private stays safe; sharing waits until the audience is known.
    expect(within(note).getByRole('button', { name: 'Make private' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Share with/ })).toBeNull();
  });

  it('says a failed collection read failed, instead of "nobody", and retries it', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(listCollections).mockRejectedValue(new Error('boom'));
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    const retry = await screen.findByRole('button', { name: /Sdílení se nepodařilo načíst/ });
    const note = await noteGroup();
    expect(within(note).getByText('Shared')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Share with/ })).toBeNull();

    withAudience();
    await user.click(retry);
    expect(await screen.findByRole('checkbox', { name: 'Share with petr@example.cz' }))
      .toBeInTheDocument();
  });

  it('offers the checkbox, off by default, and saves the mark it was given', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(createPropertyNote).mockResolvedValue({ ...NOTE, id: 12 } as never);
    renderWithReads();

    const box = await screen.findByRole('checkbox', { name: 'Share with petr@example.cz' });
    expect(box).not.toBeChecked();
    await user.type(screen.getByRole('textbox', { name: 'Notes' }), 'Makléř slevní.');
    await user.click(box);
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() =>
      expect(createPropertyNote).toHaveBeenCalledWith(42, 'Makléř slevní.', 900, true));
    // Every note needs its own consent: the box clears once the note is saved.
    await waitFor(() => expect(box).not.toBeChecked());
    await user.type(screen.getByRole('textbox', { name: 'Notes' }), 'Druhá.');
    await user.click(screen.getByRole('button', { name: 'Save note' }));
    await waitFor(() =>
      expect(createPropertyNote).toHaveBeenLastCalledWith(42, 'Druhá.', 900, false));
  });

  it('marks my shared note and turns the mark off with a PATCH of {shared} alone', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    vi.mocked(updatePropertyNote).mockResolvedValue({ ...NOTE, shared: false } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(await within(note).findByText('Shared')).toBeInTheDocument();
    await user.click(within(note).getByRole('button', { name: 'Make private' }));
    await waitFor(() => expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { shared: false }));
  });

  it('offers to share my private note once there is an audience', async () => {
    const user = userEvent.setup();
    mockReads();
    withAudience();
    vi.mocked(updatePropertyNote).mockResolvedValue({ ...NOTE, shared: true } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(within(note).queryByText('Shared')).toBeNull();
    // The toggle names who the note will reach; no tooltip carries it.
    await user.click(await within(note).findByRole('button', { name: 'Share with petr@example.cz' }));
    await waitFor(() => expect(updatePropertyNote).toHaveBeenCalledWith(42, 11, { shared: true }));
  });

  it('says a shared note reaches nobody when the audience is empty, and keeps the off switch', async () => {
    mockReads();
    vi.mocked(listPropertyNotes).mockResolvedValue({ data: [{ ...NOTE, shared: true }] } as never);
    renderWithReads();

    const note = await noteGroup();
    expect(await within(note).findByText('Shared · nobody can see it yet')).toBeInTheDocument();
    expect(within(note).getByRole('button', { name: 'Make private' })).toBeInTheDocument();
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
    expect(within(note).getByText('Shared')).toBeInTheDocument();
    expect(within(note).queryByText(/nobody/)).toBeNull();
    expect(within(note).getByRole('button', { name: 'Make private' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();

    // A note deleted as the owner re-reads the owner's notes, never Browse's note marks.
    fireEvent.click(within(note).getByRole('button', { name: 'Delete note' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
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
    const note = await screen.findByRole('group', { name: /^Poznámka z / });
    expect(await within(note).findByText('Sdíleno')).toBeInTheDocument();
    expect(within(note).getByRole('button', { name: 'Nesdílet' })).toBeInTheDocument();
  });

  it('edits the body with a PATCH of {body} alone', async () => {
    const user = userEvent.setup();
    mockReads();
    vi.mocked(updatePropertyNote).mockResolvedValue(NOTE as never);
    renderWithReads();

    await user.click(await screen.findByRole('button', { name: 'Edit note' }));
    const editor = screen.getByRole('textbox', { name: 'Edit note' });
    await user.clear(editor);
    await user.type(editor, 'Nový text.');
    await user.click(screen.getByRole('button', { name: 'Save' }));
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
    expect(await screen.findByText(/^edited by c@example\.cz · /)).toBeInTheDocument();
    expect(screen.getAllByText(/^edited by /)).toHaveLength(1);
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
