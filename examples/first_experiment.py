"""Inspect a lab, then optionally execute one explicitly selected learner action.

uv run python examples/first_experiment.py LAB_ID
uv run python examples/first_experiment.py LAB_ID --action ACTION --arguments '{"text":"..."}'
"""

import argparse
import json

from ai_security_school_sdk import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lab_id")
    parser.add_argument("--action")
    parser.add_argument("--arguments", default="{}", help="Action arguments as a JSON object")
    args = parser.parse_args()
    with Client.from_env() as client:
        lab = client.labs.get(args.lab_id)
        print(lab.title)
        run = lab.runs.create()
        print("run_id:", run.run_id)
        for action in run.actions.list():
            print(json.dumps(action.model_dump(), ensure_ascii=False, indent=2))
        if args.action:
            job = run.actions.start_call(args.action, json.loads(args.arguments))
            print("job_id:", job.job_id, flush=True)
            print(job.wait().model_dump_json(indent=2))
            print(run.observation().model_dump_json(indent=2))


if __name__ == "__main__":
    main()
