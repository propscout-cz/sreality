"""One real hosted-code turn under a dollar cap: an input goes up, a workbook comes back, the operator opens it.

Usage:
    python -m scripts.agent_code_live [--max-usd 2.00] [--model claude-opus-5-5] [--out agent-code-live/]

Needs ANTHROPIC_API_KEY and SUPABASE_DB_URL (each model call writes an llm_calls row as 'agent_run'). It reads no
listing and acts as no user: its one input is the small table below. Below about $1.00 on Opus 5.5 the run stops on
`max_cost` before spending anything, because the loop budgets a code call for every sample of its server-side loop.
Exit 0: the run ended on submit_result with the workbook in hand; open it and check the total printed last.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api import dependencies
from api.agents import hosted_code, loop
from api.agents.definition import Definition, DefinitionError, Limits
from api.llm_client import LLMClient
from api.providers import HostedFile, ProviderError
from scraper import db

MODEL = "claude-opus-5-5"
WORKBOOK = "totals.xlsx"
RENTS = {"Brno-střed": 18_500, "Praha 7": 31_000, "Ostrava": 9_900}
EXPECTED_TOTAL = sum(RENTS.values())
INPUT = HostedFile(name="rents.csv", media_type="text/csv",
                   data=("town,rent_czk\n" + "".join(f"{t},{r}\n" for t, r in RENTS.items())).encode())
INSTRUCTIONS = f"""You check that hosted code works. Do exactly this, using the code execution tool:
1. Read rents.csv from your container.
2. With openpyxl, write {WORKBOOK}: a header row, one row per town, and a last row named Total with the sum of rent_czk.
3. Copy {WORKBOOK} to $OUTPUT_DIR and list that directory, in one command.
4. Call submit_result with markdown stating the total, and files: ["{WORKBOOK}"]. Call nothing else in that turn."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-usd", type=float, default=2.0, help="the run's cost cap (default: 2.00; at most 5.00)")
    parser.add_argument("--model", default=MODEL, help=f"a Claude model (default: {MODEL})")
    parser.add_argument("--out", type=Path, default=Path("agent-code-live"), help="the folder the workbook lands in")
    opts = parser.parse_args(argv)
    if not 0 < opts.max_usd <= 5:
        print("--max-usd must be above 0 and at most 5", file=sys.stderr)
        return 2

    definition = Definition(
        name="hosted_code_live_check", description="One real code turn.", instructions=INSTRUCTIONS, tools=(),
        models={"anthropic": opts.model}, limits=Limits(max_iterations=6, max_cost_usd=opts.max_usd, wall_clock_timeout_s=300.0),
        input_schema={"type": "object", "additionalProperties": False, "properties": {}}, output_schema=None,
        sha="0" * 64, code=True,
    )

    def record(step: loop.Step) -> None:
        print(f"#{step.n} {step.kind} {step.name} {step.duration_ms} ms ${step.cost_usd:.4f}", file=sys.stderr)

    try:
        with db.connect() as conn:
            outcome = loop.run(definition, {}, provider="anthropic", record=record, inputs=[INPUT],
                               llm=LLMClient(conn, providers=dependencies.get_providers()),
                               call_tool=lambda name, args: {})
    except (DefinitionError, ProviderError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{outcome.stop_reason} after {outcome.model_calls} model calls, ${outcome.cost_usd:.4f} of the "
          f"${opts.max_usd:.2f} cap (Anthropic's bill may also show container time: see llm-pipelines)", file=sys.stderr)
    if outcome.answer is None or not outcome.answer.files:
        print("no file came back", file=sys.stderr)
        return 1
    print(outcome.answer.markdown)
    for path in hosted_code.save(outcome.answer.files, opts.out):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    print(f"Open {opts.out / WORKBOOK}: its Total row should read {EXPECTED_TOTAL}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
