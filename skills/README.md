# Agents

Each folder here is one agent. Adding an agent means adding a folder: no Python. The image copies this
directory (`COPY skills/ ./skills/`), `api/agents/definition.py` loads it, and
`tests/api/test_agents_definition.py` fails CI on any folder that would not load. A file beside the folders,
like this README, is not an agent.

## A folder

```
skills/<name>/
  SKILL.md        frontmatter (the agent) + body (the skill: the instructions the model follows)
  references/     optional; the operator's context, appended to the instructions under its file name
```

Nothing else may sit in the folder. Every file must be UTF-8 text. An empty reference file is skipped, so a slot
can wait for its text. The folder's sha covers every file and is recorded with each run.

| Frontmatter key | What it is |
|---|---|
| `name` | the folder's name: lowercase letters, digits and `_` |
| `description` | one line on what the agent does |
| `preferred_model` | `{provider: model}`, one or more of `anthropic`, `gemini`, `openai`, `qwen`, `oss`; every model must be priced in `api/providers/<provider>.py`, or its cost could not be capped |
| `allowed_tools` | registry tools by name (`api/capabilities/`). A read tool is called by the model; a write tool is only ever **proposed** to the user (ADR-0004) |
| `limits` | `max_iterations` (model calls, 1–50), `max_cost_usd` (0–20), `wall_clock_timeout_s` (1–600) |
| `input` | JSON Schema of the run input; `type: object` and `additionalProperties: false` |
| `output` | optional JSON Schema of a structured answer, with the same two rules |
| `code` | optional, `true` to let the agent write and run Python (below); `preferred_model` may then name only `anthropic` |

## A run

`api/agents/loop.py` runs every agent in the same way:

1. It checks the input against `input`.
2. It sends the input to the model as JSON in the opening message.
3. Each tool call goes through `registry.call` under the run's user, and each model call and each tool call is
   one journal step.
4. A run stops when it reaches `max_iterations`, `max_cost_usd` or the wall clock (checked before each model and
   tool call; an answer that arrives late is still checked). A reply that did not end on its tool calls (cut off at
   its limit, a refusal, a full context window) also ends the run, under that name; nothing in it is executed. A
   reply with tool calls goes back to the model as its provider wrote it, thinking included.
5. Before each model call, the reply's token limit (16,000 at most; it includes the model's thinking) is lowered
   until even the worst case fits the money left. When that leaves fewer than 4,096 tokens, the run stops on
   `max_cost`.

A run ends successfully only on `submit_result`, which carries `markdown`, `cited_property_ids` and `files`.
`proposed_actions` (`{entry, args}`) appears only for an agent that lists a write tool, and `output` only for one
that declares it. The server refuses a submission, and tells the model what to fix, when:

- a cited id, or a property named in `output` or in a proposal's arguments (`property_id`, `property_ids`, at any depth),
  was never a `property_id` under `data` in this run's tool results, so an output's shortlist names properties by those keys;
- a file was not produced by this run (the refusal lists those it has);
- a proposal names no write tool the agent lists, or its arguments fail that tool's schema;
- it shares its turn with another call, or with hosted code still waiting to run.

## The photo facets

`photo-facets.md`, beside the folders, is the prompt and the facet list of `assess_property_visuals`, and the operator's
to edit: each `### key` heading is a facet, the rest is the wording. It is one file for all agents, not in any agent's
`references/`, because every agent must see the same judgement (a per-agent file would split the cache) and because
`references/` is appended to an agent's instructions, where it would cost tokens on every model call and tell the agent
how to grade rather than what the grade was. An agent uses it by listing the tool. Saving any edit makes a new prompt
version with no code change, so every property is judged again the next time it is asked about, at about $0.10 each;
batch your edits. CI refuses a heading that is no facet key.

## Code

An agent with `code: true` gets Claude's hosted code execution tool in every model call (ADR-0005). It is a tool of the
model call, not a registry entry: nothing goes through `registry.call`, and an agent without `code` never sees it, on
any provider. `run_python` is the name the design and the admin page give it; the model sees `code_execution`.

- **Files in.** `loop.run(inputs=...)` uploads each through Anthropic's Files API with a one-hour expiry and brings it
  into the container. A runner passes `--file`.
- **Files out.** The model must copy a file to `$OUTPUT_DIR` (the answer tool tells it so) and name it in
  `submit_result`'s `files`. It arrives in the loop as bytes, name and type (`HostedFile`), on the accepted answer. The
  name is cut down to one harmless path segment and the type to a plain media type. At most 20 MiB a file and 10 files a
  run; one refused or failed is a journal line, and the model hears of it when it names the file. A later file of the
  same name replaces the earlier one even when it cannot be fetched, so an answer never carries older bytes than the
  model thinks; when a failure leaves no name to go on, every file held goes.
- **Files stay.** Each file of an accepted answer goes to the private `agent-files` bucket and gets an `agent_artifacts`
  row, in the transaction of the verdict, so a run shows only the attempt that finished (`api/agents/agent_files.py`). A
  file that cannot be stored is marked `Not stored` and the run still succeeds. The Runs page offers a download, through
  the API and never as active content. `work_once` passes no input files: no ticket gives a user a way to hand a run one.
- **Files leave.** When the run ends, however it ends, every file it uploaded or got is deleted from Anthropic. A
  failed delete is a warning naming the file id. Ids are known only from a reply that came back, so a process killed
  mid-run, or a model call that failed after its code ran (a timeout, a 5xx), leaves produced files there until someone
  deletes them: they have no expiry, and no sweep exists. Inputs expire within the hour. The container's own data stays
  up to 30 days under Anthropic's retention, with no delete call.
- **Pauses.** A `pause_turn` reply goes back as it came with its container: another model call, journalled and billed,
  so `max_iterations`, `max_cost_usd` and the wall clock bound it like any other.
- **Budget.** One request can sample the model up to 10 times on its own, so a code call's reply limit is set as if
  each sample paid in full: a code agent needs a far larger `max_cost_usd` than a text one (about $1 buys the first
  call on Opus 5.5). Code output a sample reads is billed as input and cannot be bounded in advance, so one call can
  overshoot; the next call is then refused. The wall clock is checked between calls, not inside one.
- **Cost.** Each call's tokens and container time are one `llm_calls` row, journalled per model step with
  `code_execution_requests`. The API reports no execution time, so container time is an upper bound at list price: the
  whole request's duration at $0.05 an hour, the five-minute minimum on a new container, ignoring the organisation's
  1,550 free hours a month, which no response shows. Anthropic's invoice is the truth for this line.

## Running one locally

```
python -m scripts.agent_run skills/<name> --user <auth user id> --input @input.json [--provider openai] [--record run.json] [--file data.csv] [--out outputs/]
```

The runner acts as that user on production data, read-only. Each model call is priced into `llm_calls` as
`agent_run`. `--record` writes a cassette: the journal saved, which `tests/api/_agent_replay.py` plays back
through the same loop with no model and no database. A cassette saved under `tests/cassettes/` replays on every pull
request, and an agent folder owes at least one there (`tests/api/test_agent_cassettes.py`; the scout, ten). A production
cassette carries what the user can see, their own collections and notes included, so scrub it before it goes in git while
the repository is public: the lane refuses one holding an e-mail address, and nothing checks the rest. It keeps a produced
file's name, type, size and hash, never its bytes; a replay serves a blank file of that size.

`python -m scripts.agent_code_live [--max-usd 2.00]` runs one real code turn under a dollar cap with no agent folder
and no user: a small table goes up, a workbook comes back, and the operator opens it.
