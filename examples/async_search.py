"""Compare candidate argument objects using independent runs and bounded concurrency.

The JSON file is a list of objects matching the selected action's input schema.
This example executes one action per candidate. More complex attack algorithms
can make several explicit actions inside attempt() before submitting.

uv run python examples/async_search.py LAB_ID ACTION candidates.json --concurrency 2
"""

import argparse
import asyncio
import json
from pathlib import Path

from ai_security_school_sdk import AsyncClient, SDKError


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lab_id")
    parser.add_argument("action")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    candidates = json.loads(args.candidates.read_text())
    if not isinstance(candidates, list) or not all(isinstance(c, dict) for c in candidates):
        parser.error("candidates.json must be a list of argument objects")

    semaphore = asyncio.Semaphore(args.concurrency)
    async with AsyncClient.from_env() as client:
        lab = await client.labs.get(args.lab_id)

        async def attempt(index: int, arguments: dict) -> dict:
            async with semaphore:
                run = await lab.runs.create()
                print(f"candidate={index} run_id={run.run_id}", flush=True)
                try:
                    job = await run.actions.start_call(args.action, arguments)
                    print(f"candidate={index} job_id={job.job_id}", flush=True)
                    response = await job.wait()
                    submission = await run.start_submission()
                    print(f"candidate={index} submission_job_id={submission.job_id}", flush=True)
                    verdict = await submission.wait()
                    return {
                        "candidate": index,
                        "run_id": run.run_id,
                        "response": response.model_dump(mode="json"),
                        "verdict": verdict.model_dump(mode="json"),
                    }
                except SDKError as error:
                    # Preserve the run and job for inspection; do not blindly rerun.
                    return {"candidate": index, "run_id": run.run_id, "error": str(error)}

        results = await asyncio.gather(*(attempt(i, item) for i, item in enumerate(candidates)))
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
