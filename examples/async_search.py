"""Try candidate argument objects sequentially against one shared environment.

The JSON file contains a list of objects matching the named action's input schema.
By default candidates build on previous state. --reset-each explicitly resets the
shared environment before every candidate, including the first one. Reset behavior
is environment-specific and may preserve already earned completions.

uv run python examples/async_search.py TASK_ID ACTION candidates.json --reset-each
"""

import argparse
import asyncio
import json
from pathlib import Path

from ai_security_school_sdk import AsyncClient


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("action")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--reset-each", action="store_true")
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text())
    if not isinstance(candidates, list) or not all(isinstance(c, dict) for c in candidates):
        parser.error("candidates.json must be a list of argument objects")

    async with AsyncClient.from_env() as client:
        task = await client.tasks.get(args.task_id)
        docs = await task.documentation()
        for index, arguments in enumerate(candidates):
            if args.reset_each:
                await task.reset()
            current = await task.state()
            if current.status == "locked":
                print(
                    json.dumps(
                        {"candidate": index, "state": current.model_dump(mode="json")},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    flush=True,
                )
                break
            # Do not gather candidates: all calls below change the same user state.
            response = await task.actions.call(args.action, arguments)
            result = {"candidate": index, "response": response.model_dump(mode="json")}
            if response.status == "locked":
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                break
            if docs.supports_standalone_grading:
                verdict = await task.grade()
                result["verdict"] = verdict.model_dump(mode="json")
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            # Exceptions stop the search. Inspect shared state before retrying.


if __name__ == "__main__":
    asyncio.run(main())
