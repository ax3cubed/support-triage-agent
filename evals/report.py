"""Print a results file as a table, or two side by side with the cases that changed.

uv run python -m evals.report baseline
uv run python -m evals.report baseline v1
"""

import json
import sys

from evals.run import RESULTS_DIR, case_scores, summarise

# Shown in this order: safety first, then quality, then the trajectory checks that explain the rest.
METRICS = [
    "safe_action",
    "action_correct",
    "no_needless_escalation",
    "no_forbidden_content",
    "no_account_status_leak",
    "account_status_private",
    "card_hidden_from_model",
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
    run = json.loads((RESULTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    # Recomputed from the rows, so files saved by older versions of the runner are read the same way.
    run["summary"] = summarise(run["rows"])
    run["case_scores"] = case_scores(run["rows"])
    return run


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


def _rate(rate: float, runs: int) -> str:
    return f"{round(rate * runs)} of {runs}"


def failures(run: dict) -> str:
    """Every case with a failed check. With repeated runs, how many of its runs passed each one."""
    actions: dict[str, list[str]] = {}
    for row in run["rows"]:
        out = row["outputs"] or {}
        did = "ERROR" if row["error"] else out["action"] + (f" → {out['team']}" if out["action"] == "escalate" else "")
        actions.setdefault(row["case_id"], []).append(did)
    lines = []
    for case_id, case in run["case_scores"].items():
        failed = {key: rate for key, rate in case["rates"].items() if rate < 1}
        if not failed:
            continue
        did = ", ".join(f"{action} ×{actions[case_id].count(action)}" for action in dict.fromkeys(actions[case_id]))
        if case["runs"] == 1:
            lines.append(f"- {case_id} ({case['slice']}): {actions[case_id][0]}; failed {', '.join(failed)}")
        else:
            detail = ", ".join(f"{key} passed {_rate(rate, case['runs'])}" for key, rate in failed.items())
            lines.append(f"- {case_id} ({case['slice']}): {did}; {detail}")
    return "\n".join(lines) or "No failed checks."


def changed_cases(before: dict, after: dict) -> str:
    """Checks whose pass rate differs between two runs, on the cases and checks scored in both.

    With one run each, about four cases in 47 change by chance alone, so a single flip is not evidence.
    """
    lines = []
    for case_id, new in after["case_scores"].items():
        old = before["case_scores"].get(case_id)
        if old is None:
            lines.append(f"- {case_id}: only in {after['name']}")
            continue
        fixed, broke = [], []
        for key in sorted(set(old["rates"]) & set(new["rates"])):
            was, now = old["rates"][key], new["rates"][key]
            if was == now:
                continue
            repeated = old["runs"] > 1 or new["runs"] > 1
            label = f"{key} ({_rate(was, old['runs'])} → {_rate(now, new['runs'])})" if repeated else key
            (fixed if now > was else broke).append(label)
        if fixed or broke:
            parts = ([f"better: {', '.join(fixed)}"] if fixed else []) + ([f"WORSE: {', '.join(broke)}"] if broke else [])
            lines.append(f"- {case_id}: {'; '.join(parts)}")
    lines += [
        f"- {case_id}: only in {before['name']}" for case_id in before["case_scores"] if case_id not in after["case_scores"]
    ]
    return "\n".join(lines) or "No check changed."


def main() -> None:
    runs = [_load(name) for name in sys.argv[1:3]]
    if not runs:
        sys.exit("usage: python -m evals.report <name> [<other name>]")
    print(summary_table(runs))
    for run in runs:
        runs_per_case = max(case["runs"] for case in run["case_scores"].values())
        errors = sum(case["errors"] for case in run["case_scores"].values())
        print(
            f"\n### Failed checks: {run['name']} ({len(run['case_scores'])} cases, {runs_per_case} run(s) each, {errors} errors)"
        )
        print(failures(run))
    if len(runs) == 2:
        print(f"\n### Changed between {runs[0]['name']} and {runs[1]['name']}\n{changed_cases(*runs)}")
        if len(runs[0]["case_scores"]) != len(runs[1]["case_scores"]):
            print("\nWARNING: the two runs cover different cases, so the percentages above are not like for like.")


if __name__ == "__main__":
    main()
