"""Execute an explicit sequence of action recipes, checkpointing each accepted stage.

recipe.json contains an array of stages. Each stage is a list of
{"action": "ACTION_NAME", "arguments": {...}} objects from the manifest.
For recipes needing dynamic object IDs, replace this simple loop with Python
that reads result.data and supplies those IDs to subsequent actions.

uv run python examples/multistage.py LAB_ID recipe.json
"""

import argparse
import json
from pathlib import Path

from ai_security_school_sdk import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lab_id")
    parser.add_argument("recipe", type=Path)
    args = parser.parse_args()
    stages = json.loads(args.recipe.read_text())
    with Client.from_env() as client:
        lab = client.labs.get(args.lab_id)
        run = lab.runs.create()
        for index, actions in enumerate(stages):
            print("run_id:", run.run_id, "task_id:", run.task_id, flush=True)
            for action in actions:
                response = run.actions.call(action["action"], action["arguments"])
                print(response.model_dump_json())
            verdict = run.submit()
            print(verdict.model_dump_json())
            if not verdict.passed:
                print("Stage was not accepted; inspect this run before trying another candidate.")
                break
            checkpoint = run.checkpoint()
            print("checkpoint_id:", checkpoint.checkpoint_id, flush=True)
            if index + 1 < len(stages):
                run = checkpoint.fork()
                run.advance()


if __name__ == "__main__":
    main()
