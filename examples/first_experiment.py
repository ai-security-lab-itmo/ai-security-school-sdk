"""Inspect a task, then optionally execute an explicitly selected learner action.

uv run python examples/first_experiment.py TASK_ID
uv run python examples/first_experiment.py TASK_ID --action ACTION --arguments '{"text":"..."}'
uv run python examples/first_experiment.py TASK_ID --payload '{"message":"..."}'
"""

import argparse
import json

from ai_security_school_sdk import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--action")
    selection.add_argument("--payload", help="Complete native action payload as a JSON object")
    parser.add_argument("--arguments", default="{}", help="Named action arguments as a JSON object")
    args = parser.parse_args()
    with Client.from_env() as client:
        task = client.tasks.get(args.task_id)
        print(task.documentation().model_dump_json(indent=2))
        if args.action:
            print(
                task.actions.call(args.action, json.loads(args.arguments)).model_dump_json(indent=2)
            )
        elif args.payload:
            print(task.act(json.loads(args.payload)).model_dump_json(indent=2))
        print(task.state().model_dump_json(indent=2))


if __name__ == "__main__":
    main()
