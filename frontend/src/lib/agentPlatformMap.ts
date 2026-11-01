/* The Agent platform page's content, one file so a ticket's PR edits one place (docs/agents/issue-tracker.md § How work
 * closes): flip the ticket's `done`, set AS_OF, and reword TODAY if it no longer holds. The counts, the run diagram,
 * the tool chips and the services' states all derive from `done`. tests/api/test_agent_platform_page.py holds the live
 * tools to the registry. */

export const AS_OF = '2026-11-01';

export const TODAY =
  'No agent can be started in production yet. The foundations are merged: the read tools, the agent loop and its ' +
  'result check, the cached photo assessment, hosted Python, and the security fixes. The scout, the Runs page and the agent-worker service don\'t exist yet, and ' +
  'the only agent you can use today is still the old hard-wired estimator.';

export interface Ticket {
  n: number;
  done: boolean;
  title: string;
}

export interface Milestone {
  key: string;
  summary: string;
  tickets: Ticket[];
}

export const MILESTONES: Milestone[] = [
  {
    key: 'M0',
    summary: 'Close security holes, move Railway config',
    tickets: [
      { n: 2058, done: true, title: 'The browser can no longer create or rewrite estimation records' },
      { n: 2059, done: true, title: 'Building routes admin-only, stricter sign-in checks, safe boot' },
      { n: 2060, done: true, title: 'Railway settings for the three services moved into code' },
      { n: 2061, done: false, title: 'Credential rotation: all done except the GitHub tokens' },
    ],
  },
  {
    key: 'M1a',
    summary: 'Tools, loop, photo judgement, Python, the scout tested locally',
    tickets: [
      { n: 2062, done: true, title: 'Tool registry and the market analytics tools' },
      { n: 2063, done: true, title: 'Search, dossier, your own context and brokers, read as the screens read them' },
      { n: 2064, done: true, title: 'Cohort statistics and achieved prices' },
      { n: 2065, done: true, title: 'Agent folders, the generic loop, the result check, a local runner' },
      { n: 2179, done: true, title: 'Models keep their own reply blocks across tool calls' },
      { n: 2066, done: true, title: 'Structured photo assessment with a cache' },
      { n: 2067, done: true, title: 'Valuo as a capped, recorded paid tool' },
      { n: 2068, done: true, title: 'Agent Python on Claude\'s hosted code execution' },
      { n: 2069, done: false, title: 'The scout\'s folder, and your grading of ten shortlists' },
    ],
  },
  {
    key: 'M1b',
    summary: 'Queue, Runs page, Apply and Undo, first production run',
    tickets: [
      { n: 2070, done: true, title: 'Run queue and diary: submit, execute once, cancel' },
      { n: 2071, done: false, title: 'Run routes and the minimal Runs page' },
      { n: 2072, done: false, title: 'Propose, Apply, Undo' },
      { n: 2073, done: false, title: 'Agent files in Supabase Storage' },
      { n: 2074, done: false, title: 'Recorded-run regression and security tests in CI' },
      { n: 2075, done: false, title: 'agent-worker in production and your first scout run' },
    ],
  },
  {
    key: 'M2',
    summary: 'Estimator rebuilt on the same system',
    tickets: [
      { n: 2076, done: false, title: 'Estimator v2, same record and same routes' },
      { n: 2077, done: false, title: 'The estimator\'s richer output and its screens' },
      { n: 2078, done: false, title: 'Delete the old estimation paths' },
    ],
  },
  {
    key: 'M3',
    summary: 'Letter drafter',
    tickets: [{ n: 2079, done: false, title: 'The letter drafter' }],
  },
];

export const USAGE_GATE = 'You rate 7 of 10 shortlists useful, or run scouts weekly. If not, M2 and M3 wait.';

export const ALL_TICKETS = MILESTONES.flatMap((m) => m.tickets);

export const isDone = (n: number): boolean => ALL_TICKETS.some((t) => t.n === n && t.done);

/* Built once every ticket it names is done; none named = it exists already. */
export const isBuilt = (builtBy: readonly number[] = []): boolean => builtBy.every(isDone);

export interface ToolGroup {
  label: string;
  /* `ticket` = not merged until that ticket is done. */
  tools: { name: string; ticket?: number }[];
}

export const TOOL_GROUPS: ToolGroup[] = [
  { label: 'Find properties', tools: [{ name: 'search_properties' }] },
  {
    label: 'Read the market',
    tools: [
      { name: 'aggregate_properties' },
      { name: 'market_evidence' },
      { name: 'analyze_distribution' },
      { name: 'find_distribution_outliers' },
      { name: 'compute_market_velocity' },
      { name: 'compute_listing_velocity' },
    ],
  },
  {
    label: 'Judge value',
    tools: [
      { name: 'find_comparables_relaxed' },
      { name: 'estimate_yield' },
      { name: 'compute_reference_rent' },
      { name: 'valuo_valuation' },
    ],
  },
  {
    label: 'Understand one property',
    tools: [
      { name: 'get_property_dossier' },
      { name: 'describe_neighborhood' },
      { name: 'assess_property_visuals' },
    ],
  },
  { label: 'Know the brokers', tools: [{ name: 'get_property_brokers' }, { name: 'rank_brokers' }] },
  { label: 'See your own work', tools: [{ name: 'get_my_property_context' }] },
];

/* Claude's hosted tool, passed through the model call: never a registry entry. */
export const HOSTED_CODE = { label: 'Build files', name: 'run_python', ticket: 2068 };

export const PROPOSALS: { label: string; ticket: number }[] = [
  { label: 'new collection', ticket: 2072 },
  { label: 'add to collection', ticket: 2072 },
  { label: 'note or viewing', ticket: 2072 },
  { label: 'pipeline card', ticket: 2072 },
  { label: 'watchdog', ticket: 2072 },
  { label: 'draft e-mail', ticket: 2079 },
];

export const liveToolCount = (): number =>
  TOOL_GROUPS.flatMap((g) => g.tools).filter((t) => !t.ticket || isDone(t.ticket)).length;

export interface AgentCard {
  name: string;
  when: string;
  builtBy: number[];
  rows: { label: string; text: string }[];
}

export const AGENTS: AgentCard[] = [
  {
    name: 'Regional scout',
    when: 'M1, first',
    builtBy: [2075],
    rows: [
      { label: 'You give it', text: 'A region (town or district codes) and criteria: price band, size, layout, minimum yield.' },
      { label: 'You get', text: 'A ranked shortlist with a reason per property, an under- or over-priced verdict for each, an optional workbook, and a proposed new collection.' },
      { label: 'Its evidence', text: 'Cohort statistics, prices achieved by ads that left the market after 2–60 days (labelled as a proxy), registered sales, photos, Valuo, city indexes for ranking.' },
      { label: 'Model', text: 'Claude Opus 5.5 to start; gpt-5.6-luna compared where no code is needed.' },
    ],
  },
  {
    name: 'Estimator v2',
    when: 'M2, after the gate',
    builtBy: [2076],
    rows: [
      { label: 'You give it', text: 'One property, from the website or the Chrome extension, as today.' },
      { label: 'You get', text: 'First the same result in the same place, so nothing breaks. Then a value range, confidence, a verdict and an evidence list, with new screens.' },
      { label: 'Its evidence', text: 'The same tools as the scout. City indexes stay out of every price estimate.' },
      { label: 'Removes', text: 'The skills table, the Settings editor and the old estimation paths, after 30 estimations and 7 quiet days.' },
    ],
  },
  {
    name: 'Letter drafter',
    when: 'M3, after the gate',
    builtBy: [2079],
    rows: [
      { label: 'You give it', text: 'A property you want to write to the broker about.' },
      { label: 'You get', text: 'A draft with subject, body and broker, plus an "open in my mail" link. You send it yourself.' },
      { label: 'Its evidence', text: 'The property\'s dossier, its brokers as you see them, your notes and your pipeline card.' },
      { label: 'Model', text: 'A cheap text model (gpt-5.6-luna by default). No tracking of replies yet.' },
    ],
  },
];

export interface ServiceRow {
  name: string;
  job: string;
  change: string;
  builtBy?: number[];
}

export const SERVICES: ServiceRow[] = [
  {
    name: 'agent-worker',
    job: 'Runs queued agent runs: 2 slots, one account can\'t take every slot, daily spending ceiling, off until a setting turns it on.',
    change: 'New Railway service, same image as the API.',
    builtBy: [2075],
  },
  { name: 'API', job: 'Serves the website and holds the tools.', change: 'Security fixes done. Gains run routes, Apply and Undo.' },
  { name: 'Website', job: 'The app you use.', change: 'Gains the Runs page: a form, the step timeline, result, files, proposals.' },
  { name: 'realtime-worker', job: 'Always-on scraping, maintenance and alerts.', change: 'Loses its estimation lane in M2.' },
  { name: 'job-runner', job: 'Scheduled jobs moved off GitHub Actions.', change: 'Separate programme (Actions exit). Shares the Railway config file.' },
];

export const SERVICES_NOTE =
  'Outside services this adds: Claude\'s hosted code execution and Files API, and one Supabase Storage bucket.';

export const CANT_DO: { head: string; text: string }[] = [
  { head: 'Change your data.', text: 'Every write is a proposal. Apply works once, and Undo reverses it.' },
  { head: 'See another account.', text: 'Your own data is read under your login. A file request across accounts returns nothing.' },
  { head: 'Write its own database queries.', text: 'It calls named tools with checked inputs.' },
  { head: 'Invent a property.', text: 'Every cited property must have come from that run\'s tool results.' },
  { head: 'Overspend.', text: 'Cost cap per run, daily ceiling for the platform, at most 10 paid photo assessments per run, Valuo at most 3 per run plus a monthly cap.' },
  { head: 'Start for just anyone.', text: 'Only accounts on the allowlist can start a run.' },
];

export const TRADE_OFF =
  'One trade-off you ruled on: what a code run sends to Claude is processed in the US under Anthropic\'s standard retention, and code agents are Claude-only.';

export const NEEDS_YOU: { head: string; text: string; ticket?: number }[] = [
  { head: 'Rotate the GitHub tokens.', text: 'The last credentials pending.', ticket: 2061 },
  { head: 'Pick the Valuo monthly cap.', text: 'One number, the same for every account. No agent can call Valuo until it is set, and your own clicks on the property page count towards it.' },
  { head: 'Edit the photo facet list.', text: 'Claude drafts version 1.', ticket: 2066 },
  { head: 'Grade 10 scout shortlists.', text: 'This happens before the production path is finished.', ticket: 2069 },
  { head: 'Write the customer-group page, later.', text: 'What makes a property attractive to whom. The scout works without it.' },
];

export const KNOWN_GAPS =
  'The spec promises map counts and overlays as tools, and "a screen capability an agent lacks is a bug". No tool exists, and no ticket builds them yet. Search also lacks Browse\'s broker-scope and market-growth filters (#2172, untriaged). The usage gate has no gate ticket.';

export const LEFT_FOR_LATER =
  'Deliberately left for later: asking you questions mid-run, connecting outside AI clients (MCP), sending or receiving mail, chat, resuming a long run from its last step, and opening agents beyond the allowlist.';

export interface RunNode {
  x: number;
  y: number;
  w: number;
  title: string;
  sub: string;
  builtBy?: number[];
}

export const RUN_NODES: RunNode[] = [
  { x: 30, y: 28, w: 190, title: 'You start a run', sub: 'Runs page: region, criteria', builtBy: [2071] },
  { x: 30, y: 160, w: 190, title: 'You review the result', sub: 'shortlist, files, proposals', builtBy: [2071] },
  { x: 30, y: 265, w: 190, title: 'You click Apply', sub: 'once, under your own login', builtBy: [2072] },
  { x: 30, y: 390, w: 190, title: 'Your collections, notes', sub: 'pipeline cards, watchdogs' },
  { x: 310, y: 28, w: 150, title: 'Run queue', sub: 'first in, first out', builtBy: [2070] },
  { x: 540, y: 28, w: 170, title: 'AI models', sub: 'Claude Opus 5.5, others' },
  { x: 790, y: 28, w: 170, title: 'Hosted Python', sub: 'at Claude: sheets, charts', builtBy: [2068] },
  { x: 310, y: 160, w: 150, title: 'Result check', sub: 'cited ids, proposals' },
  { x: 540, y: 160, w: 170, title: 'One generic loop', sub: 'caps: steps, time, cost' },
  { x: 790, y: 160, w: 170, title: 'Agent folder', sub: 'instructions, tools, limits', builtBy: [2069] },
  { x: 540, y: 265, w: 170, title: 'Tool registry', sub: 'the read tools below' },
  { x: 310, y: 390, w: 150, title: 'File storage', sub: 'Supabase, per account', builtBy: [2073] },
  { x: 500, y: 390, w: 165, title: 'Our database', sub: 'market data + your data' },
  { x: 690, y: 390, w: 130, title: 'Valuo', sub: 'paid valuation', builtBy: [2067] },
];
