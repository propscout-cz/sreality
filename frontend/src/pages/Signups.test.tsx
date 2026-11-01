import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { WaitlistResponse, WaitlistSignup } from '@/lib/waitlist';

const row = (over: Partial<WaitlistSignup>): WaitlistSignup => ({
  id: 1, email: 'a@example.cz', created_at: new Date().toISOString(), consent_version: '2026-10-31', page_variant: 'default',
  utm: {}, investor_type: null, note: null, answered_at: null, unsubscribed_at: null, invited_at: null, ...over,
});

const response: WaitlistResponse = {
  truncated: false,
  data: [
    row({ id: 3, email: 'flipper@example.cz', investor_type: 'flip', note: 'Pět portálů denně', answered_at: new Date().toISOString(),
      utm: { utm_source: 'facebook', utm_campaign: 'launch' }, page_variant: 'flip' }),
    row({ id: 2, email: 'landlord@example.cz', utm: { utm_source: 'facebook', utm_campaign: 'launch' } }),
    row({ id: 1, email: 'hejtmanekp+propscout-landing-test@gmail.com', utm: { utm_source: 'claude-test' } }),
  ],
};

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchWaitlistSignups: vi.fn(async () => response),
}));

import Signups from './Signups';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Signups /></QueryClientProvider>);
}

describe('Signups', () => {
  it('lists real sign-ups newest first, counts them, and keeps the test row out until asked', async () => {
    renderPage();
    const people = await screen.findByRole('region', { name: 'People on the waitlist' });
    const emails = (await within(people).findAllByRole('row')).slice(1).map((tr) => within(tr).getAllByRole('cell')[1].textContent);
    expect(emails).toEqual(['flipper@example.cz', 'landlord@example.cz']);
    expect(within(people).getByText('Pět portálů denně')).toBeTruthy();
    expect(within(people).getByText('facebook · launch (flip)')).toBeTruthy();
    expect(screen.getByText('+ 1 test, not counted')).toBeTruthy();

    fireEvent.click(within(people).getByLabelText('Show tests'));
    expect(within(people).getByText('hejtmanekp+propscout-landing-test@gmail.com')).toBeTruthy();
    expect(within(people).getByText('test')).toBeTruthy();
  });

  it('groups where they came from', async () => {
    renderPage();
    const sources = await screen.findByRole('region', { name: 'Where they came from' });
    const cells = (await within(sources).findAllByRole('row')).slice(1).map((tr) => within(tr).getAllByRole('cell').map((c) => c.textContent));
    expect(cells).toEqual([
      ['facebook', 'launch', 'default', '1'],
      ['facebook', 'launch', 'flip', '1'],
    ]);
  });
});
