"""Run the agent over the dataset, score it, and save a results file that `evals.report` can compare.

uv run python -m evals.run --name baseline            # LangSmith experiment + results/baseline.json
uv run python -m evals.run --name baseline --local    # no upload; needs only a model key
"""

import argparse
import json
from collections import defaultdict

from langsmith import Client, evaluate

from evals.dataset import load_cases, local_examples, sync_to_langsmith
from evals.evaluators import deterministic_checks
from evals.judge import reply_judge
from triage.agent import triage
from triage.config import AGENT_MODEL, JUDGE_MODEL, ROOT

RESULTS_DIR = ROOT / "results"


def collect_rows(experiment) -> list[dict]:
    """Turn LangSmith's result objects into plain rows: one per case, with outputs and every score."""
    rows = []
    for item in experiment:
        metadata = item["example"].metadata or {}
        rows.append(
            {
                "case_id": metadata.get("case_id"),
                "slice": metadata.get("slice"),
                "expected": item["example"].outputs,
                "outputs": item["run"].outputs,
                "error": item["run"].error,
                "scores": {result.key: result.score for result in item["evaluation_results"]["results"]},
                "comments": {r.key: r.comment for r in item["evaluation_results"]["results"] if r.comment},
            }
        )
    return sorted(rows, key=lambda row: row["case_id"] or "")


def summarise(rows: list[dict]) -> dict:
    """Mean of each score overall and per slice, with how many cases it was scored on."""
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for key, score in row["scores"].items():
            if score is not None:
                buckets["all"][key].append(score)
                buckets[row["slice"]][key].append(score)
    return {
        group: {key: {"mean": round(sum(scores) / len(scores), 3), "n": len(scores)} for key, scores in metrics.items()}
        for group, metrics in buckets.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the triage agent.")
    parser.add_argument("--name", required=True, help="Experiment name, e.g. baseline or v1.")
    parser.add_argument("--prompt", default="v0", help="Prompt version from triage.prompts.")
    parser.add_argument("--local", action="store_true", help="Do not upload the dataset or results to LangSmith.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM judge (deterministic checks only).")
    parser.add_argument("--repetitions", type=int, default=1, help="Run each case this many times to see variance.")
    parser.add_argument("--only", nargs="*", help="Run only these case ids.")
    args = parser.parse_args()

    cases = [case for case in load_cases() if not args.only or case["id"] in args.only]
    if args.local or args.only:
        data = local_examples(cases)
    else:
        data = sync_to_langsmith(Client(), cases)

    experiment = evaluate(
        lambda inputs: triage(inputs, prompt_version=args.prompt),
        data=data,
        evaluators=[deterministic_checks] if args.no_judge else [deterministic_checks, reply_judge],
        experiment_prefix=args.name,
        metadata={"prompt": args.prompt, "agent_model": AGENT_MODEL, "judge_model": JUDGE_MODEL},
        num_repetitions=args.repetitions,
        max_concurrency=4,
        upload_results=not (args.local or args.only),
    )

    rows = collect_rows(experiment)
    record = {
        "name": args.name,
        "prompt": args.prompt,
        "agent_model": AGENT_MODEL,
        "judge_model": None if args.no_judge else JUDGE_MODEL,
        "experiment": getattr(experiment, "experiment_name", None),
        "summary": summarise(rows),
        "rows": rows,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{args.name}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {path.relative_to(ROOT)}. Compare runs with: uv run python -m evals.report {args.name}")


if __name__ == "__main__":
    main()
