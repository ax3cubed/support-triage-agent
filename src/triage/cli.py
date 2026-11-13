"""Run one ticket from the command line and print each step as it happens."""

import argparse
import json

from triage.agent import LATEST, VERSIONS, build_graph, initial_state, summarise
from triage.config import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage one support ticket.")
    parser.add_argument("body", nargs="?", help="The ticket text.")
    parser.add_argument("--from", dest="from_email", default="alice@brightbakery.example")
    parser.add_argument("--subject", default="(no subject)")
    parser.add_argument("--agent", default=LATEST, choices=VERSIONS, help=f"Agent version (default: {LATEST}).")
    parser.add_argument("--case", help="Run a case from the evaluation dataset by id, e.g. e02.")
    args = parser.parse_args()

    if args.case:
        dataset = ROOT / "data/eval/triage.jsonl"
        if not dataset.exists():
            parser.error("--case needs the evaluation dataset, which is only in a source checkout")
        cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
        ticket = next((case["inputs"] for case in cases if case["id"] == args.case), None)
        if ticket is None:
            parser.error(f"unknown case id: {args.case}")
    elif args.body:
        ticket = {"from_email": args.from_email, "subject": args.subject, "body": args.body}
    else:
        parser.error("give a ticket body or --case")

    state: dict = initial_state(ticket, args.agent)
    # "updates" is what gets printed; "values" is the graph's own merged state, so nothing is re-merged by hand.
    for mode, chunk in build_graph(version=args.agent).stream(state, stream_mode=["updates", "values"]):
        if mode == "values":
            state = chunk
            continue
        for node, update in chunk.items():
            for name in (update or {}).get("tool_calls", []):
                print(f"  [{node}] {name}")

    result = summarise(state)
    result.pop("retrieved_context")
    print(json.dumps(result, indent=2, ensure_ascii=False))
