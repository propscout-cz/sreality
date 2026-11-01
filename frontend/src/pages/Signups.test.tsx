import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { WaitlistResponse, WaitlistSignup } from '@/lib/waitlist';

const row = (over: Partial<WaitlistSignup>): WaitlistSignup => ({
  id: 1, email: 'a@example.cz', created_at: new Date().toISOString(), consent_version: '2026-10-31', page_variant: 'default',
  utm: {}, investor_type: null, note: null, answered_at: null, unsubscribed_at: null, invited_at: null, has_account: false, ...over,
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
  inviteWaitlistEntries: vi.fn(async (ids: number[]) => ({ sent: ids, skipped: [] })),
}));

import Signups from './Signups';
import { inviteWaitlistEntries } from '@/lib/api';
import { entryState, inviteSummary, nextToInvite } from '@/lib/waitlist';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Signups /></QueryClientProvider>);
}

describe('Signups', () => {
  it('lists real sign-ups newest first, counts them, and keeps the test row out until asked', async () => {
    renderPage();
    const people = await screen.findByRole('region', { name: 'People on the waitlist' });
    const emails = (await within(people).findAllByRole('row')).slice(1).map((tr) => within(tr).getAllByRole('cell')[1].querySelector('span')?.textContent);
    expect(emails).toEqual(['flipper@example.cz', 'landlord@example.cz']);
    expect(within(people).getByText('Pět portálů denně')).toBeTruthy();
    expect(within(people).getByText('facebook · launch (flip)')).toBeTruthy();
    expect(screen.getByText('+ 1 test, not counted')).toBeTruthy();

    fireEvent.click(within(people).getByLabelText('Show tests'));
    expect(within(people).getByText('hejtmanekp+propscout-landing-test@gmail.com')).toBeTruthy();
    expect(within(people).getByText('test')).toBeTruthy();
  });

  it('invites one entry from its row', async () => {
    vi.mocked(inviteWaitlistEntries).mockClear();
    renderPage();
    const people = await screen.findByRole('region', { name: 'People on the waitlist' });
    const row = (await within(people).findByText('landlord@example.cz')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Invite' }));
    await waitFor(() => expect(vi.mocked(inviteWaitlistEntries).mock.calls[0][0]).toEqual([2]));
  });

  it('asks before a batch, then sends the oldest real entries first', async () => {
    vi.mocked(inviteWaitlistEntries).mockClear();
    renderPage();
    const people = await screen.findByRole('region', { name: 'People on the waitlist' });
    fireEvent.click(await within(people).findByRole('button', { name: 'Invite next 2' }));
    expect(inviteWaitlistEntries).not.toHaveBeenCalled();
    fireEvent.click(within(people).getByRole('button', { name: 'Send 2 invitations' }));
    await waitFor(() => expect(vi.mocked(inviteWaitlistEntries).mock.calls[0][0]).toHaveLength(2));
  });

  it('knows each state, and only a waiting real entry is next', () => {
    const now = new Date().toISOString();
    expect(entryState(row({}))).toBe('waiting');
    expect(entryState(row({ invited_at: now }))).toBe('invited');
    expect(entryState(row({ invited_at: now, has_account: true }))).toBe('account');
    expect(entryState(row({ has_account: true, unsubscribed_at: now }))).toBe('unsubscribed');
    const rows = [
      row({ id: 4, created_at: '2026-11-04T00:00:00Z' }),
      row({ id: 3, created_at: '2026-11-03T00:00:00Z', invited_at: now }),
      row({ id: 2, created_at: '2026-11-02T00:00:00Z', utm: { utm_source: 'claude-test' } }),
      row({ id: 1, created_at: '2026-11-01T00:00:00Z' }),
    ];
    expect(nextToInvite(rows, 5)).toEqual([1, 4]);
    expect(nextToInvite(rows, 1)).toEqual([1]);
  });

  it('says what a send did', () => {
    expect(inviteSummary({ sent: [1, 2], skipped: [{ id: 3, reason: 'cap' }, { id: 4, reason: 'cap' }, { id: 5, reason: 'failed' }] }))
      .toBe("Sent 2 · 2 over today's limit · 1 failed to send");
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
