"""Re-apply the checks done by code to saved runs, after a label in the test has changed.

    uv run python -m evals.rescore v0-final v3-final v4

The agent's answers and the judge's verdicts are left exactly as they were. Only the deterministic
checks are worked out again, from the saved answers and the labels as they now stand, so a label
fix does not need another paid run. Each file records that it was re-scored and why.
"""

import argparse
import json

from evals.dataset import load_cases
from evals.evaluators import deterministic_checks
from evals.run import RESULTS_DIR, summarise

JUDGE_KEYS = {"grounded", "correct", "helpful", "account_status_private"}


def rescore(run: dict, cases: dict[str, dict]) -> int:
    """Update every row in place. Returns how many scores changed."""
    changed = 0
    for row in run["rows"]:
        if row["error"] or row["case_id"] not in cases:
            continue
        expected = cases[row["case_id"]]["expected"]
        judged = {key: score for key, score in row["scores"].items() if key in JUDGE_KEYS}
        fresh = {result["key"]: result["score"] for result in deterministic_checks(row["outputs"], expected)}
        new_scores = {**fresh, **judged}
        changed += sum(new_scores.get(key) != row["scores"].get(key) for key in set(new_scores) | set(row["scores"]))
        row["scores"], row["expected"] = new_scores, expected
    run["summary"] = summarise(run["rows"])
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-apply the code checks to saved runs.")
    parser.add_argument("runs", nargs="+", help="Names of results files, for example v3-final v4.")
    parser.add_argument("--reason", required=True, help="Why the labels changed; saved in each file.")
    args = parser.parse_args()
    cases = {case["id"]: case for case in load_cases()}
    for name in args.runs:
        path = RESULTS_DIR / f"{name}.json"
        run = json.loads(path.read_text(encoding="utf-8"))
        changed = rescore(run, cases)
        run.setdefault("rescored", []).append({"reason": args.reason, "scores_changed": changed})
        path.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"{name}: {changed} scores changed")


if __name__ == "__main__":
    main()
