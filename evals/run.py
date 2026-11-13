"""Run the agent over the dataset, score it, and save a results file that `evals.report` can compare.

uv run python -m evals.run --name baseline --agent v0 # LangSmith experiment + results/baseline.json
uv run python -m evals.run --name baseline --local    # no upload; needs only a model key
"""

import argparse
import json
from collections import defaultdict

from langsmith import Client, evaluate, tracing_context

from evals.dataset import load_cases, local_examples, sync_to_langsmith
from evals.evaluators import deterministic_checks
from evals.judge import reply_judge
from triage.agent import LATEST, VERSIONS, triage
from triage.config import AGENT_MODEL, JUDGE_MODEL, ROOT
from triage.redact import find_card_numbers_strict

RESULTS_DIR = ROOT / "results"


# A run that crashed made no decision at all, so it fails these whatever else is true of it.
CRASH_SCORES = {"safe_action": 0, "action_correct": 0, "reached_decision": 0}


def crash_scores(inputs: dict, expected: dict) -> dict:
    """Scores for a run that raised: every check that applies to the case, scored on an empty answer.

    Without this a crashed case drops out of checks like no_card_number and retrieval_hit, and their
    averages are taken over fewer cases than the run had. Nothing was written, so no_card_number
    passes; nothing is known about what the model saw, so a card in the ticket counts as having reached it.
    """
    card = bool(find_card_numbers_strict("\n".join(str(value) for value in (inputs or {}).values())))
    empty = {
        "action": "error", "category": None, "team": None, "priority": None, "message": "", "cited_doc_ids": [],
        "retrieved_doc_ids": [], "retrieved_sections": [], "tool_calls": [], "saw_charges": False,
        "card_in_ticket": card, "card_reached_model": card, "gave_up": True,
    }  # fmt: skip
    return {**{result["key"]: result["score"] for result in deterministic_checks(empty, expected or {})}, **CRASH_SCORES}


def run_experiment(target, data, evaluators: list, upload: bool, **options):
    """`langsmith.evaluate`, with one repair: a run that uploads nothing really sends nothing.

    With upload_results=False LangSmith sets tracing to "local", which LangChain reads as on, so the
    agent's own trace (the ticket included) was still posted whenever a key was set.
    """
    if not upload:
        inner = target

        def target(inputs: dict) -> dict:
            with tracing_context(enabled=False):
                return inner(inputs)

    return evaluate(target, data=data, evaluators=evaluators, upload_results=upload, **options)


def collect_rows(experiment) -> list[dict]:
    """Turn LangSmith's result objects into plain rows: one per run of a case, with outputs and every score."""
    rows = []
    for item in experiment:
        metadata = item["example"].metadata or {}
        results = item["evaluation_results"]["results"]
        scores = {result.key: result.score for result in results if result.score is not None}
        rows.append(
            {
                "case_id": metadata.get("case_id"),
                "slice": metadata.get("slice"),
                "inputs": item["example"].inputs,
                "expected": item["example"].outputs,
                "outputs": item["run"].outputs,
                "error": item["run"].error,
                "scores": crash_scores(item["example"].inputs, item["example"].outputs) if item["run"].error else scores,
                "comments": {r.key: r.comment for r in results if r.comment},
            }
        )
    rows.sort(key=lambda row: row["case_id"] or "")
    seen: dict[str, int] = defaultdict(int)
    for row in rows:
        seen[row["case_id"]] += 1
        row["repetition"] = seen[row["case_id"]]
    return rows


def case_scores(rows: list[dict]) -> dict[str, dict]:
    """One entry per case: its slice, how many times it ran, and each check's pass rate across those runs."""
    cases: dict[str, dict] = {}
    for row in rows:
        case = cases.setdefault(row["case_id"], {"slice": row["slice"], "runs": 0, "errors": 0, "by_check": defaultdict(list)})
        case["runs"] += 1
        case["errors"] += bool(row.get("error"))
        for key, score in row["scores"].items():
            if score is not None:
                case["by_check"][key].append(score)
    for case in cases.values():
        case["rates"] = {key: sum(scores) / len(scores) for key, scores in case.pop("by_check").items()}
    return cases


def summarise(rows: list[dict]) -> dict:
    """Mean of each check overall and per slice. `n` is the number of cases, however often each was run.

    Each case is averaged over its own runs first, so a case run five times counts once, not five times.
    """
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for case in case_scores(rows).values():
        for key, rate in case["rates"].items():
            buckets["all"][key].append(rate)
            buckets[case["slice"]][key].append(rate)
    return {
        group: {key: {"mean": round(sum(rates) / len(rates), 3), "n": len(rates)} for key, rates in metrics.items()}
        for group, metrics in buckets.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the triage agent.")
    parser.add_argument("--name", required=True, help="Experiment name, e.g. baseline or v1.")
    parser.add_argument("--agent", default=LATEST, choices=VERSIONS, help=f"Agent version to test (default: {LATEST}).")
    parser.add_argument("--local", action="store_true", help="Do not upload the dataset or results to LangSmith.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM judge (deterministic checks only).")
    parser.add_argument("--repetitions", type=int, default=1, help="Run each case this many times to see variance.")
    parser.add_argument("--only", nargs="*", help="Run only these case ids.")
    args = parser.parse_args()

    known = {case["id"] for case in load_cases()}
    if args.only and set(args.only) - known:
        parser.error(f"unknown case ids: {', '.join(sorted(set(args.only) - known))}")
    cases = [case for case in load_cases() if not args.only or case["id"] in args.only]
    if args.local or args.only:
        data = local_examples(cases)
    else:
        data = sync_to_langsmith(Client(), cases)

    experiment = run_experiment(
        lambda inputs: triage(inputs, version=args.agent),
        data,
        [deterministic_checks] if args.no_judge else [deterministic_checks, reply_judge],
        upload=not (args.local or args.only),
        experiment_prefix=args.name,
        metadata={"agent_version": args.agent, "agent_model": AGENT_MODEL, "judge_model": JUDGE_MODEL},
        num_repetitions=args.repetitions,
        max_concurrency=4,
    )

    rows = collect_rows(experiment)
    record = {
        "name": args.name,
        "agent_version": args.agent,
        "agent_model": AGENT_MODEL,
        "judge_model": None if args.no_judge else JUDGE_MODEL,
        "experiment": getattr(experiment, "experiment_name", None),
        "repetitions": args.repetitions,
        "cases": len({row["case_id"] for row in rows}),
        "errors": sum(bool(row["error"]) for row in rows),
        "summary": summarise(rows),
        "rows": rows,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    # A run of a few cases must never replace the full results file of the same name.
    path = RESULTS_DIR / (f"{args.name}-partial.json" if args.only else f"{args.name}.json")
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {path.relative_to(ROOT)}. Compare runs with: uv run python -m evals.report {path.stem}")


if __name__ == "__main__":
    main()
