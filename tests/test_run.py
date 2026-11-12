from conftest import ScriptedModel
from langsmith import evaluate

from evals.dataset import load_cases, local_examples
from evals.evaluators import deterministic_checks
from evals.judge import reply_judge
from evals.report import changed_cases, failures
from evals.run import case_scores, collect_rows, summarise
from triage.agent import triage


def test_a_local_run_scores_each_case_and_summarises_by_slice():
    cases = [case for case in load_cases() if case["id"] in ("n01", "e01")]

    def always_replies(inputs: dict) -> dict:
        model = ScriptedModel(
            [("search_help_center", {"query": "automatic reminders"})],
            [("send_reply", {"category": "product", "body": "Settings › Reminders.", "cited_doc_ids": ["reminders"]})],
        )
        return triage(inputs, model=model)

    experiment = evaluate(always_replies, data=local_examples(cases), evaluators=[deterministic_checks], upload_results=False)
    rows = collect_rows(experiment)

    assert [row["case_id"] for row in rows] == ["e01", "n01"]
    assert rows[0]["scores"]["safe_action"] == 0  # replied to a refund request
    assert rows[1]["scores"]["action_correct"] == 1
    assert summarise(rows)["all"]["safe_action"] == {"mean": 0.5, "n": 2}


def test_the_judge_skips_escalations_without_an_evaluator_error(caplog):
    cases = [case for case in load_cases() if case["id"] == "e01"]

    def always_escalates(inputs: dict) -> dict:
        args = {"category": "billing", "team": "billing", "priority": "normal", "summary": "Refund request."}
        return triage(inputs, model=ScriptedModel([("escalate_to_human", args)]))

    experiment = evaluate(always_escalates, data=local_examples(cases), evaluators=[reply_judge], upload_results=False)
    rows = collect_rows(experiment)

    assert rows[0]["scores"] == {}
    assert "Error running evaluator" not in caplog.text


def _row(case_id: str, repetition: int, **scores) -> dict:
    outputs = {"action": "reply", "team": None}
    return {"case_id": case_id, "slice": "normal", "repetition": repetition, "error": None, "outputs": outputs, "scores": scores}


def _run(name: str, rows: list[dict]) -> dict:
    return {"name": name, "rows": rows, "case_scores": case_scores(rows)}


def test_a_case_run_three_times_counts_once_with_its_pass_rate():
    rows = [_row("n01", 1, safe_action=1), _row("n01", 2, safe_action=0), _row("n01", 3, safe_action=1)]
    rows += [_row("n02", i, safe_action=1) for i in (1, 2, 3)]

    assert summarise(rows)["all"]["safe_action"] == {"mean": 0.833, "n": 2}
    assert "safe_action passed 2 of 3" in failures(_run("a", rows))


def test_comparing_a_repeated_run_with_itself_reports_no_change():
    rows = [_row("n01", 1, safe_action=1), _row("n01", 2, safe_action=0), _row("n01", 3, safe_action=1)]

    assert changed_cases(_run("a", rows), _run("b", rows)) == "No check changed."


def test_a_changed_pass_rate_and_a_missing_case_are_both_reported():
    before = _run(
        "a", [_row("n01", i, safe_action=score) for i, score in enumerate((1, 0, 0), 1)] + [_row("n02", 1, safe_action=1)]
    )
    after = _run("b", [_row("n01", i, safe_action=1) for i in (1, 2, 3)])

    report = changed_cases(before, after)

    assert "n01: better: safe_action (1 of 3 → 3 of 3)" in report
    assert "n02: only in a" in report


def test_a_crashed_run_counts_as_a_failure_not_a_gap():
    cases = [case for case in load_cases() if case["id"] == "e03"]

    def crashes(inputs: dict) -> dict:
        raise TimeoutError("provider down")

    experiment = evaluate(crashes, data=local_examples(cases), evaluators=[deterministic_checks], upload_results=False)
    rows = collect_rows(experiment)

    assert rows[0]["error"]
    assert summarise(rows)["all"]["safe_action"] == {"mean": 0.0, "n": 1}
