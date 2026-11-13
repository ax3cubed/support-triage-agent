"""Check the LLM judge against a person's own marks.

    uv run python -m evals.calibrate sample v0-final v3-final   # pick the messages to mark
    uv run python -m evals.calibrate mark                       # mark them, one at a time, blind
    uv run python -m evals.calibrate report                     # confusion matrix per question

`sample` picks on purpose, not at random: every message the judge failed, every reply sent when a
person was needed, and a random share of the messages the judge passed. Failures are rare, so a
random sample would hold too few of them to say anything.

`mark` never shows the judge's verdict, its reasoning, or which agent version wrote the message, and
shows the messages in a shuffled order. The marks are saved after every answer, so it can be stopped
and picked up again.
"""

import argparse
import hashlib
import json
import random
import textwrap

from triage import kb
from triage.config import ROOT

CALIBRATION_DIR = ROOT / "calibration"
ITEMS_FILE = CALIBRATION_DIR / "items.jsonl"
MARKS_FILE = CALIBRATION_DIR / "human_marks.jsonl"
RESULTS_DIR = ROOT / "results"

QUESTIONS = {
    "grounded": "GROUNDED: does it state only facts found in the help sections or the account record? "
    "(Declining, hand-over wording and courtesy are fine. A refund promise or refusal is not.)",
    "correct": "CORRECT: does it agree with the section that holds the right answer?",
    "helpful": "HELPFUL: does it answer what was asked, say what to do next, and read as something you would send?",
    "account_status_private": "PRIVATE: does it avoid revealing whether an account exists for anyone?",
}


def _read(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sample(run_names: list[str], passes: int, seed: int) -> list[dict]:
    """Choose the messages to mark from saved runs. Returns one item per distinct message."""
    seen, failed, passed = set(), [], []
    for name in run_names:
        run = json.loads((RESULTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for row in run["rows"]:
            judge = {key: row["scores"][key] for key in QUESTIONS if key in row["scores"]}
            if not judge or row["outputs"]["action"] == "escalate":
                continue
            key = (row["case_id"], row["outputs"]["message"])
            if key in seen:
                continue
            seen.add(key)
            item = {
                "id": hashlib.sha1("\n".join(key).encode()).hexdigest()[:10],
                "run": name,
                "case_id": row["case_id"],
                "inputs": row["inputs"],
                "action": row["outputs"]["action"],
                "message": row["outputs"]["message"],
                "retrieved_context": row["outputs"]["retrieved_context"],
                "account_context": row["outputs"].get("account_context", ""),
                "sections": row["expected"].get("sections", []),
                "judge": judge,
                "judge_reasons": {key: row["comments"].get(key, "") for key in judge},
                "wrong_action": row["scores"].get("action_correct") == 0,
            }
            (failed if 0 in judge.values() or item["wrong_action"] else passed).append(item)
    random.Random(seed).shuffle(passed)
    items = failed + passed[:passes]
    random.Random(seed).shuffle(items)
    return items


def _show(item: dict, position: int, total: int) -> None:
    def block(title: str, text: str) -> None:
        print(f"\n--- {title} ---")
        for line in (text or "(none)").splitlines():
            print(textwrap.fill(line, 110) if line.strip() else "")

    print("\n" + "=" * 110)
    print(f"Message {position} of {total}   (this is a {'clarifying question' if item['action'] == 'ask_customer' else 'reply'})")
    ticket = item["inputs"]
    block("TICKET", f"From: {ticket['from_email']}\nSubject: {ticket['subject']}\n\n{ticket['body']}")
    block("ACCOUNT RECORD THE AGENT LOOKED UP", item["account_context"])
    block("HELP SECTIONS THE AGENT FOUND", item["retrieved_context"])
    answers = [kb.section_text(section) for section in item["sections"]]
    if any(answers):
        block("SECTION THAT HOLDS THE RIGHT ANSWER", "\n\n".join(answer for answer in answers if answer))
    block("MESSAGE SENT TO THE CUSTOMER", item["message"])


def mark() -> None:
    """Ask the person each question the judge was asked, for every message not yet marked."""
    items = _read(ITEMS_FILE)
    if not items:
        raise SystemExit("Nothing to mark. Run `sample` first.")
    done = {mark["id"] for mark in _read(MARKS_FILE)}
    todo = [item for item in items if item["id"] not in done]
    print(f"{len(done)} of {len(items)} marked. Answer y (pass), n (fail) or s (skip this message). q saves and quits.")
    for position, item in enumerate(todo, len(done) + 1):
        _show(item, position, len(items))
        answers: dict[str, int] = {}
        for key in item["judge"]:
            reply = ""
            while reply not in ("y", "n", "s", "q"):
                reply = input(f"\n{QUESTIONS[key]}\n  y / n / s / q > ").strip().lower()
            if reply == "q":
                return
            if reply == "s":
                answers = {}
                break
            answers[key] = int(reply == "y")
        if answers:
            note = input("\nOne line on why, for any 'n' (optional) > ").strip()
            with MARKS_FILE.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"id": item["id"], "marks": answers, "note": note}, ensure_ascii=False) + "\n")
    print("\nAll marked. Now run: uv run python -m evals.calibrate report")


def confusion(pairs: list[tuple[int, int]]) -> dict:
    """Compare (human, judge) marks, where 1 is pass and 0 is fail. A fail is the thing we want caught.

    caught: both failed it. missed: the person failed it and the judge passed it (the dangerous one).
    false_alarm: the judge failed something the person passed. agreed_pass: both passed it.
    """
    caught = sum(human == 0 and judge == 0 for human, judge in pairs)
    missed = sum(human == 0 and judge == 1 for human, judge in pairs)
    false_alarm = sum(human == 1 and judge == 0 for human, judge in pairs)
    agreed_pass = sum(human == 1 and judge == 1 for human, judge in pairs)
    total = len(pairs)
    observed = (caught + agreed_pass) / total if total else 0.0
    # Agreement expected by chance, from how often each side says pass and fail.
    human_fail, judge_fail = (caught + missed) / total if total else 0, (caught + false_alarm) / total if total else 0
    expected = human_fail * judge_fail + (1 - human_fail) * (1 - judge_fail)

    def ratio(part: int, whole: int) -> float | None:
        return round(part / whole, 2) if whole else None

    return {
        "n": total,
        "caught": caught,
        "missed": missed,
        "false_alarm": false_alarm,
        "agreed_pass": agreed_pass,
        "accuracy": round(observed, 2),
        "catch_rate": ratio(caught, caught + missed),
        "judge_fails_that_were_right": ratio(caught, caught + false_alarm),
        "kappa": round((observed - expected) / (1 - expected), 2) if expected < 1 else None,
    }


def report() -> None:
    items = {item["id"]: item for item in _read(ITEMS_FILE)}
    marks = {mark["id"]: mark for mark in _read(MARKS_FILE)}
    if not marks:
        raise SystemExit("No marks yet. Run `mark` first.")
    print(f"{len(marks)} of {len(items)} messages marked.\n")
    for key in QUESTIONS:
        pairs = [(mark["marks"][key], items[i]["judge"][key]) for i, mark in marks.items() if key in mark["marks"]]
        if not pairs:
            continue
        stats = confusion(pairs)
        print(f"## {key} ({stats['n']} messages)\n")
        print("|                 | Judge said fail | Judge said pass |")
        print("|---|---|---|")
        print(f"| **You said fail** | {stats['caught']} caught | {stats['missed']} missed |")
        print(f"| **You said pass** | {stats['false_alarm']} false alarm | {stats['agreed_pass']} agreed |")
        print(f"\n- Catch rate (of the ones you failed, how many the judge failed): {stats['catch_rate']}")
        print(f"- Of the judge's fails, how many you also failed: {stats['judge_fails_that_were_right']}")
        print(f"- Agreement beyond chance (kappa, 1 is perfect, 0 is chance): {stats['kappa']}")
        print(f"- Plain accuracy, for reference only: {stats['accuracy']}\n")
    print("## Where you and the judge disagree\n")
    for i, mark in marks.items():
        item = items[i]
        for key, human in mark["marks"].items():
            if human != item["judge"][key]:
                kind = "MISSED by the judge" if human == 0 else "false alarm"
                print(f"- {item['case_id']} ({item['run']}), {key}: {kind}. You: {mark['note'] or '(no note)'}")
                print(f"  Judge: {item['judge_reasons'][key][:300]}")
    print(
        "\nNote: the messages were picked to include every judge fail, so these rates describe this set. "
        "They are not the rates you would see on a random day's tickets."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the LLM judge against a person's marks.")
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("sample", help="Pick the messages to mark from saved runs.")
    pick.add_argument("runs", nargs="+", help="Names of results files, for example v0-final v3-final.")
    pick.add_argument("--passes", type=int, default=25, help="How many judge-passed messages to add.")
    pick.add_argument("--seed", type=int, default=7)
    commands.add_parser("mark", help="Mark the messages, one at a time, without seeing the judge.")
    commands.add_parser("report", help="Print the confusion matrix for each question.")
    args = parser.parse_args()

    if args.command == "sample":
        if _read(MARKS_FILE):
            raise SystemExit(f"{MARKS_FILE.relative_to(ROOT)} already has marks. Move it away before sampling again.")
        items = sample(args.runs, args.passes, args.seed)
        CALIBRATION_DIR.mkdir(exist_ok=True)
        ITEMS_FILE.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n", encoding="utf-8")
        flagged = sum(0 in item["judge"].values() or item["wrong_action"] for item in items)
        print(f"Saved {len(items)} messages to {ITEMS_FILE.relative_to(ROOT)}: {flagged} flagged, {len(items) - flagged} passed.")
        print("Now run: uv run python -m evals.calibrate mark")
    elif args.command == "mark":
        mark()
    else:
        report()


if __name__ == "__main__":
    main()
