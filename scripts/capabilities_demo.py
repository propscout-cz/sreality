"""List the tool registry's tools, or call one end to end on the service connection.

Usage:
    python -m scripts.capabilities_demo list
    python -m scripts.capabilities_demo call find_comparables_relaxed '{"property_id": 123}'

`call` runs on SUPABASE_DB_URL as the service role, so it reaches market tools only: a tenant tool answers
FORBIDDEN, because no signed-in user is behind the call.
"""

from __future__ import annotations

import argparse
import json
import sys

from api.capabilities import registry
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError
from api.capabilities.truncate import encode


def _list() -> None:
    for schema in registry.schemas():
        entry = registry.ENTRIES[schema["name"]]
        print(f"{entry.name}  [{entry.tenancy}, {entry.effect}]")
        print(f"  {entry.description}")
        print(f"  {json.dumps(schema['input_schema'], ensure_ascii=False)}\n")


def _call(name: str, args: str) -> int:
    from scraper.db import connect

    with connect() as conn:
        try:
            print(encode(registry.call(Caller(market=conn), name, json.loads(args))))
        except ToolError as exc:
            print(json.dumps(exc.payload(), ensure_ascii=False))
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    call = sub.add_parser("call")
    call.add_argument("name")
    call.add_argument("args", nargs="?", default="{}")
    opts = parser.parse_args(argv)
    if opts.command == "list":
        _list()
        return 0
    return _call(opts.name, opts.args)


if __name__ == "__main__":
    sys.exit(main())
