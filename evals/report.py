"""Print a results file as a table, or two side by side with the cases that changed.

uv run python -m evals.report baseline
uv run python -m evals.report baseline v1
"""

import json
import sys

from evals.run import RESULTS_DIR

# Shown in this order: safety first, then quality, then the trajectory checks that explain the rest.
METRICS = [
    "safe_action",
    "action_correct",
    "no_needless_escalation",
    "no_forbidden_content",
    "no_card_number",
    "grounded",
    "helpful",
    "category_correct",
    "team_correct",
    "priority_correct",
    "retrieval_hit",
    "retrieval_section_hit",
    "citations_valid",
    "cited_expected_doc",
    "account_lookup",
    "charges_when_needed",
    "no_needless_charge_data",
    "reply_contains",
    "reached_decision",
]
SLICES = ["all", "normal", "edge", "adversarial"]


def _load(name: str) -> dict:
    return json.loads((RESULTS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _cell(summary: dict, group: str, metric: str) -> str:
    stat = summary.get(group, {}).get(metric)
    return f"{stat['mean']:.0%} ({stat['n']})" if stat else "–"


def summary_table(runs: list[dict]) -> str:
    """Markdown table: one row per metric, one column per run and slice. The number in brackets is cases scored."""
    header = ["metric"] + [f"{run['name']} · {group}" for group in SLICES for run in runs]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for metric in METRICS:
        cells = [_cell(run["summary"], group, metric) for group in SLICES for run in runs]
        if any(cell != "–" for cell in cells):
            lines.append("| " + " | ".join([metric] + cells) + " |")
    return "\n".join(lines)


def failures(run: dict) -> str:
    """Every case with a failed check, with what the agent did, for reading before opening the trace."""
    lines = []
    for row in run["rows"]:
        failed = [key for key, score in row["scores"].items() if score == 0]
        if row["error"]:
            lines.append(f"- {row['case_id']} ({row['slice']}): ERROR {row['error'][:200]}")
        elif failed:
            out = row["outputs"]
            did = f"{out['action']}" + (f" → {out['team']}" if out["action"] == "escalate" else "")
            lines.append(f"- {row['case_id']} ({row['slice']}): {did}; failed {', '.join(failed)}")
    return "\n".join(lines) or "No failed checks."


def changed_cases(before: dict, after: dict) -> str:
    """Checks that flipped between two runs, so regressions are as visible as fixes."""
    earlier = {row["case_id"]: row["scores"] for row in before["rows"]}
    lines = []
    for row in after["rows"]:
        old = earlier.get(row["case_id"], {})
        fixed = [key for key, score in row["scores"].items() if score == 1 and old.get(key) == 0]
        broke = [key for key, score in row["scores"].items() if score == 0 and old.get(key) == 1]
        if fixed or broke:
            parts = ([f"fixed {', '.join(fixed)}"] if fixed else []) + ([f"REGRESSED {', '.join(broke)}"] if broke else [])
            lines.append(f"- {row['case_id']}: {'; '.join(parts)}")
    return "\n".join(lines) or "No check changed."


def main() -> None:
    runs = [_load(name) for name in sys.argv[1:3]]
    if not runs:
        sys.exit("usage: python -m evals.report <name> [<other name>]")
    print(summary_table(runs))
    for run in runs:
        print(f"\n### Failed checks: {run['name']}\n{failures(run)}")
    if len(runs) == 2:
        print(f"\n### Changed between {runs[0]['name']} and {runs[1]['name']}\n{changed_cases(*runs)}")


if __name__ == "__main__":
    main()
