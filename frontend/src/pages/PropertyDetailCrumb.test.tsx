/* "Back to browse" on the property page. With a history entry behind it, it
 * steps back to the exact Browse URL. Without one (a card opened in a new tab,
 * a map pin's plain link) it goes to the Browse view this browser last had,
 * not a bare, unfiltered /browse. */
import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Crumb } from './PropertyDetail';
import { writeLastBrowseSearch } from '@/lib/lastBrowseView';

function BrowseStub() {
  const { search } = useLocation();
  return <p>browse page {search}</p>;
}

function renderAt(entries: string[], index: number) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={index}>
      <Routes>
        <Route path="/browse" element={<BrowseStub />} />
        <Route path="/property/:propertyId" element={<Crumb />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => localStorage.clear());

describe('<Crumb> Back to browse', () => {
  it('steps back to the exact Browse URL when a history entry is behind it', async () => {
    writeLastBrowseSearch('?price=1-2');
    renderAt(['/browse?price=3000000-6000000&area=50-80', '/property/7'], 1);
    await userEvent.click(screen.getByRole('button', { name: 'Back to browse' }));
    expect(screen.getByText('browse page ?price=3000000-6000000&area=50-80')).toBeInTheDocument();
  });

  it('lands on the remembered view when the page was opened with no history behind it', async () => {
    writeLastBrowseSearch('?price=3000000-6000000&area=50-80');
    renderAt(['/property/7'], 0);
    const link = screen.getByRole('link', { name: 'Back to browse' });
    expect(link).toHaveAttribute('href', '/browse?price=3000000-6000000&area=50-80');
    await userEvent.click(link);
    expect(screen.getByText('browse page ?price=3000000-6000000&area=50-80')).toBeInTheDocument();
  });

  it('is the bare Browse page when no view was ever remembered', () => {
    renderAt(['/property/7'], 0);
    expect(screen.getByRole('link', { name: 'Back to browse' })).toHaveAttribute('href', '/browse');
  });
});
