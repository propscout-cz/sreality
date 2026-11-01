import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchWaitlistSignups, inviteWaitlistEntries } from '@/lib/api';
import { fmtAbsolute, fmtCount } from '@/lib/format';
import { pushToast } from '@/lib/toast';
import {
  bySource, cameFrom, entryState, INVESTOR_LABEL, inviteSummary, isTest, nextToInvite, STATE_LABEL, summarize, toCsv,
  type WaitlistSignup,
} from '@/lib/waitlist';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PANEL_ACTION } from '@/components/Panel';
import Stat from '@/components/Stat';

/* Admin: the propscout.cz waitlist (GET /admin/waitlist; migration 700). Who joined from the landing page, newest
 * first, with the thank-you page's answer and the ad they came from. Test entries (utm_source containing "test") are
 * listed and marked, never counted. Sending a waitlist invitation (POST /admin/waitlist/invite) is the page's one
 * write. E-mails are personal data: the export is the operator's own download. */

const TH = 'py-1.5 pr-3 font-medium';
const TD = 'py-2 pr-3 align-top';

export default function Signups() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin', 'waitlist'],
    queryFn: fetchWaitlistSignups,
    refetchInterval: 60_000,
  });
  const rows = useMemo(() => data?.data ?? [], [data]);
  const sum = useMemo(() => summarize(rows), [rows]);
  const sources = useMemo(() => bySource(rows), [rows]);
  const [showTests, setShowTests] = useState(false);
  const shown = showTests ? rows : rows.filter((r) => !isTest(r));
  const client = useQueryClient();
  const invite = useMutation({
    mutationFn: inviteWaitlistEntries,
    onSuccess: (result) => {
      pushToast(result.sent.length ? 'ok' : 'err', inviteSummary(result));
      void client.invalidateQueries({ queryKey: ['admin', 'waitlist'] });
    },
    onError: (err) => pushToast('err', (err as Error).message),
  });
  const [batch, setBatch] = useState(20);
  const [confirming, setConfirming] = useState(false);
  const next = useMemo(() => nextToInvite(rows, batch), [rows, batch]);

  const copyEmails = () => {
    const emails = rows.filter((r) => !isTest(r) && !r.unsubscribed_at).map((r) => r.email).join('\n');
    navigator.clipboard.writeText(emails).then(
      () => pushToast('ok', `Copied ${fmtCount(sum.total)} e-mails`),
      () => pushToast('err', 'Copy was blocked by the browser'),
    );
  };
  const downloadCsv = () => {
    const blob = new Blob(['﻿' + toCsv(rows)], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `propscout-waitlist-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const pct = (n: number) => (sum.total ? `${Math.round((100 * n) / sum.total)} %` : undefined);
  const value = (n: number) => (isLoading ? '…' : fmtCount(n));

  return (
    <div className="px-6 pt-5 pb-8 max-w-screen-2xl mx-auto">
      <h1 className="text-2xl leading-tight">Waitlist</h1>
      <p className="text-sm text-[var(--color-ink-2)] mt-0.5">
        The waitlist from{' '}
        <a className="underline underline-offset-2" href="https://propscout.cz" target="_blank" rel="noopener noreferrer">
          propscout.cz
        </a>
        , newest first · refreshes every minute
      </p>
      {error ? <ErrorBanner title="Failed to load the waitlist:" message={(error as Error).message} /> : null}

      <div className="mt-4 space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <Stat label="Entries" value={value(sum.total)} hint={sum.tests ? `+ ${sum.tests} test, not counted` : undefined} />
          <Stat label="Last 7 days" value={value(sum.last7)} />
          <Stat label="Answered the question" value={value(sum.answered)} hint={pct(sum.answered)} />
          <Stat label="Flip" value={value(sum.byType.flip + sum.byType.both)} hint={sum.byType.both ? `incl. ${sum.byType.both} both` : undefined} />
          <Stat label="Pronájem" value={value(sum.byType.rent + sum.byType.both)} hint={sum.byType.both ? `incl. ${sum.byType.both} both` : undefined} />
          <Stat label="Teprve začíná" value={value(sum.byType.starting)} />
        </div>

        <Panel
          title="People on the waitlist"
          count={isLoading ? null : sum.total}
          note={data?.truncated ? 'Showing the newest 5,000' : undefined}
          accessory={
            <>
              <label className="inline-flex items-center gap-1.5 text-[0.8rem] text-[var(--color-ink-2)]">
                <input type="checkbox" checked={showTests} onChange={(e) => setShowTests(e.target.checked)} />
                Show tests
              </label>
              <label className="inline-flex items-center gap-1.5 text-[0.8rem] text-[var(--color-ink-2)]">
                Next
                <input
                  type="number" min={1} max={200} value={batch} aria-label="How many to invite"
                  className="w-16 rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] bg-transparent px-1.5 py-0.5"
                  onChange={(e) => { setBatch(Math.min(200, Math.max(1, Number(e.target.value) || 1))); setConfirming(false); }}
                />
              </label>
              {confirming ? (
                <>
                  <button
                    type="button" className={PANEL_ACTION} disabled={invite.isPending}
                    onClick={() => { setConfirming(false); invite.mutate(next); }}
                  >
                    Send {fmtCount(next.length)} invitations
                  </button>
                  <button type="button" className={PANEL_ACTION} onClick={() => setConfirming(false)}>Cancel</button>
                </>
              ) : (
                <button
                  type="button" className={PANEL_ACTION} disabled={!next.length || invite.isPending}
                  onClick={() => setConfirming(true)}
                >
                  Invite next {fmtCount(next.length)}
                </button>
              )}
              <button type="button" className={PANEL_ACTION} onClick={copyEmails} disabled={!sum.total}>
                Copy e-mails
              </button>
              <button type="button" className={PANEL_ACTION} onClick={downloadCsv} disabled={!rows.length}>
                Download CSV
              </button>
            </>
          }
        >
          {isLoading ? (
            <p className="text-sm text-[var(--color-ink-3)]">Loading the waitlist…</p>
          ) : shown.length ? (
            <SignupTable rows={shown} busy={invite.isPending} onInvite={(id) => invite.mutate([id])} />
          ) : (
            <p className="text-sm text-[var(--color-ink-3)]">
              Nobody yet{sum.tests && !showTests ? ` (${sum.tests} test hidden)` : ''}.
            </p>
          )}
        </Panel>

        <Panel title="Where they came from" count={isLoading ? null : sources.length}
          lede="Each ad link carries utm tags; the headline variant is the ?v= the ad opened (flip, najem) or default.">
          {sources.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
                    <th className={TH}>Source</th>
                    <th className={TH}>Campaign</th>
                    <th className={TH}>Headline</th>
                    <th className={`${TH} text-right`}>Sign-ups</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((s) => (
                    <tr key={`${s.source}|${s.campaign}|${s.variant}`} className="border-t border-[var(--color-rule-soft)]">
                      <td className={TD}>{s.source}</td>
                      <td className={TD}>{s.campaign}</td>
                      <td className={TD}>{s.variant}</td>
                      <td className={`${TD} text-right font-mono tabular-nums`}>{fmtCount(s.count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-[var(--color-ink-3)]">{isLoading ? 'Loading…' : 'Nothing yet.'}</p>
          )}
        </Panel>
      </div>
    </div>
  );
}

function SignupTable({ rows, busy, onInvite }: { rows: WaitlistSignup[]; busy: boolean; onInvite: (id: number) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
            <th className={TH}>Joined</th>
            <th className={TH}>E-mail</th>
            <th className={TH}>Invests</th>
            <th className={TH}>What slows them down</th>
            <th className={TH}>Came from</th>
            <th className={TH}>Invitation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[var(--color-rule-soft)]">
              <td className={`${TD} whitespace-nowrap font-mono tabular-nums text-[0.78rem] text-[var(--color-ink-2)]`}>
                {fmtAbsolute(r.created_at)}
              </td>
              <td className={TD}>
                <span className="break-all">{r.email}</span>
                {isTest(r) ? <Chip>test</Chip> : null}
                <Chip>{STATE_LABEL[entryState(r)]}</Chip>
              </td>
              <td className={`${TD} whitespace-nowrap`}>
                {r.investor_type ? INVESTOR_LABEL[r.investor_type] : <span className="text-[var(--color-ink-3)]">—</span>}
              </td>
              <td className={`${TD} max-w-[28rem] text-[var(--color-ink-2)]`}>
                {r.note ? r.note : <span className="text-[var(--color-ink-3)]">—</span>}
              </td>
              <td className={`${TD} text-[0.78rem] text-[var(--color-ink-2)]`}>{cameFrom(r)}</td>
              <td className={`${TD} whitespace-nowrap`}>
                {entryState(r) === 'waiting' || entryState(r) === 'invited' ? (
                  <button type="button" className={PANEL_ACTION} disabled={busy} onClick={() => onInvite(r.id)}>
                    {entryState(r) === 'waiting' ? 'Invite' : 'Send again'}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Chip({ children }: { children: string }) {
  return (
    <span className="ml-2 inline-block rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1.5 py-px align-middle font-mono text-[0.65rem] uppercase tracking-[0.06em] text-[var(--color-ink-3)]">
      {children}
    </span>
  );
}
