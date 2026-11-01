import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PanelEmpty, PanelGroup } from '@/components/Panel';
import Stat from '@/components/Stat';
import { TwoStep } from '@/components/TwoStep';
import { SectionLabel } from '@/components/section';
import Timeline from '@/components/estimation/Timeline';
import FilesPanel from '@/components/runs/FilesPanel';
import ProposalsPanel from '@/components/runs/ProposalsPanel';
import { RunJson, RunStateChip } from '@/components/runs/RunParts';
import { ApiError, cancelAgentRun } from '@/lib/api';
import { agentRunKeys, useAgentRunOptions } from '@/lib/agentRunOptions';
import {
  citedIds, fmtRunCost, isLive, stepsByAttempt, toTrace, useAgentRun, useAgentRunSteps, waitingNote, type AgentRun,
  type AgentRunStep,
} from '@/lib/agentRuns';
import { fmtAbsolute, fmtCount } from '@/lib/format';
import { ROUTES } from '@/lib/routes';

/* One run: its state and cost, its steps, its result, what it made and proposed. Everything but the ids is the model's
 * or an ad's words (lib/agentRuns), so it is React text here and nowhere a link: the result is pre-wrapped text, and the
 * only links are the property pages this file builds from the run's cited ids.
 *
 * Files and proposals have their own panels (components/runs): each draws from what the server says. */

function ResultPanel({ run }: { run: AgentRun }) {
  const cited = citedIds(run.cited_property_ids);
  return (
    <Panel title="Result">
      {run.result_markdown ? (
        <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--color-ink)]">{run.result_markdown}</div>
      ) : (
        <PanelEmpty>{isLive(run.status) ? 'No result yet.' : 'This run ended without a result.'}</PanelEmpty>
      )}
      {cited.length > 0 && (
        <PanelGroup title="Cited properties" count={cited.length}>
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {cited.map((id) => (
              <li key={id}>
                <Link className="font-mono text-[0.78rem] tabular-nums underline underline-offset-2" to={ROUTES.property.build({ propertyId: id })}>
                  #{id}
                </Link>
              </li>
            ))}
          </ul>
        </PanelGroup>
      )}
      {run.output != null && (
        <PanelGroup title="Structured output">
          <RunJson value={run.output} />
        </PanelGroup>
      )}
    </Panel>
  );
}

function StepsPanel({ steps }: { steps: UseQueryResult<{ data: AgentRunStep[] }, Error> }) {
  const rows = steps.data?.data;
  const attempts = stepsByAttempt(rows ?? []);
  const retry = () => void steps.refetch();
  return (
    <Panel title="Steps" count={rows ? rows.length : null}>
      {!rows ? (
        steps.isError ? (
          <ErrorBanner flush title="Could not load the steps:" message={steps.error.message} retryLabel="Try again" onRetry={retry} />
        ) : (
          <p className="text-sm text-[var(--color-ink-3)]">Loading the steps…</p>
        )
      ) : attempts.length === 0 ? (
        <PanelEmpty>No steps yet.</PanelEmpty>
      ) : attempts.length === 1 ? (
        <Timeline trace={toTrace(attempts[0][1])} />
      ) : (
        attempts.map(([attempt, group]) => (
          <PanelGroup key={attempt} title={<>Attempt <span className="font-mono tabular-nums">{attempt}</span></>} count={group.length}>
            <Timeline trace={toTrace(group)} />
          </PanelGroup>
        ))
      )}
      {/* After a run ends nothing polls, so a failed last read must say the list may be short and offer the way out. */}
      {rows && steps.isRefetchError && (
        <p className="text-[0.78rem] text-[var(--color-ink-3)]">
          The last refresh failed, so this list may be short.{' '}
          <button type="button" className="underline underline-offset-2" onClick={retry}>Try again</button>
        </p>
      )}
    </Panel>
  );
}

function Recorded({ run }: { run: AgentRun }) {
  const rows: Array<[string, string | null]> = [
    ['Provider', run.provider], ['Model', run.model], ['Stop reason', run.stop_reason],
    ['Agent definition', run.definition_sha], ['Tool set', run.tool_set_sha], ['Commit', run.commit_sha],
    ['Created', fmtAbsolute(run.created_at)], ['Started', fmtAbsolute(run.started_at)], ['Finished', fmtAbsolute(run.finished_at)],
  ];
  return (
    <Panel title="What ran">
      <PanelGroup title="Input">
        <RunJson value={run.input} />
      </PanelGroup>
      <PanelGroup title="Recorded">
        <dl className="grid gap-x-5 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt><SectionLabel>{label}</SectionLabel></dt>
              <dd className="break-all font-mono text-[0.78rem] text-[var(--color-ink-2)]">{value ?? '—'}</dd>
            </div>
          ))}
        </dl>
      </PanelGroup>
    </Panel>
  );
}

function CancelControl({ run }: { run: AgentRun }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const cancel = useMutation({
    mutationFn: () => cancelAgentRun(run.id),
    onSettled: () => {
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: agentRunKeys.all });
    },
  });
  if (!isLive(run.status) || run.cancel_requested_at) return null;
  return (
    <div>
      <TwoStep idle="Cancel run" confirm="Yes, cancel it" cancel="Keep it" confirming={confirming} setConfirming={setConfirming}
        pending={cancel.isPending} onConfirm={() => cancel.mutate()} />
      {cancel.isError && <ErrorBanner flush title="Not cancelled:" message={cancel.error.message} />}
    </div>
  );
}

function RunBody({ run }: { run: AgentRun }) {
  const live = isLive(run.status);
  const steps = useAgentRunSteps(run.id, run.status);
  const { data: options } = useAgentRunOptions({ live });
  const note = waitingNote(run, options?.queue, options?.limit);

  /* The run's last read can land after its last step: fetch the diary once more as a run ends. */
  const { refetch: refetchSteps } = steps;
  const wasLive = useRef(live);
  useEffect(() => {
    if (wasLive.current && !live) void refetchSteps();
    wasLive.current = live;
  }, [live, refetchSteps]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="State" value={<RunStateChip status={run.status} />} />
        <Stat label="Cost" value={fmtRunCost(run.cost_usd)} hint="AI models; Valuo counts in calls" />
        <Stat label="Steps" value={steps.data ? fmtCount(steps.data.data.length) : steps.isError ? '—' : '…'} />
        <Stat label="Attempt" value={fmtCount(run.attempts)} />
      </div>
      {note && <p className="text-sm text-[var(--color-ink-2)]">{note}</p>}
      {run.error && <ErrorBanner flush title="This run failed:" message={run.error} />}
      <ResultPanel run={run} />
      <StepsPanel steps={steps} />
      <FilesPanel run={run} />
      <ProposalsPanel run={run} />
      <Recorded run={run} />
    </div>
  );
}

export default function RunDetail() {
  const { id: rawId } = useParams<{ id: string }>();
  const id = /^\d+$/.test(rawId ?? '') ? Number(rawId) : null;
  const query = useAgentRun(id);
  const missing = id == null || (query.error instanceof ApiError && query.error.status === 404);

  return (
    <div className="mx-auto max-w-screen-2xl px-4 pb-8 pt-5 sm:px-6">
      <Link className="text-[0.78rem] text-[var(--color-ink-3)] underline underline-offset-2" to={ROUTES.runs.build()}>
        ‹ All runs
      </Link>
      <div className="mt-1 mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h1 className="text-2xl leading-tight">
          Run{id != null && <> <span className="font-mono tabular-nums">#{id}</span></>}
          {query.data && <> · <span className="font-mono text-lg">{query.data.agent}</span></>}
        </h1>
        {query.data && <CancelControl run={query.data} />}
      </div>
      {/* A poll that failed leaves the last good read on screen, says so, and tries again by itself. */}
      {query.data ? (
        <>
          {query.isRefetchError && <p className="mb-3 text-[0.78rem] text-[var(--color-ink-3)]">The last refresh failed; this is the last good read.</p>}
          <RunBody run={query.data} />
        </>
      ) : missing ? (
        <p className="text-sm text-[var(--color-ink-2)]">There is no run{id != null ? ` #${id}` : ''} on your account.</p>
      ) : query.isError ? (
        <ErrorBanner flush title="Could not load the run:" message={query.error.message} retryLabel="Try again" onRetry={() => void query.refetch()} />
      ) : (
        <p className="text-sm text-[var(--color-ink-3)]">Loading the run…</p>
      )}
    </div>
  );
}
