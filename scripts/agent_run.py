"""Run one agent folder as a signed-in user against production data, read-only, and optionally record a cassette.

Usage:
    python -m scripts.agent_run skills/<agent> --user <auth user id> --input '{"...": ...}'
    python -m scripts.agent_run skills/<agent> --user <auth user id> --input @input.json --record run.json
    python -m scripts.agent_run skills/<agent> --user <auth user id> --input '{}' --file data.csv --out outputs/

Needs SUPABASE_DB_URL (the market tools, and the llm_calls row every model call writes as 'agent_run'),
TENANT_POOL_DB_URL (the user's own data, under their login) and the provider's API key. The loop calls no write
tool: what the agent proposes is printed, never applied. An agent that allows code takes `--file` inputs and writes
the files it answers with to `--out` (default: they are only listed). A cassette holds everything the run read, the user's own
collections and notes included: keep a production cassette out of git while the repository is public.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from api import dependencies
from api.agents import cassette, hosted_code, loop
from api.agents.definition import DefinitionError, InputError, load
from api.capabilities import registry
from api.capabilities.entry import Caller
from api.llm_client import LLMClient
from scraper import db


def _input(raw: str) -> Any:
    return json.loads(Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="the agent folder, e.g. skills/<agent>")
    parser.add_argument("--user", required=True, type=uuid.UUID, help="the auth user id the run acts as")
    parser.add_argument("--input", required=True, help="the run input as JSON, or @file")
    parser.add_argument("--provider", help="default: the first provider the agent names")
    parser.add_argument("--record", type=Path, help="write the run's cassette to this file")
    parser.add_argument("--file", type=Path, action="append", default=[], help="an input file for a code agent; repeatable")
    parser.add_argument("--out", type=Path, help="write the files the answer carries into this folder")
    opts = parser.parse_args(argv)

    try:
        definition = load(opts.folder)
        value = _input(opts.input)
        definition.check_input(value)
        if opts.file and not definition.code:
            raise DefinitionError(f"{definition.name} does not allow code, so it takes no --file")
        inputs = [hosted_code.read_input(path) for path in opts.file]
    except (DefinitionError, InputError, json.JSONDecodeError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    provider = opts.provider or next(iter(definition.models))
    if provider not in definition.models:
        print(f"{definition.name} runs on {sorted(definition.models)}, not {provider}", file=sys.stderr)
        return 2
    model = definition.models[provider]
    providers = dependencies.get_providers()
    steps: list[loop.Step] = []
    outcome: loop.Outcome | None = None

    def record(step: loop.Step) -> None:
        steps.append(step)
        print(f"#{step.n} {step.kind} {step.name} {step.duration_ms} ms ${step.cost_usd:.4f}", file=sys.stderr)

    try:
        with db.connect() as conn:
            caller = Caller(market=conn, claims={"sub": str(opts.user), "role": "authenticated"})
            llm = LLMClient(conn, providers=providers)
            outcome = loop.run(definition, value, provider=provider, llm=llm, record=record,
                               call_tool=lambda name, args: registry.call(caller, name, args), inputs=inputs)
    finally:
        # A run that raised (a provider error) keeps the steps it paid for; its cassette has no outcome.
        if opts.record and steps:
            cassette.dump(opts.record, definition=definition, provider=provider, model=model,
                          price=providers[provider].price_for(model), value=value, steps=steps, outcome=outcome)
    print(f"{outcome.stop_reason} after {outcome.model_calls} model calls, ${outcome.cost_usd:.4f}", file=sys.stderr)
    if outcome.answer is None:
        return 1
    if opts.out:
        for path in hosted_code.save(outcome.answer.files, opts.out):
            print(f"wrote {path}", file=sys.stderr)
    answer = cassette.plain_outcome(outcome)["answer"]
    print(answer.pop("markdown"))
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
