"""Run one ticket from the command line and print each step as it happens."""

import argparse
import json

from triage.agent import build_graph, summarise
from triage.config import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage one support ticket.")
    parser.add_argument("body", nargs="?", help="The ticket text.")
    parser.add_argument("--from", dest="from_email", default="alice@brightbakery.example")
    parser.add_argument("--subject", default="(no subject)")
    parser.add_argument("--case", help="Run a case from the evaluation dataset by id, e.g. e02.")
    args = parser.parse_args()

    if args.case:
        cases = [json.loads(line) for line in (ROOT / "data/eval/triage.jsonl").read_text(encoding="utf-8").splitlines() if line]
        ticket = next(case["inputs"] for case in cases if case["id"] == args.case)
    elif args.body:
        ticket = {"from_email": args.from_email, "subject": args.subject, "body": args.body}
    else:
        parser.error("give a ticket body or --case")

    state: dict = {"ticket": ticket}
    for step in build_graph().stream(state, stream_mode="updates"):
        for node, update in step.items():
            for name in update.get("tool_calls", []):
                print(f"  [{node}] {name}")
            for key, value in update.items():
                if key in ("retrieved", "tool_calls", "messages"):
                    state[key] = state.get(key, []) + value
                else:
                    state[key] = value

    result = summarise(state)
    result.pop("retrieved_context")
    print(json.dumps(result, indent=2, ensure_ascii=False))
