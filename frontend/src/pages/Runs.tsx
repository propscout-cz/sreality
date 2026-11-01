import type { UseQueryResult } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import ErrorBanner from '@/components/ErrorBanner';
import Panel from '@/components/Panel';
import Stat from '@/components/Stat';
import { RunStateChip } from '@/components/runs/RunParts';
import StartRunForm from '@/components/runs/StartRunForm';
import {
  fmtRunCost, isLive, useAgentRunOptions, useAgentRuns, type AgentRunOptions, type AgentRunRow, type QueueState,
} from '@/lib/agentRuns';
import { fmtAbsolute, fmtCount } from '@/lib/format';
import { ROUTES } from '@/lib/routes';

/* Agent runs: start one, see yours. An operator tool behind an allowlist for now, so it reads in English like the admin
 * pages beside it. The API decides who may start (GET /runs/options, the same gate POST /runs applies), and this page
 * only shows that answer. */

const TH = 'py-1.5 pr-3 font-medium';
const TD = 'py-2 pr-3 align-top';

const QUEUE_WORD: Readonly<Record<QueueState, string>> = { dark: 'Switched off', parked: 'Paused', open: 'Open' };

function StartPanel({ options }: { options: UseQueryResult<AgentRunOptions, Error> }) {
  let body;
  if (options.isPending) {
    body = <p className="text-sm text-[var(--color-ink-3)]">Loading what you can start…</p>;
  } else if (!options.data) {
    body = <ErrorBanner flush title="Could not load the agents:" message={options.error?.message ?? ''} retryLabel="Try again" onRetry={() => void options.refetch()} />;
  } else if (!options.data.start.allowed) {
    body = <p className="text-sm text-[var(--color-ink-2)]">{options.data.start.reason}</p>;
  } else if (options.data.agents.length === 0) {
    body = (
      <p className="text-sm text-[var(--color-ink-2)]">
        No agent is available yet. An agent is a folder in the code (<span className="font-mono text-[0.78rem]">skills/</span>),
        and none has shipped.
      </p>
    );
  } else {
    body = <StartRunForm agents={options.data.agents} queue={options.data.queue} />;
  }
  return <Panel title="Start a run">{body}</Panel>;
}

function RunTable({ rows }: { rows: AgentRunRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[0.65rem] uppercase tracking-[0.1em] text-[var(--color-ink-3)]">
            <th className={TH}>Run</th>
            <th className={TH}>Agent</th>
            <th className={TH}>State</th>
            <th className={`${TH} text-right`}>Cost</th>
            <th className={TH}>Started</th>
            <th className={TH}>Input</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[var(--color-rule-soft)]">
              <td className={TD}>
                <Link className="font-mono tabular-nums underline underline-offset-2" to={ROUTES.runDetail.build({ id: r.id })}>
                  #{r.id}
                </Link>
              </td>
              <td className={`${TD} font-mono text-[0.78rem]`}>{r.agent}</td>
              <td className={`${TD} whitespace-nowrap`}><RunStateChip status={r.status} /></td>
              <td className={`${TD} text-right font-mono tabular-nums`}>{fmtRunCost(r.cost_usd)}</td>
              <td className={`${TD} whitespace-nowrap font-mono tabular-nums text-[0.78rem] text-[var(--color-ink-2)]`}>
                {fmtAbsolute(r.started_at ?? r.created_at)}
              </td>
              <td className={`${TD} max-w-[24rem] truncate font-mono text-[0.72rem] text-[var(--color-ink-3)]`}>
                {JSON.stringify(r.input)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Runs() {
  const runs = useAgentRuns();
  const rows = runs.data?.data ?? [];
  const live = rows.some((r) => isLive(r.status));
  const options = useAgentRunOptions({ live });
  const count = (status: string) => (runs.data ? fmtCount(rows.filter((r) => r.status === status).length) : '…');

  return (
    <div className="mx-auto max-w-screen-2xl px-4 pb-8 pt-5 sm:px-6">
      <h1 className="text-2xl leading-tight">Runs</h1>
      <p className="mt-0.5 text-sm text-[var(--color-ink-2)]">
        Start an agent, follow what it reads, and read its result · refreshes while a run is live
      </p>
      <div className="mt-4 space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Queued" value={count('queued')} />
          <Stat label="Running" value={count('running')} />
          {options.data?.queue && <Stat label="Queue" value={QUEUE_WORD[options.data.queue]} />}
        </div>
        <StartPanel options={options} />
        <Panel title="Your runs" count={runs.data ? rows.length : null}
          note={runs.data?.truncated ? `Showing the newest ${rows.length}` : undefined}>
          {runs.isPending ? (
            <p className="text-sm text-[var(--color-ink-3)]">Loading runs…</p>
          ) : !runs.data ? (
            <ErrorBanner flush title="Could not load your runs:" message={runs.error?.message ?? ''} retryLabel="Try again" onRetry={() => void runs.refetch()} />
          ) : rows.length ? (
            <RunTable rows={rows} />
          ) : (
            <p className="text-sm text-[var(--color-ink-3)]">No runs yet.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
