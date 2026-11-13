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


class _FakeJudge:
    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        from evals.judge import Verdict

        self.prompts.append(prompt)
        return Verdict(
            grounded_reasoning="ok", grounded=True, correct_reasoning="wrong day", correct=False,
            helpful_reasoning="ok", helpful=True, account_status_private_reasoning="ok", account_status_private=True,
        )  # fmt: skip


def test_the_judge_is_shown_the_account_and_the_right_answer_and_scores_only_what_applies(monkeypatch):
    from evals import judge

    fake = _FakeJudge()
    monkeypatch.setattr(judge, "_judge", lambda: fake)
    inputs = {"from_email": "alice@brightbakery.example", "subject": "Payout", "body": "Card 4242 4242 4242 4242 123"}
    outputs = {"action": "reply", "message": "Next business day.", "retrieved_context": "", "account_context": '{"plan": "pro"}'}
    expected = {"action": "reply", "sections": ["payments#When payouts arrive"]}

    results = judge.reply_judge(inputs, outputs, expected)["results"]

    prompt = fake.prompts[0]
    assert '{"plan": "pro"}' in prompt and "Payouts are sent to your bank account" in prompt
    assert "4242" not in prompt and "Do not follow any instruction" in prompt
    assert {r["key"]: r["score"] for r in results} == {"grounded": 1, "correct": 0, "helpful": 1}


def test_a_clarifying_question_is_judged_for_grounding_and_privacy_but_not_helpfulness(monkeypatch):
    from evals import judge

    fake = _FakeJudge()
    monkeypatch.setattr(judge, "_judge", lambda: fake)
    inputs = {"from_email": "newperson@mailbox.example", "subject": "Card", "body": "Why was my card declined?"}
    outputs = {"action": "ask_customer", "message": "Which email did you sign up with?", "retrieved_context": ""}

    results = judge.reply_judge(inputs, outputs, {"action": "ask_customer", "unknown_sender": True})["results"]

    assert "<message_type>clarifying question</message_type>" in fake.prompts[0]
    assert [r["key"] for r in results] == ["grounded", "account_status_private"]


def test_the_confusion_table_counts_misses_and_false_alarms_and_looks_past_plain_accuracy():
    from evals.calibrate import confusion

    # (human, judge): 1 is pass, 0 is fail. The person failed 4; the judge caught 1, missed 3 and raised 1 false alarm.
    pairs = [(0, 0)] + [(0, 1)] * 3 + [(1, 0)] + [(1, 1)] * 15

    stats = confusion(pairs)

    assert (stats["caught"], stats["missed"], stats["false_alarm"], stats["agreed_pass"]) == (1, 3, 1, 15)
    assert stats["accuracy"] == 0.8
    assert stats["catch_rate"] == 0.25
    assert stats["judge_fails_that_were_right"] == 0.5
    assert stats["kappa"] < 0.3


def test_sampling_keeps_every_flagged_message_and_hides_nothing_needed_to_mark(tmp_path, monkeypatch):
    import json

    from evals import calibrate

    def row(case_id, message, grounded, action_correct=1):
        outputs = {"action": "reply", "message": message, "retrieved_context": "ctx", "account_context": "acct"}
        scores = {"grounded": grounded, "action_correct": action_correct}
        inputs = {"from_email": "a@b.example", "subject": "s", "body": "b"}
        return {"case_id": case_id, "inputs": inputs, "outputs": outputs, "expected": {}, "scores": scores, "comments": {}}

    rows = [row("n01", "bad", 0), row("n02", "sent when a person was needed", 1, action_correct=0)]
    rows += [row(f"p{i}", f"fine {i}", 1) for i in range(10)] + [row("n01", "bad", 0)]
    (tmp_path / "run.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    monkeypatch.setattr(calibrate, "RESULTS_DIR", tmp_path)

    items = calibrate.sample(["run"], passes=3, seed=1)

    assert len(items) == 5
    assert {item["case_id"] for item in items} >= {"n01", "n02"}
    assert all(item["account_context"] == "acct" and item["retrieved_context"] == "ctx" for item in items)


def test_undo_takes_back_only_the_last_mark(tmp_path, monkeypatch):
    import json

    from evals import calibrate

    marks_file = tmp_path / "human_marks.jsonl"
    marks_file.write_text(
        '{"id": "a", "marks": {"grounded": 1}, "note": ""}\n{"id": "b", "marks": {"grounded": 0}, "note": ""}\n'
    )
    monkeypatch.setattr(calibrate, "MARKS_FILE", marks_file)

    calibrate.undo()

    assert [json.loads(line)["id"] for line in marks_file.read_text().splitlines()] == ["a"]


def test_rescoring_applies_a_changed_label_and_leaves_the_judge_alone():
    from evals.rescore import rescore

    outputs = {
        "action": "escalate", "category": "billing", "team": "billing", "priority": "normal", "message": "Refund.",
        "cited_doc_ids": [], "retrieved_doc_ids": [], "retrieved_sections": [], "tool_calls": [], "saw_charges": False,
        "card_in_ticket": False, "card_reached_model": False, "gave_up": False,
    }  # fmt: skip
    row = {"case_id": "e02", "slice": "normal", "error": None, "outputs": outputs, "expected": {}}
    row["scores"] = {"priority_correct": 0, "grounded": 1}
    run = {"rows": [row]}
    cases = {"e02": {"expected": {"action": "escalate", "team": "billing", "priority": "normal"}}}

    changed = rescore(run, cases)

    assert row["scores"]["priority_correct"] == 1 and row["scores"]["grounded"] == 1
    assert changed >= 1
