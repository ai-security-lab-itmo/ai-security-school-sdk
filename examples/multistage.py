"""Execute explicit task payloads in one environment with shared state.

recipe.json is an array of {"task_id": "...", "payloads": [{...}, {...}]} objects.
Payloads use each task's action_payload_schema. For dynamic IDs, replace this loop
with Python that reads response.response and passes IDs to the next action.

uv run python examples/multistage.py INSTANCE_ID recipe.json
"""

import argparse
import json
from pathlib import Path

from ai_security_school_sdk import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("recipe", type=Path)
    args = parser.parse_args()
    stages = json.loads(args.recipe.read_text())
    with Client.from_env() as client:
        env = client.envs.get(args.instance_id)
        for stage in stages:
            task = env.tasks.get(stage["task_id"])
            state = task.state()
            if state.status == "locked":
                print(state.model_dump_json(indent=2))
                break
            for payload in stage["payloads"]:
                response = task.act(payload)
                print(response.model_dump_json(indent=2), flush=True)
            if task.documentation().supports_grading:
                verdict = task.grade()
                print(verdict.model_dump_json(indent=2), flush=True)
                if not verdict.grader_passed:
                    break
            # Next task uses the same state. The server enforces prerequisites.


if __name__ == "__main__":
    main()
