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

## A run

`api/agents/loop.py` runs every agent in the same way:

1. It checks the input against `input`.
2. It sends the input to the model as JSON in the opening message.
3. Each tool call goes through `registry.call` under the run's user, and each model call and each tool call is
   one journal step.
4. A run stops when it reaches `max_iterations`, `max_cost_usd` or the wall clock (checked before each model and
   tool call; an answer that arrives late is still checked). A reply that did not end on its tool calls (cut off at
   its limit, or a refusal) also ends the run; nothing in it is executed.
5. Before each model call, the reply's token limit (16,000 at most; it includes the model's thinking) is lowered
   until even the worst case fits the money left. When that leaves fewer than 4,096 tokens, the run stops on
   `max_cost`.

A run ends successfully only on `submit_result`, which carries `markdown`, `cited_property_ids` and `files`.
`proposed_actions` (`{entry, args}`) appears only for an agent that lists a write tool, and `output` only for one
that declares it. The server refuses a submission, and tells the model what to fix, when:

- a cited id was never a `property_id` under `data` in this run's tool results;
- a file was not produced by this run;
- a proposal names no write tool the agent lists, or its arguments fail that tool's schema;
- it shares its turn with another call.

## Running one locally

```
python -m scripts.agent_run skills/<name> --user <auth user id> --input @input.json [--provider openai] [--record run.json]
```

The runner acts as that user on production data, read-only. Each model call is priced into `llm_calls` as
`agent_run`. `--record` writes a cassette: the journal saved, which `tests/api/_agent_replay.py` plays back
through the same loop with no model and no database. A production cassette carries what the user can see,
their own collections and notes included, so keep it out of git while the repository is public.
