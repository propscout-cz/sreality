import Panel, { PanelGroup } from '@/components/Panel';
import Stat from '@/components/Stat';
import { fmtCount } from '@/lib/format';
import {
  AGENTS, ALL_TICKETS, AS_OF, CANT_DO, HOSTED_CODE, isBuilt, isDone, KNOWN_GAPS, LEFT_FOR_LATER, liveToolCount,
  MILESTONES, NEEDS_YOU, PROPOSALS, RUN_NODES, SERVICES, SERVICES_NOTE, TODAY, TOOL_GROUPS, TRADE_OFF, USAGE_GATE,
} from '@/lib/agentPlatformMap';

/* Admin: where the agent platform programme stands (spec #2055), in plain words. Static by design: the content is
 * `lib/agentPlatformMap.ts`, which each ticket's PR updates. Solid means it exists and dashed means it doesn't yet
 * (system.md § Depth); copper marks the one path that writes to the user's data. */

const ISSUES = 'https://github.com/propscout-cz/sreality/issues/';
const L4 = 'text-[0.65rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]';
const TH = `py-1.5 pr-3 font-medium ${L4}`;
const TD = 'py-2 pr-3 align-top';

function TicketLink({ n }: { n: number }) {
  return (
    <a className="font-mono text-[0.78rem] tabular-nums underline underline-offset-2" href={`${ISSUES}${n}`}
      target="_blank" rel="noopener noreferrer">
      #{n}
    </a>
  );
}

function StateChip({ built, children }: { built: boolean; children: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-xs)] border px-1.5 py-px font-mono text-xs [overflow-wrap:anywhere] ${built
      ? 'border-[var(--color-rule-strong)] text-[var(--color-ink)]'
      : 'border-dashed border-[var(--color-rule-strong)] text-[var(--color-ink-3)]'}`}>
      {children}
    </span>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--color-ink-2)]">
      <span className="inline-flex items-center gap-2">
        <i aria-hidden className="block h-3 w-5 rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)]" />
        Built or already running
      </span>
      <span className="inline-flex items-center gap-2">
        <i aria-hidden className="block h-3 w-5 rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)]" />
        Still to build
      </span>
    </div>
  );
}

function Track() {
  const cells = MILESTONES.flatMap((m, i) => {
    const closed = m.tickets.filter((t) => t.done).length;
    const cell = (
      <div key={m.key} className="min-w-0 space-y-1.5">
        <div className="font-mono text-[13px] text-[var(--color-ink)]">{m.key}</div>
        <p className="text-[0.8rem] leading-snug text-[var(--color-ink-2)]">{m.summary}</p>
        <div className="flex flex-wrap gap-[3px]" aria-hidden>
          {m.tickets.map((t) => (
            <i key={t.n} className={`block h-3 w-3 rounded-[2px] ${t.done
              ? 'bg-[var(--color-ink-2)]'
              : 'border border-dashed border-[var(--color-ink-3)]'}`} />
          ))}
        </div>
        <div className="font-mono text-xs tabular-nums text-[var(--color-ink-3)]">{closed} of {m.tickets.length}</div>
      </div>
    );
    if (i !== 2) return [cell];
    return [cell, (
      <div key="gate" className="min-w-0 space-y-1.5">
        <div className="font-mono text-[13px] text-[var(--color-ink)]">Gate</div>
        <p className="text-[0.8rem] leading-snug text-[var(--color-ink-2)]">{USAGE_GATE}</p>
      </div>
    )];
  });
  return <div className="grid grid-cols-2 gap-x-5 gap-y-4 md:grid-cols-3 lg:grid-cols-6">{cells}</div>;
}

const EDGE = 'stroke-current fill-none';
const LABEL = 'font-mono text-[11.5px] fill-[var(--color-ink-3)]';

function RunDiagram() {
  return (
    <figure className="space-y-2">
      <div className="overflow-x-auto rounded-[var(--radius-sm)] border border-[var(--color-rule-soft)] p-2">
        <svg viewBox="0 0 1000 462" role="img" className="block h-auto w-full min-w-[760px] text-[var(--color-ink-2)]"
          aria-label="A run: you start it, it queues, agent-worker claims it, the loop calls models and tools, the result is checked, you review proposals and click Apply, which writes to your data under your login.">
          <defs>
            <marker id="ap-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
            </marker>
          </defs>
          <rect x="290" y="125" width="690" height="220" rx="4" strokeDasharray={isDone(2075) ? undefined : '5 4'}
            className="fill-none stroke-[var(--color-rule-strong)]" />
          <text x="305" y="146" className="text-[12.5px] font-semibold fill-[var(--color-ink-2)]">agent-worker (new service)</text>

          {RUN_NODES.map((node) => (
            <g key={node.title}>
              <rect x={node.x} y={node.y} width={node.w} height="56" rx="4"
                strokeDasharray={isBuilt(node.builtBy) ? undefined : '5 4'}
                className="fill-[var(--color-paper-3)] stroke-[var(--color-rule-strong)]" />
              <text x={node.x + 14} y={node.y + 23} className="text-[13px] font-semibold fill-[var(--color-ink)]">{node.title}</text>
              <text x={node.x + 14} y={node.y + 41} className="text-[11.5px] fill-[var(--color-ink-2)]">{node.sub}</text>
            </g>
          ))}

          <line className={EDGE} x1="220" y1="56" x2="308" y2="56" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="228" y="48">POST /runs</text>
          <text className={LABEL} x="228" y="74">allowlist</text>
          <line className={EDGE} x1="385" y1="84" x2="385" y2="123" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="395" y="109">claims a run</text>
          <line className={EDGE} x1="625" y1="86" x2="625" y2="158" markerStart="url(#ap-arrow)" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="635" y="109">prompt / reply</text>
          <line className={EDGE} x1="712" y1="56" x2="788" y2="56" markerStart="url(#ap-arrow)" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="716" y="48">run_python</text>
          <line className={EDGE} x1="790" y1="188" x2="712" y2="188" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="726" y="180">defines</text>
          <line className={EDGE} x1="540" y1="188" x2="462" y2="188" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="472" y="180">ends run</text>
          <line className={EDGE} x1="310" y1="188" x2="222" y2="188" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="232" y="180">passed</text>
          <line className={EDGE} x1="625" y1="218" x2="625" y2="263" markerStart="url(#ap-arrow)" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="635" y="245">tool call / facts</text>
          <line className={EDGE} x1="385" y1="216" x2="385" y2="388" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="395" y="372">uploads files</text>
          <line className={EDGE} x1="590" y1="321" x2="590" y2="388" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="598" y="378">reads</text>
          <polyline className={EDGE} points="680,321 680,360 755,360 755,388" markerEnd="url(#ap-arrow)" />
          <text className={LABEL} x="765" y="378">max 3 per run</text>

          <g className="text-[var(--color-copper)]">
            <line className={`${EDGE} stroke-[1.8]`} x1="125" y1="216" x2="125" y2="263" markerEnd="url(#ap-arrow)" />
            <line className={`${EDGE} stroke-[1.8]`} x1="125" y1="321" x2="125" y2="388" markerEnd="url(#ap-arrow)" />
          </g>
          <text className={LABEL} x="135" y="244">you choose</text>
          <text className={LABEL} x="135" y="352">writes</text>
          <text className={LABEL} x="135" y="368">Undo reverses</text>
        </svg>
      </div>
      <Legend />
      <figcaption className="max-w-[52rem] text-[0.78rem] leading-relaxed text-[var(--color-ink-3)]">
        The only path that writes to your data is your Apply click, the coloured arrows. The worker reads, computes and proposes.
        The result check is where an invented property or an unregistered action gets refused.
      </figcaption>
    </figure>
  );
}

function ListRows({ rows }: { rows: { head: string; text: string; ticket?: number }[] }) {
  return (
    <ul>
      {rows.map((r) => (
        <li key={r.head} className="border-t border-[var(--color-rule-soft)] py-2 text-sm text-[var(--color-ink-2)] first:border-t-0 first:pt-0">
          <b className="font-semibold text-[var(--color-ink)]">{r.head}</b> {r.text}
          {r.ticket ? <> <TicketLink n={r.ticket} /></> : null}
        </li>
      ))}
    </ul>
  );
}

export default function AgentPlatform() {
  const closed = ALL_TICKETS.filter((t) => t.done).length;
  const running = AGENTS.filter((a) => isBuilt(a.builtBy)).length;
  return (
    <div className="px-6 pt-5 pb-8 max-w-screen-2xl mx-auto">
      <h1 className="text-2xl leading-tight">Agent platform</h1>
      <p className="text-sm text-[var(--color-ink-2)] mt-0.5 max-w-[52rem]">
        One system that can run any number of AI agents over our property data, with the access a signed-in person has.
        A new agent is a new folder of text, with no new code. An agent changes nothing by itself: it finishes with
        proposals, and you click to apply them. State on <span className="font-mono tabular-nums">{AS_OF}</span>; each
        ticket&apos;s PR updates this page.
      </p>

      <div className="mt-4 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          <Stat label="Build tickets closed" value={`${fmtCount(closed)} of ${fmtCount(ALL_TICKETS.length)}`} />
          <Stat label="Tools merged" value={fmtCount(liveToolCount())} />
          <Stat label="Agents in production" value={`${fmtCount(running)} of ${fmtCount(AGENTS.length)}`} />
          <Stat label="New service" value={isDone(2075) ? 'live' : 'not created'} hint="agent-worker" />
        </div>

        <Panel title="Where it stands" lede={TODAY}>
          <Track />
          <p className="text-xs text-[var(--color-ink-3)]">One square per build ticket. Gate tickets are not counted.</p>
        </Panel>

        <Panel title="What happens in one run">
          <RunDiagram />
        </Panel>

        <Panel title="The agents" count={AGENTS.length}
          lede="The set stays open. Later agents are more folders under skills/, using the same tools.">
          <div className="grid gap-x-6 gap-y-5 md:grid-cols-3">
            {AGENTS.map((a) => (
              <div key={a.name} className="min-w-0 space-y-2.5">
                <div>
                  <h3 className="text-sm leading-5 font-semibold text-[var(--color-ink)]">{a.name}</h3>
                  <div className="text-xs text-[var(--color-ink-3)]">{a.when}</div>
                </div>
                <dl className="space-y-2">
                  {a.rows.map((r) => (
                    <div key={r.label}>
                      <dt className={L4}>{r.label}</dt>
                      <dd className="text-sm text-[var(--color-ink-2)]">{r.text}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="What an agent will be able to do" count={liveToolCount()} note="tools merged">
          <table className="w-full text-sm">
            <tbody>
              {TOOL_GROUPS.map((g) => (
                <tr key={g.label} className="border-t border-[var(--color-rule-soft)] first:border-t-0">
                  <th scope="row" className={`${TD} w-44 text-left font-medium text-[var(--color-ink)]`}>{g.label}</th>
                  <td className={TD}>
                    <div className="flex flex-wrap gap-1.5">
                      {g.tools.map((t) => <StateChip key={t.name} built={!t.ticket || isDone(t.ticket)}>{t.name}</StateChip>)}
                    </div>
                  </td>
                </tr>
              ))}
              <tr className="border-t border-[var(--color-rule-soft)]">
                <th scope="row" className={`${TD} w-44 text-left font-medium text-[var(--color-ink)]`}>{HOSTED_CODE.label}</th>
                <td className={TD}><StateChip built={isDone(HOSTED_CODE.ticket)}>{HOSTED_CODE.name}</StateChip></td>
              </tr>
              <tr className="border-t border-[var(--color-rule-soft)]">
                <th scope="row" className={`${TD} w-44 text-left font-medium text-[var(--color-ink)]`}>Propose changes</th>
                <td className={TD}>
                  <div className="flex flex-wrap gap-1.5">
                    {PROPOSALS.map((p) => <StateChip key={p.label} built={isDone(p.ticket)}>{p.label}</StateChip>)}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <Legend />
        </Panel>

        <Panel title="The services" count={SERVICES.length} lede={SERVICES_NOTE}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-sm">
              <thead>
                <tr className="text-left">
                  <th className={TH}>Service</th>
                  <th className={TH}>Its job</th>
                  <th className={TH}>What this programme changes</th>
                  <th className={TH}>State</th>
                </tr>
              </thead>
              <tbody>
                {SERVICES.map((s) => (
                  <tr key={s.name} className="border-t border-[var(--color-rule-soft)]">
                    <td className={`${TD} whitespace-nowrap font-medium text-[var(--color-ink)]`}>{s.name}</td>
                    <td className={`${TD} text-[var(--color-ink-2)]`}>{s.job}</td>
                    <td className={`${TD} text-[var(--color-ink-2)]`}>{s.change}</td>
                    <td className={`${TD} whitespace-nowrap`}><StateChip built={isBuilt(s.builtBy)}>{isBuilt(s.builtBy) ? 'live' : 'not created'}</StateChip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="What an agent can't do">
            <ListRows rows={CANT_DO} />
            <p className="text-xs text-[var(--color-ink-3)]">{TRADE_OFF}</p>
          </Panel>
          <Panel title="What needs you" count={NEEDS_YOU.length}>
            <ListRows rows={NEEDS_YOU} />
            <p className="text-xs text-[var(--color-ink-3)]">Known gaps: {KNOWN_GAPS}</p>
          </Panel>
        </div>

        <Panel title="Every ticket" count={ALL_TICKETS.length} lede={LEFT_FOR_LATER}>
          {MILESTONES.map((m) => (
            <PanelGroup key={m.key} title={`${m.key}: ${m.summary}`} count={`${m.tickets.filter((t) => t.done).length} of ${m.tickets.length}`}>
              <ul>
                {m.tickets.map((t) => (
                  <li key={t.n} className="grid grid-cols-[4rem_minmax(0,1fr)_3rem] gap-2 border-t border-[var(--color-rule-soft)] py-1.5 text-sm text-[var(--color-ink-2)] first:border-t-0">
                    <TicketLink n={t.n} />
                    <span>{t.title}</span>
                    <span className={`text-right text-xs ${t.done ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-3)]'}`}>{t.done ? 'done' : 'open'}</span>
                  </li>
                ))}
              </ul>
            </PanelGroup>
          ))}
        </Panel>
      </div>
    </div>
  );
}
