import { useState } from 'react';
import { Link } from 'react-router-dom';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PANEL_ACTION, PanelEmpty, PanelGroup } from '@/components/Panel';
import { TwoStep } from '@/components/TwoStep';
import { SectionLabel } from '@/components/section';
import { agentRunKeys } from '@/lib/agentRunOptions';
import { useAgentRunProposals, type AgentProposal, type AgentRun } from '@/lib/agentRuns';
import { applyAgentProposal, undoAgentProposal } from '@/lib/api';
import { tableReads } from '@/lib/curationSync';
import { fmtAbsolute } from '@/lib/format';
import { watchdogKeys } from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';

/* A run's proposals, each drawn by the server from our own data (api/agents/proposals.py), with Apply and Undo. The model's own
 * words arrive only in `agent_text`, are marked as its, and are React text: never markdown, never a link. The only links are the
 * property pages built here from the server's ids.
 *
 * No patch on these writes: what Apply wrote is the server's to say, so a proposal changes when its re-read lands. */

type Verb = 'apply' | 'undo';
interface ProposalWrite {
  index: number;
  verb: Verb;
}

const WRITE_KEY = ['write', 'agent-proposals', 'apply-undo'] as const;
const WRITES: Record<Verb, (id: number, index: number) => Promise<AgentProposal>> = { apply: applyAgentProposal, undo: undoAgentProposal };
const FAILED: Record<Verb, string> = { apply: 'Not applied:', undo: 'Not undone:' };
// What an Apply or Undo can touch: the curation it wrote, and the dismissal a card's write lifts or restores.
const CURATED = ['collections', 'collection_properties', 'property_notes', 'property_pipeline', 'property_dismissals'] as const;

function Lines({ label, lines }: { label: string; lines: string[] }) {
  return (
    <div className="min-w-0">
      <SectionLabel>{label}</SectionLabel>
      {lines.map((line) => (
        <p key={line} className="text-sm leading-relaxed text-[var(--color-ink-2)]">{line}</p>
      ))}
    </div>
  );
}

function AgentWords({ words }: { words: AgentProposal['agent_text'] }) {
  if (words.length === 0) return null;
  return (
    <div className="space-y-1.5 border-l-2 border-[var(--color-rule-strong)] pl-3">
      <SectionLabel>Written by the agent</SectionLabel>
      {words.map(({ label, text }) => (
        <div key={label} className="min-w-0">
          <p className="text-[0.78rem] text-[var(--color-ink-3)]">{label}</p>
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--color-ink)]">{text}</p>
        </div>
      ))}
    </div>
  );
}

function StateChip({ proposal }: { proposal: AgentProposal }) {
  if (proposal.state === 'open') return null;
  const applied = proposal.state === 'applied';
  const at = applied ? proposal.applied_at : proposal.undone_at;
  return (
    <span className="text-[0.78rem] text-[var(--color-ink-3)]">
      <span className={`mr-2 inline-block rounded-[var(--radius-xs)] px-2 py-0.5 font-medium text-[var(--color-ink)] ${
        applied ? 'bg-[var(--color-sage-soft)]' : 'border border-[var(--color-rule-strong)]'}`}>
        {applied ? 'Applied' : 'Undone'}
      </span>
      {at && fmtAbsolute(at)}
    </span>
  );
}

function UndoControl({ proposal, pending, onUndo }: { proposal: AgentProposal; pending: boolean; onUndo: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const says = proposal.undo?.says;
  if (!proposal.undo?.available) {
    return says ? <p className="text-[0.78rem] text-[var(--color-ink-3)]">{says}</p> : null;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {/* Rule 22: taking a card off the board goes behind the one confirm step, never a toggle. */}
      {proposal.undo_confirm ? (
        <TwoStep idle="Undo" confirm="Yes, take the card off" cancel="Keep it" confirming={confirming} setConfirming={setConfirming}
          pending={pending} onConfirm={onUndo} />
      ) : (
        <button type="button" className={PANEL_ACTION} disabled={pending} onClick={onUndo}>{pending ? 'Undoing…' : 'Undo'}</button>
      )}
      {says && <span className="text-[0.78rem] text-[var(--color-ink-3)]">{says}</span>}
    </div>
  );
}

function ProposalBlock({ proposal, pending, failed, onWrite }: {
  proposal: AgentProposal;
  pending: boolean;
  failed: { verb: Verb; message: string } | undefined;
  onWrite: (verb: Verb) => void;
}) {
  const { properties, filters } = proposal;
  return (
    <PanelGroup title={proposal.title} accessory={<StateChip proposal={proposal} />}>
      {proposal.refused ? (
        <p className="text-sm text-[var(--color-ink-2)]">This can’t be applied: {proposal.refused}</p>
      ) : (
        <div className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
          <Lines label="Before" lines={proposal.before} />
          <Lines label="After" lines={proposal.after} />
        </div>
      )}
      <AgentWords words={proposal.agent_text} />
      {filters.length > 0 && (
        <dl className="grid gap-x-5 gap-y-1 sm:grid-cols-2">
          {filters.map(({ label, value }) => (
            <div key={`${label}:${value}`} className="flex min-w-0 gap-2 text-[0.78rem]">
              <dt className="shrink-0 text-[var(--color-ink-3)]">{label}</dt>
              <dd className="min-w-0 break-words text-[var(--color-ink-2)]">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {properties.length > 0 && (
        <ul className="space-y-1 text-[0.78rem] text-[var(--color-ink-2)]">
          {properties.map(({ property_id: id, title }) => (
            <li key={id ?? title} className="break-words">
              {id != null ? <Link className="underline underline-offset-2" to={ROUTES.property.build({ propertyId: id })}>{title}</Link> : title}
            </li>
          ))}
        </ul>
      )}
      {proposal.answer && <p className="text-sm text-[var(--color-ink)]">{proposal.answer}</p>}
      {proposal.state === 'open' && !proposal.refused && (
        <button type="button" className={PANEL_ACTION} disabled={pending} onClick={() => onWrite('apply')}>
          {pending ? 'Applying…' : 'Apply'}
        </button>
      )}
      {proposal.state === 'applied' && <UndoControl proposal={proposal} pending={pending} onUndo={() => onWrite('undo')} />}
      {failed && <ErrorBanner flush title={FAILED[failed.verb]} message={failed.message} />}
    </PanelGroup>
  );
}

export default function ProposalsPanel({ run }: { run: AgentRun }) {
  const count = run.proposals.length;
  const proposals = useAgentRunProposals(run.id, count > 0);
  const [failed, setFailed] = useState<Record<number, { verb: Verb; message: string }>>({});
  const write = useOptimisticWrite<ProposalWrite, AgentProposal>({
    mutationKey: WRITE_KEY,
    mutationFn: ({ index, verb }) => WRITES[verb](run.id, index),
    revalidate: [agentRunKeys.proposals(run.id), ...tableReads(CURATED, null), watchdogKeys.subscriptions],
    pendingKey: ({ index }) => index,
    onSuccess: (_, { index }) => setFailed(({ [index]: _settled, ...rest }) => rest),
    onError: (error, { index, verb }) => setFailed((now) => ({ ...now, [index]: { verb, message: error.message } })),
  });
  const rows = proposals.data?.data;
  return (
    <Panel title="Proposals" count={count} note={count ? 'Nothing changes until you apply it' : undefined}>
      {count === 0 ? (
        <PanelEmpty>No proposals.</PanelEmpty>
      ) : !rows ? (
        proposals.isError ? (
          <ErrorBanner flush title="Could not load the proposals:" message={proposals.error.message} retryLabel="Try again"
            onRetry={() => void proposals.refetch()} />
        ) : (
          <p className="text-sm text-[var(--color-ink-3)]">Loading the proposals…</p>
        )
      ) : (
        rows.map((proposal) => (
          <ProposalBlock key={proposal.index} proposal={proposal} pending={write.pendingFor(proposal.index)}
            failed={failed[proposal.index]} onWrite={(verb) => write.mutate({ index: proposal.index, verb })} />
        ))
      )}
    </Panel>
  );
}
